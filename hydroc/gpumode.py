# SPDX-License-Identifier: MIT
"""Firmware GPU mode — read, and optionally set.

One byte mirrored in two EFI variables, applied by the BIOS at POST
(DESIGN.md §7.7). No ACPI method exists, so a reboot is required however it is
set -- including from Windows, where Control Center writes these same variables.

    mode        UniWillVariable[0x62]   TpvSetup[0x01]
    igpu                         0x01             0x01
    dgpu                         0x02             0x02
    dynamic                      0x04             0x04

All three values were captured from the firmware's own writes. This module never
invents one, and refuses to interpret or overwrite anything else.

**This is the only write in the project without the power-cycle backstop.**
Everywhere else, settings live in volatile EC RAM and a full power cycle restores
factory defaults. EFI variables are non-volatile. Mitigating: there is no
checksum over UniWillVariable (its trailing byte is identical across all three
captured modes, so the one byte moves alone), and the BIOS menu can always set
the mode back. set_mode() therefore requires an explicit confirm= rather than
being reachable by an accidental call.
"""

from __future__ import annotations

import glob
import os
import subprocess

EFIVARS = "/sys/firmware/efi/efivars"

# name -> (byte offset into the DATA, expected data length)
VARS = {
    "UniWillVariable-9f33f85c-13ca-4fd1-9c4a-96217722c593": (0x62, 180),
    "TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b":        (0x01, 11),
}
MODES = {"igpu": 0x01, "dgpu": 0x02, "dynamic": 0x04}
MODE_NAMES = {v: k for k, v in MODES.items()}

LABELS = {
    "igpu":    "iGPU only",
    "dgpu":    "dGPU only",
    "dynamic": "Dynamic",
}
NOTES = {
    "igpu":    "Discrete GPU powered down. Longest battery life. "
               "External displays stop working — they are wired to the dGPU.",
    "dgpu":    "Panel driven by the RTX. The Intel GPU leaves the PCI bus "
               "entirely: no PRIME, no Intel VAAPI, and worse battery.",
    "dynamic": "Both GPUs. Panel on Intel, RTX renders on demand, external "
               "displays work. The default, and the best balance.",
}
# What each mode should look like once it has actually been applied.
EXPECT = {"igpu": (True, False), "dgpu": (False, True), "dynamic": (True, True)}

IGPU_PCI, DGPU_PCI = "0000:00:02.0", "0000:01:00.0"

# How to undo a mode change from outside Linux. This is shown with every write,
# not filed in the README, because the moment it is needed is the moment the
# machine will not boot far enough to read a README.
RECOVERY = ("If the desktop does not come back after the reboot: enter the BIOS "
            "setup at power-on and set the graphics mode back there. The BIOS "
            "writes the same two variables and does not need Linux to run.")

# Places a system can be pinned to one specific GPU. Anything found here was
# almost certainly written for the mode the machine is in NOW, and stops being
# true the moment that GPU leaves the PCI bus.
XORG_CONFIGS = ["/etc/X11/xorg.conf"]
XORG_GLOB = "/etc/X11/xorg.conf.d/*.conf"
MODPROBE_GLOB = "/etc/modprobe.d/*.conf"
ENV_FILES = ["/etc/environment"]
ENV_GLOBS = ["/etc/environment.d/*.conf", "/etc/profile.d/*.sh"]
PIN_VARS = ("GBM_BACKEND", "__GLX_VENDOR_LIBRARY_NAME", "WLR_DRM_DEVICES",
            "AQ_DRM_DEVICES", "DRI_PRIME", "VK_ICD_FILENAMES")


def _reads(path: str) -> str:
    """A diagnosis must never be the reason a write is refused by accident."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def xorg_pins_a_gpu() -> list[str]:
    """X configs naming a driver or a bus address.

    `BusID` is the dangerous one: EnvyControl writes BusID "PCI:1:0:0" into
    /etc/X11/xorg.conf, and that address has no device in iGPU-only mode.
    """
    hits = []
    for path in XORG_CONFIGS + sorted(glob.glob(XORG_GLOB)):
        low = _reads(path).lower()
        if 'driver "nvidia"' in low or "busid" in low:
            hits.append(path)
    return hits


def env_pins_a_gpu() -> list[tuple[str, str]]:
    """Environment files pinning the graphics stack to one vendor or card node."""
    hits = []
    for path in ENV_FILES + [f for g in ENV_GLOBS for f in sorted(glob.glob(g))]:
        for line in _reads(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            for var in PIN_VARS:
                if var not in stripped:
                    continue
                # Only a value naming nvidia or a specific card node pins
                # anything; a conditional fallback to mesa does not.
                if "nvidia" in stripped.lower() or "/dev/dri" in stripped:
                    hits.append((path, stripped))
                break
    return hits


def modeset_forced() -> list[str]:
    """nvidia-drm modeset forced on, where the module may have no device."""
    hits = []
    cmdline = _reads("/proc/cmdline")
    if "nvidia-drm.modeset=1" in cmdline or "nvidia_drm.modeset=1" in cmdline:
        hits.append("/proc/cmdline")
    for path in sorted(glob.glob(MODPROBE_GLOB)):
        low = _reads(path).lower()
        if "nvidia-drm" in low and "modeset=1" in low:
            hits.append(path)
    return hits


def preflight(mode: str) -> list[dict]:
    """What could leave this machine without a desktop after the reboot.

    Only iGPU-only is checked, because it is the only mode that removes the GPU
    the running system was configured against. Reported on 2026-09-10: a switch
    to iGPU-only decrypted the root filesystem and then never started a session,
    recoverable only from the BIOS.

    Advisory, never a veto -- these are heuristics over other people's config
    files, and a false positive must not be able to block a legitimate change.
    """
    if mode != "igpu":
        return []

    risks = []
    igpu, _dgpu = topology()
    if not igpu:
        risks.append({
            "name": "the integrated GPU is not on the bus",
            "detail": "this machine is in dGPU-only mode, so the Intel GPU is "
                      "powered down and invisible. Nothing here can confirm it "
                      "works before the reboot that depends on it",
            "remedy": "switch to Dynamic first and reboot. If the desktop comes "
                      "up on Intel there, iGPU-only is a much smaller step"})

    xorg = xorg_pins_a_gpu()
    if xorg:
        risks.append({
            "name": "an X config pins a specific GPU",
            "detail": ", ".join(xorg) + " names a driver or a PCI bus address. "
                      "EnvyControl writes BusID \"PCI:1:0:0\" here, and that "
                      "address has no device once the discrete GPU is gone",
            "remedy": "move it aside (sudo mv /etc/X11/xorg.conf "
                      "/etc/X11/xorg.conf.bak) before rebooting"})

    env = env_pins_a_gpu()
    if env:
        risks.append({
            "name": "the environment pins the graphics stack",
            "detail": "; ".join(f"{p}: {line}" for p, line in env[:3]),
            "remedy": "these name a vendor or a card node that will not exist "
                      "after the switch. Comment them out first"})

    forced = modeset_forced()
    if forced:
        risks.append({
            "name": "nvidia-drm modeset is forced on",
            "detail": ", ".join(forced) + " sets nvidia-drm modeset=1. The "
                      "module will have no device to bind to. Harmless by "
                      "itself, but it is part of the early-KMS path that draws "
                      "the boot splash and any disk passphrase prompt",
            "remedy": "no action needed if the rest of this list is clear; "
                      "worth removing if the screen stays black at boot"})
    return risks


class GpuModeError(RuntimeError):
    """Raised instead of writing something we cannot vouch for."""


def _path(name: str) -> str:
    return os.path.join(EFIVARS, name)


def available() -> bool:
    """False on any machine without these variables -- do not offer the UI."""
    return os.path.isdir(EFIVARS) and all(os.path.exists(_path(n)) for n in VARS)


def _read(name: str) -> tuple[int, bytes]:
    with open(_path(name), "rb") as fh:
        raw = fh.read()
    if len(raw) < 5:
        raise GpuModeError(f"{name}: only {len(raw)} bytes")
    return int.from_bytes(raw[:4], "little"), raw[4:]


def _write(name: str, attrs: int, data: bytes) -> None:
    """Attributes and data must reach the kernel in ONE write -- efivarfs
    rejects a partial one -- and the immutable flag has to come off first."""
    path = _path(name)
    subprocess.run(["chattr", "-i", path], capture_output=True)
    buf = attrs.to_bytes(4, "little") + data
    fd = os.open(path, os.O_WRONLY)
    try:
        n = os.write(fd, buf)
    finally:
        os.close(fd)
    if n != len(buf):
        raise GpuModeError(f"{name}: short write ({n}/{len(buf)})")


def _current() -> dict[str, tuple[int, int, bytes]]:
    out = {}
    for name, (off, size) in VARS.items():
        attrs, data = _read(name)
        if len(data) != size:
            raise GpuModeError(
                f"{name}: expected {size} data bytes, got {len(data)} -- refusing "
                "to touch a structure this build does not recognise")
        out[name] = (data[off], attrs, data)
    return out


def topology() -> tuple[bool, bool]:
    """(iGPU present, dGPU present) as the kernel sees it right now."""
    return (os.path.exists(f"/sys/bus/pci/devices/{IGPU_PCI}"),
            os.path.exists(f"/sys/bus/pci/devices/{DGPU_PCI}"))


def status() -> dict:
    """Never raises. The UI needs a reading even when the answer is 'unknown'."""
    if not available():
        return {"supported": False}
    try:
        cur = _current()
    except (OSError, GpuModeError) as e:
        return {"supported": True, "mode": None, "error": str(e)}

    vals = {b for b, _, _ in cur.values()}
    igpu, dgpu = topology()
    def _risks(k):
        try:
            return preflight(k)
        except Exception:                            # noqa: BLE001
            return []                                # never break the reading

    out = {"supported": True, "igpu_present": igpu, "dgpu_present": dgpu,
           "recovery": RECOVERY,
           "modes": [{"id": k, "label": LABELS[k], "note": NOTES[k],
                      "risks": _risks(k)}
                     for k in ("dynamic", "dgpu", "igpu")]}

    if len(vals) != 1:
        out["mode"] = None
        out["error"] = ("the two firmware variables disagree; set the mode once "
                        "in the BIOS to resynchronise")
        return out
    byte = vals.pop()
    if byte not in MODE_NAMES:
        out["mode"] = None
        out["error"] = (f"stored value 0x{byte:02X} is not one this build knows; "
                        "refusing to interpret it")
        return out

    mode = MODE_NAMES[byte]
    out["mode"] = mode
    out["label"] = LABELS[mode]
    out["note"] = NOTES[mode]
    # Stored mode versus what is actually on the bus. They differ exactly when a
    # change has been written but not yet applied, which is a normal state worth
    # naming rather than a fault.
    out["reboot_pending"] = (igpu, dgpu) != EXPECT[mode]
    return out


def set_mode(mode: str, confirm: bool = False,
             acknowledge_risks: bool = False) -> dict:
    """Write both variables. Takes effect at the next boot, not now.

    Two gates, for two different mistakes. `confirm` stops an accidental call
    reaching non-volatile firmware at all. `acknowledge_risks` stops a
    *deliberate* call going through when preflight() has found something that
    will plausibly leave this machine without a desktop -- because that failure
    appears one reboot later, with no way back except the BIOS.
    """
    if not confirm:
        raise GpuModeError("set_mode requires confirm=True: this writes "
                           "non-volatile firmware state and a power cycle will "
                           "not undo it")
    if mode not in MODES:
        raise GpuModeError(f"unknown mode {mode!r}")
    if not available():
        raise GpuModeError("this machine has no GPU mode variables")

    target = MODES[mode]
    cur = _current()
    vals = {b for b, _, _ in cur.values()}
    if len(vals) != 1:
        raise GpuModeError("the two firmware variables disagree; resynchronise "
                           "in the BIOS before writing")
    now = vals.pop()
    if now not in MODE_NAMES:
        raise GpuModeError(f"current value 0x{now:02X} is not a known mode; "
                           "refusing to overwrite a structure this build cannot "
                           "interpret")
    if now == target:
        return {"ok": True, "changed": False, "mode": mode,
                "message": f"already {LABELS[mode]}"}

    # Only now, with the firmware structure understood and a real change to
    # make. Ordering matters: "these variables disagree" is a more fundamental
    # refusal than "this might not boot", and must be reported as itself.
    risks = preflight(mode)
    if risks and not acknowledge_risks:
        raise GpuModeError(
            f"refusing to write {LABELS[mode]} without acknowledge_risks=True. "
            + " ".join(f"{r['name']}: {r['detail']}." for r in risks)
            + " " + RECOVERY)

    for name, (_, attrs, data) in cur.items():
        buf = bytearray(data)
        buf[VARS[name][0]] = target
        _write(name, attrs, bytes(buf))

    after = _current()
    bad = [n for n, (b, _, _) in after.items() if b != target]
    if bad:
        raise GpuModeError(
            "not every firmware variable took the new value ("
            + ", ".join(n.split("-")[0] for n in bad)
            + "). Set the mode in the BIOS to resynchronise.")

    return {"ok": True, "changed": True, "mode": mode, "was": MODE_NAMES[now],
            "message": f"{LABELS[mode]} will take effect after a reboot",
            "recovery": RECOVERY}
