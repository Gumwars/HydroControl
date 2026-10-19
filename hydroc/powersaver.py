# SPDX-License-Identifier: MIT
"""
hydroc.powersaver — fewer watts on battery, without making the machine worse
at what it is for on battery: browsing, video, music.

    power_saver: "auto" | "on" | "off"      (profile key, default "auto")

"auto" engages on unplug and releases on plug-in; "on" and "off" pin it.
Driven by hydroc-server's power watcher, beside battery lighting.

WHAT IT DOES, AND WHAT EACH PART WAS MEASURED TO BE WORTH

power_saver_probe.py, on battery, 2026-10-19 (idle / video playing):

    audio codec power save   snd_hda_intel power_save 0 -> 1 -1.0 / -0.2 W *
    wi-fi power save         iw ... set power_save on        -1.0 / -0.7 W
    PCI runtime PM           power/control 'on' -> 'auto'    -2.6 / -2.0 W
    brightness cap           down to 50 %, never up          ~4 W from 100 %
    platform profile         power-profiles-daemon power-saver
    lighting                 battery_lighting forced to "off"

From 24.3 to 19.6 W idle, 37.3 to 34.4 W with video, before brightness and
lighting.

* Probably noise. CachyOS's own udev rule (cachyos-settings,
  20-audio-pm.rules) already sets power_save=10 on unplug, so the probe was
  moving 10 to 1, and with nothing playing the codec sleeps under either.
  The lever stays for systems that leave it at 0, and only ever moves 0.

WHAT IT DOES NOT DO, ON THE SAME EVIDENCE

  * CPU power limits. PL1 15 / PL2 25 never bound at these loads: +0.1 W,
    noise. Capping them costs responsiveness for nothing measured.
  * nvidia-persistenced. The RTX was in runtime D3 for 100 % of both runs with
    it running. Stopping it bought nothing.
  * Taking the RTX out of the session. Same evidence -- it already sleeps. It
    is watched instead: rtx_status() says when it is awake and what holds it,
    because an awake RTX is the one thing that costs more than every lever
    above together (~29 W seen at P0).
  * Refresh rate. hyprmoncfg owns it and would fight a change; status() says
    when the panel is above 60 Hz and leaves the choice to the user.

RESTORING

Every lever records what it found in /run/hydroc/power-saver.json before
changing it, and release gives each back -- but brightness and the platform
profile only if they still hold what we set. A change the user made on
battery is new intent, not ours to undo (the batterylight rule). /run is
cleared on reboot, and so is every lever's state, so the two cannot disagree.

Resume and Wi-Fi reconnects can quietly undo the low-level levers. While
engaged, reconcile() re-asserts audio, Wi-Fi and PCI -- never brightness or
the profile, which the user may have changed on purpose.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess

MODES = ("auto", "on", "off")
KEY = "power_saver"
DEFAULT = "auto"
STATE_PATH = "/run/hydroc/power-saver.json"

BRIGHTNESS_CAP_PCT = 50
PANEL_HZ = 60
PPD_SAVER = "power-saver"
HYPR_SOCKETS = "/run/user/*/hypr/*/.socket.sock"


def mode_of(profile: dict) -> str:
    m = profile.get(KEY, DEFAULT)
    return m if m in MODES else DEFAULT


def wanted(profile: dict, on_battery: bool | None) -> bool | None:
    """Should it be engaged? None when power is unreadable and mode is auto."""
    m = mode_of(profile)
    if m == "on":
        return True
    if m == "off":
        return False
    return on_battery


def lighting_profile(profile: dict, on_battery: bool | None) -> dict:
    """The profile battery lighting should see: lights off while engaged.

    Battery lighting only acts on battery, so "on" while plugged in leaves the
    lights alone -- the keyboard is not ours to darken on AC.
    """
    if wanted(profile, on_battery):
        return dict(profile, battery_lighting="off")
    return profile


# -- the RTX -----------------------------------------------------------------

def find_dgpu(pci: str = "/sys/bus/pci/devices") -> str | None:
    """The NVIDIA display controller's sysfs directory. Resolved, never
    hardcoded: bus numbers move with firmware and slot changes."""
    for dev in sorted(glob.glob(os.path.join(pci, "*"))):
        if _read(os.path.join(dev, "vendor")) == "0x10de" \
                and (_read(os.path.join(dev, "class")) or "").startswith("0x03"):
            return dev
    return None


def dgpu_nodes(dgpu: str | None) -> set[str]:
    """Device nodes that reach the RTX: the NVIDIA character devices, and the
    DRM card and render nodes the kernel attached to that PCI device."""
    nodes = set(glob.glob("/dev/nvidia*"))
    if dgpu:
        nodes |= {"/dev/dri/" + os.path.basename(p)
                  for p in glob.glob(os.path.join(dgpu, "drm", "*"))
                  if os.path.basename(p).startswith(("card", "renderD"))}
    return nodes


def dgpu_holders(dgpu: str | None) -> list[str]:
    """Processes with an RTX node open.

    Holding is not using: Xwayland and Firefox's decoder hold it while it
    sleeps. Only meaningful next to an awake state, and a /proc walk, so it is
    asked for on demand -- never from the watcher's two-second loop.
    """
    nodes, out = dgpu_nodes(dgpu), set()
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        pid = fd_dir.split("/")[2]
        try:
            links = {os.readlink(os.path.join(fd_dir, f))
                     for f in os.listdir(fd_dir)}
        except OSError:
            continue
        if links & nodes:
            out.add(f"{_read(f'/proc/{pid}/comm') or '?'} ({pid})")
    return sorted(out)


def rtx_state(dgpu: str | None) -> str | None:
    """'suspended' | 'active' | ... from runtime PM. Reading it never wakes
    the GPU -- unlike nvidia-smi, which is a client and would."""
    return _read(os.path.join(dgpu, "power", "runtime_status")) if dgpu else None


# -- the panel ---------------------------------------------------------------

def panel_refresh(pattern: str = HYPR_SOCKETS) -> float | None:
    """The internal panel's refresh rate as Hyprland runs it, or None."""
    for path in sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p)
                       if os.path.exists(p) else 0, reverse=True):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(path)
                s.sendall(b"j/monitors")
                chunks = []
                while True:
                    c = s.recv(65536)
                    if not c:
                        break
                    chunks.append(c)
            mons = json.loads(b"".join(chunks))
        except (OSError, ValueError):
            continue
        for m in mons:
            if str(m.get("name", "")).startswith("eDP"):
                try:
                    return round(float(m.get("refreshRate")), 2)
                except (TypeError, ValueError):
                    return None
    return None


# -- helpers -----------------------------------------------------------------

def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _write(path: str, value: str) -> str | None:
    """None on success, else the error."""
    try:
        with open(path, "w") as fh:
            fh.write(value)
        return None
    except OSError as e:
        return f"{path}: {e.strerror or e}"


class PowerSaver:
    """Engage, release and re-assert the levers. Idempotent.

    Paths and the command runner are injectable so the policy can be tested
    against a fake sysfs without root.
    """

    def __init__(self, sys_root: str = "/sys", state_path: str = STATE_PATH,
                 run=subprocess.run, which=shutil.which):
        self.sys = sys_root
        self.state_path = state_path
        self.run = run
        self.which = which

    # -- paths ---------------------------------------------------------------
    @property
    def audio_param(self) -> str:
        return os.path.join(self.sys, "module/snd_hda_intel/parameters/power_save")

    def wifi_ifaces(self) -> list[str]:
        return sorted(os.path.basename(os.path.dirname(p)) for p in
                      glob.glob(os.path.join(self.sys, "class/net/*/wireless")))

    def pci_controls(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.sys, "bus/pci/devices/*/power/control")))

    def backlight(self) -> str | None:
        found = sorted(glob.glob(os.path.join(self.sys, "class/backlight/*")))
        return found[0] if found else None

    # -- state ---------------------------------------------------------------
    def _load(self) -> dict:
        try:
            with open(self.state_path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict) -> None:
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh)
        os.replace(tmp, self.state_path)

    def _clear(self) -> None:
        try:
            os.unlink(self.state_path)
        except FileNotFoundError:
            pass

    def engaged(self) -> bool:
        return bool(self._load().get("engaged"))

    # -- commands ------------------------------------------------------------
    def _cmd(self, *args: str) -> tuple[int, str]:
        try:
            r = self.run(list(args), capture_output=True, text=True, timeout=10)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 1, str(e)

    def _wifi_ps(self, iface: str) -> str | None:
        rc, out = self._cmd("iw", "dev", iface, "get", "power_save")
        if rc != 0:
            return None
        return "on" if ": on" in out else "off" if ": off" in out else None

    def _ppd_get(self) -> str | None:
        if not self.which("powerprofilesctl"):
            return None
        rc, out = self._cmd("powerprofilesctl", "get")
        return out.strip() if rc == 0 and out.strip() else None

    # -- the levers ----------------------------------------------------------
    def _assert_low_level(self, st: dict, actions: list, errors: list,
                          record: bool) -> None:
        """Audio, Wi-Fi, PCI: set them. The audio value found is recorded
        only on first engage (`record`), so a re-assert after resume never
        mistakes our own reset for the user's setting. Wi-Fi interfaces and
        PCI devices are added to the list whenever we move them -- release
        must give back every one."""
        # Only from 0 (off). Any other value is a timeout someone chose, and
        # CachyOS's 20-audio-pm.rules sets 10 on unplug itself -- overwriting
        # it raced that rule at every unplug and fought it every minute.
        prev = _read(self.audio_param)
        if prev == "0":
            err = _write(self.audio_param, "1")
            if err:
                errors.append(err)
            else:
                actions.append("audio codec power save on")
                if record:
                    st["audio"] = prev

        if self.which("iw"):
            for iface in self.wifi_ifaces():
                if self._wifi_ps(iface) == "off":
                    rc, out = self._cmd("iw", "dev", iface, "set", "power_save", "on")
                    if rc != 0:
                        errors.append(f"{iface} power save: {out.strip()}")
                        continue
                    actions.append(f"{iface} power save on")
                    if iface not in st.setdefault("wifi", []):
                        st["wifi"].append(iface)

        moved = 0
        for ctl in self.pci_controls():
            if _read(ctl) == "on":
                err = _write(ctl, "auto")
                if err:
                    errors.append(err)
                    continue
                moved += 1
                if ctl not in st.setdefault("pci", []):
                    st["pci"].append(ctl)
        if moved:
            actions.append(f"runtime PM on {moved} PCI device(s)")

    def _engage(self, st: dict) -> dict:
        actions: list[str] = []
        errors: list[str] = []
        st = {"engaged": True}
        self._assert_low_level(st, actions, errors, record=True)

        bl = self.backlight()
        if bl:
            try:
                cur = int(_read(os.path.join(bl, "brightness")) or "")
                cap = int(_read(os.path.join(bl, "max_brightness")) or "") \
                    * BRIGHTNESS_CAP_PCT // 100
            except ValueError:
                cur = cap = None
            if cur is not None and cap is not None and cur > cap:
                err = _write(os.path.join(bl, "brightness"), str(cap))
                if err:
                    errors.append(err)
                else:
                    st["brightness"] = {"path": bl, "was": cur, "set": cap}
                    actions.append(f"brightness capped at {BRIGHTNESS_CAP_PCT}%")

        was = self._ppd_get()
        if was and was != PPD_SAVER:
            rc, out = self._cmd("powerprofilesctl", "set", PPD_SAVER)
            if rc == 0:
                st["ppd"] = was
                actions.append(f"power profile {was} -> {PPD_SAVER}")
            else:
                errors.append(f"powerprofilesctl: {out.strip()}")

        self._save(st)
        return {"changed": True, "active": True, "actions": actions, "errors": errors}

    def _release(self, st: dict) -> dict:
        actions: list[str] = []
        errors: list[str] = []

        # Back to 0 only if still ours: a rule that has since set its own
        # timeout (CachyOS sets 0 on AC itself) is left alone.
        if "audio" in st and _read(self.audio_param) == "1":
            err = _write(self.audio_param, st["audio"])
            (errors.append(err) if err else actions.append("audio codec power save restored"))

        for iface in st.get("wifi", []):
            rc, out = self._cmd("iw", "dev", iface, "set", "power_save", "off")
            (actions.append(f"{iface} power save off") if rc == 0
             else errors.append(f"{iface} power save: {out.strip()}"))

        n = 0
        for ctl in st.get("pci", []):
            if _read(ctl) == "auto":
                err = _write(ctl, "on")
                if err:
                    errors.append(err)
                else:
                    n += 1
        if n:
            actions.append(f"runtime PM restored on {n} PCI device(s)")

        b = st.get("brightness")
        if b:
            path = os.path.join(b["path"], "brightness")
            if _read(path) == str(b["set"]):
                err = _write(path, str(b["was"]))
                (errors.append(err) if err else actions.append("brightness restored"))
            else:
                actions.append("brightness left as you set it")

        if st.get("ppd"):
            if self._ppd_get() == PPD_SAVER:
                rc, out = self._cmd("powerprofilesctl", "set", st["ppd"])
                (actions.append(f"power profile -> {st['ppd']}") if rc == 0
                 else errors.append(f"powerprofilesctl: {out.strip()}"))
            else:
                actions.append("power profile left as you set it")

        self._clear()
        return {"changed": True, "active": False, "actions": actions, "errors": errors}

    def reconcile(self, profile: dict, on_battery: bool | None) -> dict:
        """Bring the levers in line with (profile, power)."""
        want = wanted(profile, on_battery)
        st = self._load()
        active = bool(st.get("engaged"))
        if want is None:
            return {"changed": False, "active": active,
                    "reason": "power state unreadable"}
        if want and not active:
            return self._engage(st)
        if active and not want:
            return self._release(st)
        if active:
            actions: list[str] = []
            errors: list[str] = []
            self._assert_low_level(st, actions, errors, record=False)
            if actions:
                self._save(st)
            return {"changed": bool(actions), "active": True,
                    "actions": actions, "errors": errors}
        return {"changed": False, "active": False}

    def status(self) -> dict:
        st = self._load()
        return {"active": bool(st.get("engaged")),
                "brightness_capped": bool(st.get("brightness")),
                "ppd_changed": st.get("ppd")}
