# SPDX-License-Identifier: MIT
"""
hydroc.cli — inspect and apply persistent hardware state.

    python3 -m hydroc.cli status              what is available
    python3 -m hydroc.cli state               actual hardware state
    python3 -m hydroc.cli telemetry           live readings
    python3 -m hydroc.cli drift               hardware vs saved profile
    sudo python3 -m hydroc.cli apply          reconcile hardware to profile
    sudo python3 -m hydroc.cli apply --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .hardware import Hardware

PROFILE_PATHS = ["/etc/hydroc/profile.json",
                 os.path.expanduser("~/.config/hydroc/profile.json")]

DEFAULT_PROFILE = {
    "charge_profile": "stationary",
    "charge_threshold": 100,
    "custom_profile": True,
    "cpu_pl1": 75,
    "cpu_pl2": 75,
    "cpu_pl4": 124,          # even: PL4 is stored at half scale, odd watts round down
    "gpu_ctgp_offset": 0,
    "fn_lock": False,
    "super_key_enable": True,
    "touchpad_toggle_enable": True,
    "ac_auto_boot": False,
    "usb_powershare_high": False,
    # Chin bar. Volatile like the EC -- restored by `apply`, not by the bar.
    "chin_mode": "static",
    "chin_color": "#8CBF73",
    "chin_brightness": 100,
    "chin_speed": 5,
}


# Settings that were renamed. A profile written before the rename keeps the old
# key, and because nothing writes the old key back it becomes permanent phantom
# drift: the UI reports a difference no action can resolve.
LEGACY_KEYS = {"cpu_power_limit": "cpu_pl1"}


def migrate_profile(profile: dict) -> tuple[dict, bool]:
    """Fold renamed keys into their replacements. Returns (profile, changed)."""
    changed = False
    for old, new in LEGACY_KEYS.items():
        if old not in profile:
            continue
        if new not in profile and profile[old] is not None:
            profile[new] = profile[old]
        del profile[old]
        changed = True
    return profile, changed


def load_profile(path: str | None = None) -> tuple[dict, str]:
    for p in ([path] if path else PROFILE_PATHS):
        if p and os.path.exists(p):
            try:
                with open(p) as fh:
                    profile = json.load(fh)
            except (OSError, ValueError) as e:
                raise SystemExit(f"{p}: {e}")
            profile, changed = migrate_profile(profile)
            if changed:
                try:
                    save_profile(profile, p)     # heal the file in place
                except OSError:
                    pass                          # in-memory migration still applies
            return profile, p
    return dict(DEFAULT_PROFILE), "(built-in defaults)"


def save_profile(profile: dict, path: str | None = None) -> str:
    """Persist the profile. Returns the path written."""
    dest = path or PROFILE_PATHS[0]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as fh:
        json.dump(profile, fh, indent=2)
        fh.write("\n")
    return dest


# What a failed check means and what fixes it. `repair` names an action the app
# can actually perform; anything else the user has to do themselves.
#
# A system update breaking the daemon is the common case and it does not
# announce itself: the symptom is "hwmon is down", not "your kernel changed".
# Users should not have to know that re-running the installer is the fix, so
# these carry the remedy with them and the UI offers the button.
REPAIR_MODULE = "module"      # rebuild + reinstall uniwill-laptop
REPAIR_MODPROBE = "modprobe"  # the module exists, it just is not loaded
REPAIR_RELOAD   = "reload"    # the module is bound but the EC is not listening


def diagnose(hw) -> list[dict]:
    """Structured health checks, shared by `doctor` and /api/health.

    One diagnosis, two presentations -- so the UI can never disagree with the
    CLI about what is wrong or how to fix it.

    A system update breaking things does not announce itself: the symptom is
    "hwmon is down", not "your kernel changed". Users should not have to know
    that re-running the installer is the fix, so each check carries its own
    remedy and, where we can actually perform it, a `repair` action.
    """
    import glob
    import subprocess

    out: list[dict] = []

    am_root = os.geteuid() == 0

    def add(name, ok, detail, remedy=None, repair=None, optional=False,
            needs_root=False):
        # A check that cannot run without root is UNKNOWN when we are not root,
        # not FAILED. Reporting "keyboard RGB unavailable" to an unprivileged
        # caller is simply false -- the daemon runs as root and it works there.
        # Saying otherwise sends people chasing a package that is already
        # installed.
        unknown = bool(needs_root and not am_root and not ok)
        out.append({"name": name, "ok": bool(ok), "detail": detail,
                    "remedy": None if unknown else remedy,
                    "repair": None if unknown else repair,
                    "optional": optional, "unknown": unknown,
                    "needs_root": bool(needs_root)})

    # 1. right machine?
    board = _first_line("/sys/class/dmi/id/board_name")
    vendor = _first_line("/sys/class/dmi/id/sys_vendor")
    supported = vendor == "ELUKTRONICS" and board.startswith("HYDROC-16")
    add("supported model", supported,
        f"{vendor} / {board}" if supported else f"this is {vendor} / {board}",
        None if supported else
        "HydroControl is only validated on the Eluktronics HYDROC-16 G1. EC "
        "register layouts differ between chassis; do not run it here.")

    # 2. kernel module built for the RUNNING kernel
    running = os.uname().release
    kos = glob.glob(f"/lib/modules/{running}/**/uniwill-laptop.ko*", recursive=True)
    vermagics = set()
    for ko in kos:
        try:
            r = subprocess.run(["modinfo", ko], capture_output=True,
                               text=True, timeout=5).stdout
            for line in r.splitlines():
                if line.startswith("vermagic:"):
                    vermagics.add(line.split()[1])
        except Exception:
            pass
    bound = hw.driver_bound()
    dkms_managed = False
    try:
        r = subprocess.run(["dkms", "status", "-m", "uniwill-laptop"],
                           capture_output=True, text=True, timeout=5).stdout
        dkms_managed = running in r
    except Exception:
        pass

    # 1b. Has a kernel upgrade removed the running kernel's modules? Nothing
    #     is broken while they stay resident, but nothing can be reloaded
    #     either, so the reload Repair below must not be offered.
    from .kmod import reboot_pending, running_kernel
    _pending = reboot_pending()
    add("kernel module tree", not _pending,
        f"present for {running_kernel()}" if not _pending
        else f"MISSING for running kernel {running_kernel()}",
        None if not _pending else
        "The kernel package was upgraded since this kernel booted, so its "
        "modules are gone from disk. Nothing is broken yet -- the driver is "
        "still resident -- but it cannot be reloaded, and reload repairs are "
        "disabled, until you reboot.")

    if bound and dkms_managed:
        add("uniwill-laptop bound", True, "driver attached, DKMS-managed")
    elif bound and _pending:
        # DKMS reports per-kernel, so while a reboot is pending it lists the
        # kernel we will boot next and not the one running. Reading that as
        # "not DKMS-managed" recommends a rebuild that is neither needed nor
        # possible -- confident wrong advice, the §4.3 failure mode.
        add("uniwill-laptop bound", True,
            "driver attached; DKMS has built for a newer kernel than the running one",
            "Not a fault. The kernel was upgraded since boot; reboot to load the "
            "module DKMS has already built. No rebuild is needed.")
    elif bound:
        add("uniwill-laptop bound", True,
            "driver attached, but NOT DKMS-managed",
            "It will break on the next kernel update. Repair registers it with "
            "DKMS so future updates rebuild it automatically.",
            repair=REPAIR_MODULE)
    elif vermagics and running not in vermagics:
        add("uniwill-laptop bound", False,
            f"module was built for {sorted(vermagics)[0]}, running {running}",
            "The kernel changed and the module was not rebuilt for it. This is "
            "the usual cause of everything going quiet after a system update.",
            repair=REPAIR_MODULE)
    else:
        add("uniwill-laptop bound", False, "not loaded",
            "The driver is not attached, so fans, temperatures, battery modes "
            "and platform toggles are all unavailable.",
            repair=REPAIR_MODULE)

    # 2b. Is the EC accepting host control at all?
    #     This sits above everything else: with 0x0741 bit 0 clear, the
    #     custom-profile latch still reads armed and power-limit writes are
    #     still accepted, but the EC ignores the lot. Nothing else in this list
    #     shows it, which is exactly why it went undiagnosed for a whole
    #     session.
    if bound and os.geteuid() == 0:
        try:
            from .ec import EC
            live = EC().manual_control_enabled()
            add("EC accepting host control", live,
                "0x0741 bit 0 set" if live else "0x0741 bit 0 CLEAR",
                None if live else
                "The EC is ignoring everything this app writes -- power limits "
                "will be accepted and silently discarded. uniwill-laptop sets "
                "this bit when it loads and clears it when it unloads, so a "
                "module reload restores it.",
                repair=None if (live or _pending) else REPAIR_RELOAD,
                needs_root=True)
        except Exception:
            pass

    # 3. acpi_call -- "missing" has two very different causes and only one of
    #    them is something this application can fix.
    acpi = os.path.exists("/proc/acpi/call")
    if acpi:
        add("acpi_call loaded", True, "present")
    else:
        installed = False
        try:
            installed = subprocess.run(["modinfo", "acpi_call"],
                                       capture_output=True,
                                       timeout=5).returncode == 0
        except Exception:
            pass
        if installed:
            add("acpi_call loaded", False, "installed but not loaded",
                "The module is present, it just is not loaded yet.",
                repair=REPAIR_MODPROBE)
        else:
            add("acpi_call loaded", False, "not installed",
                "This is how the daemon reaches the EC, and it has to come from "
                "your distribution. On Arch/CachyOS:  paru -S acpi_call-dkms")

    # 4. root
    root = os.geteuid() == 0
    add("running as root", root, "yes" if root else "no",
        None if root else
        "EC reads/writes and keyboard RGB need root. Start the daemon with sudo, "
        "or launch the desktop app which asks for authentication.")

    # 5. hwmon
    from .hardware import find_hwmon
    h = find_hwmon()
    add("hwmon sensors", h is not None, h or "absent",
        None if h else "Follows from the driver not being bound -- fix that first.")

    # 6. battery attributes
    ct = os.path.exists("/sys/class/power_supply/BAT0/charge_types")
    add("battery charge modes", ct, "available" if ct else "absent",
        None if ct else "Follows from the driver not being bound.")

    # 7. RGB transports
    try:
        from . import rgb
        kb_ok, kb_why = rgb.keyboard_available()
        cb_ok, cb_why = rgb.chinbar_available()
    except Exception as e:
        kb_ok, kb_why, cb_ok, cb_why = False, str(e), False, str(e)
    add("keyboard RGB (ITE 8291)", kb_ok,
        kb_why or "connected", None if kb_ok else
        "Keyboard lighting only. If this names a version, ite8291r3-ctl changed "
        "its API -- 0.3 and 0.4 are supported.", needs_root=True)
    add("chin bar (ITE 8233)", cb_ok, cb_why or "found",
        None if cb_ok else "Chin bar lighting only.", needs_root=True)

    # 7b. bleak, asked the way the sidecar asks it. Importing it here would
    #     answer whether *this* process can load it, which is a different
    #     question with a different answer (DESIGN.md §4.4).
    from .deps import check as _dep_check
    _bleak = _dep_check("bleak")
    add("bleak (LPP cooling dock)", _bleak["ok"] is True, _bleak["detail"],
        _bleak["remedy"], optional=True, needs_root=True)

    # 8. LPP dock -- optional hardware, so absence is never a failure.
    try:
        from .server import lpp_call
        lppd = lpp_call({"op": "status"}, timeout=3.0)
    except Exception as e:
        lppd = {"running": False, "error": str(e)}
    if lppd.get("present"):
        add("LPP cooling dock", True, f"connected at {lppd.get('address')}",
            optional=True)
    elif lppd.get("running"):
        add("LPP cooling dock", True,
            f"daemon up, dock not connected -- "
            f"{lppd.get('last_error') or 'no dock in range'}", optional=True)
    else:
        add("LPP cooling dock", True, "hydroc-lpp not running", optional=True)

    return out


def doctor(hw) -> int:
    """Preflight: check every prerequisite and say what to do about each."""
    checks = diagnose(hw)
    width = max(len(c["name"]) for c in checks)
    bad = skipped = 0
    for c in checks:
        if c.get("unknown"):
            skipped += 1
            mark, detail = "SKIP", "needs root to check -- run with sudo"
        elif c["ok"]:
            mark, detail = "PASS", c["detail"]
        else:
            bad += 1
            mark, detail = "FAIL", c["detail"]
        print(f"  [{mark}] {c['name']:<{width}}  {detail}")
        if c.get("remedy"):
            print(f"         {c['remedy']}")

    print()
    if skipped:
        print(f"  {skipped} check(s) skipped -- they need root. "
              "Re-run with sudo for the full picture.")
    if bad == 0:
        print("  All checks passed. Start the UI with:")
        print("    hydrocontrol        (or: sudo python3 -m hydroc.server)")
    else:
        print(f"  {bad} check(s) failed -- see the note beside each.")
        if any(c.get("repair") for c in checks if not c["ok"]):
            print("  Most of these are fixed by re-running:  sudo ./install.sh")
    return 0 if bad == 0 else 1


def _first_line(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def fmt(d: dict) -> str:
    width = max((len(k) for k in d), default=0)
    return "\n".join(f"  {k:<{width}}  {v}" for k, v in d.items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hydroc")
    ap.add_argument("command",
                    choices=["status", "state", "telemetry", "drift",
                             "apply", "write-default-profile", "doctor"])
    ap.add_argument("-p", "--profile", help="profile JSON path")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    hw = Hardware()

    if args.command == "doctor":
        return doctor(hw)

    if args.command == "status":
        st = hw.status()
        print(json.dumps(st, indent=2) if args.json else fmt(st))
        if not st["driver_loaded"]:
            print("\n  uniwill-laptop is not bound. Fans, temps, battery modes\n"
                  "  and toggles are all unavailable until it loads.",
                  file=sys.stderr)
        if not st["ec_available"]:
            print(f"\n  EC unavailable: {st['ec_error']}\n"
                  "  CPU power limits need root and acpi_call.", file=sys.stderr)
        return 0

    if args.command == "telemetry":
        t = hw.telemetry()
        print(json.dumps(t, indent=2) if args.json else fmt(t))
        return 0

    if args.command == "state":
        s = hw.read_state()
        print(json.dumps(s, indent=2) if args.json else fmt(s))
        return 0

    profile, src = load_profile(args.profile)

    if args.command == "write-default-profile":
        dest = args.profile or PROFILE_PATHS[0]
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as fh:
            json.dump(DEFAULT_PROFILE, fh, indent=2)
            fh.write("\n")
        print(f"wrote {dest}")
        return 0

    if args.command == "drift":
        d = hw.drift(profile)
        if args.json:
            print(json.dumps(d, indent=2))
        elif not d:
            print(f"in sync with {src}")
        else:
            print(f"drifted from {src}:")
            for k, v in d.items():
                print(f"  {k}: want {v['desired']!r}, hardware has {v['actual']!r}")
        return 1 if d else 0

    # apply
    if os.geteuid() != 0 and not args.dry_run:
        raise SystemExit("apply needs root (or use --dry-run)")

    changes = hw.apply(profile, dry_run=args.dry_run)
    if args.json:
        print(json.dumps([c.__dict__ for c in changes], indent=2))
    elif not changes:
        print(f"nothing to do -- hardware already matches {src}")
    else:
        print(f"{'would apply' if args.dry_run else 'applied'} from {src}:")
        for c in changes:
            print(f"  {c}")

    # The chin bar is not an EC setting, so Hardware.apply() cannot reach it --
    # it is hidraw, via kbctrl. Without this it comes up dark after every power
    # cycle and nothing ever restores it.
    chin_ok = True
    if not args.dry_run:
        from . import rgb
        res = rgb.apply_chinbar_profile(profile)
        if res.get("skipped"):
            print(f"  chin bar: skipped -- {res.get('error')}")
        elif res.get("ok"):
            print(f"  chin bar: {profile.get('chin_mode', 'static')}")
        else:
            chin_ok = False
            print(f"  chin bar: FAILED -- {res.get('error')}", file=sys.stderr)

    return 0 if (all(c.ok for c in changes) and chin_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
