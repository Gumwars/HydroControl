#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
power_saver_probe.py -- what each power-saving lever is actually worth here.

Power Saver is a bundle of levers, and the only honest way to choose which go
in it -- and what they buy -- is to measure them on this machine, one at a
time, on battery. Guessing is how this project has been wrong before.

WHAT IT MEASURES

System draw from the battery itself: current_now x voltage_now, sampled once a
second. Everything the machine does shows up in it, which is the point --
RAPL's package watts only see the CPU, and the RTX was drawing ~29 W while the
CPU idled. Package watts and the RTX's runtime power state are logged alongside.

Levers are applied cumulatively, each followed by a settle period and a
measurement window, so every row is "everything above, plus this":

    baseline       as the machine is now
    audio          snd_hda_intel power_save=1 (codec sleeps when silent)
    wifi           wlan power save on
    pci-runtime    runtime PM 'auto' on every PCI device left at 'on'
    persistenced   nvidia-persistenced stopped -- can the RTX power down?
    cpu-limits     EC custom limits PL1 15 / PL2 25 / PL4 60 W
    brightness     a sweep at 100 %, 50 %, 20 % to price the brightness cap

Everything is put back afterwards, the EC limits by re-applying the saved
profile exactly as boot does (`hydroc.cli apply`).

The keyboard and chin bar are not measured: hydroc-server owns the keyboard,
and battery_lighting already covers them.

HOW TO RUN

On battery, idle, nothing playing, lid open. It takes an idle inhibitor for
its own duration so hypridle does not dim or lock the screen mid-window.

    sudo python3 power_saver_probe.py -o power-saver-idle.csv

Optionally a second run with a video playing (--label video), to price the
levers against something representative of "still usable".

Run it again after logging in with Hyprland restricted to the Intel GPU: the
baseline row then shows what taking the RTX out of the session is worth.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hydroc.hardware import Hardware  # noqa: E402
from hydroc.powersaver import (dgpu_holders as _dgpu_holders,  # noqa: E402
                               dgpu_nodes as _dgpu_nodes, find_dgpu, rtx_state)

BAT = "/sys/class/power_supply/BAT0"
SUPPLIES = "/sys/class/power_supply"
PCI = "/sys/bus/pci/devices"
AUDIO_PARAM = "/sys/module/snd_hda_intel/parameters/power_save"
INHIBITED = "HYDROC_PROBE_INHIBITED"

LOW_LIMITS = {"custom_profile": True, "cpu_pl1": 15, "cpu_pl2": 25,
              "cpu_pl4": 60}                 # PL4 even: stored at half scale
BRIGHTNESS_STEPS = (100, 50, 20)             # percent of max


def read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def write(path: str, value: str) -> bool:
    try:
        with open(path, "w") as fh:
            fh.write(value)
        return True
    except OSError as e:
        print(f"    ! could not write {path}: {e}")
        return False


def on_external_power() -> bool:
    """Mains or USB-C online. USB-C counts: AC0 reads offline while charging
    over USB-C, and a plugged-in measurement is meaningless."""
    for n in os.listdir(SUPPLIES):
        if read(os.path.join(SUPPLIES, n, "type")) in ("Mains", "USB") \
                and read(os.path.join(SUPPLIES, n, "online")) == "1":
            return True
    return False


def system_watts() -> float | None:
    i, v = read(f"{BAT}/current_now"), read(f"{BAT}/voltage_now")
    if not i or not v:
        return None
    return abs(int(i)) * int(v) / 1e12


DGPU = find_dgpu(PCI)


def dgpu_state() -> str:
    return rtx_state(DGPU) or "?"


def dgpu_nodes(dgpu: str | None = None) -> set[str]:
    return _dgpu_nodes(dgpu or DGPU)


def dgpu_holders() -> list[str]:
    """Processes with an RTX node open -- what could be keeping it awake."""
    return _dgpu_holders(DGPU)


def backlight() -> str | None:
    for d in sorted(glob.glob("/sys/class/backlight/*")):
        return d
    return None


# -- measuring -------------------------------------------------------------

def measure(hw: Hardware, settle: int, window: int) -> dict:
    time.sleep(settle)
    hw.package_watts()                       # prime the RAPL differentiator
    sys_w, pkg_w, gpu = [], [], []
    for _ in range(window):
        time.sleep(1)
        w = system_watts()
        if w is not None:
            sys_w.append(w)
        p = hw.package_watts()
        if p is not None:
            pkg_w.append(p)
        gpu.append(dgpu_state())
    if on_external_power():
        raise SystemExit("\nExternal power connected mid-run -- measurements "
                         "are void. Settings restored; run again on battery.")
    return {
        "system_w": round(statistics.mean(sys_w), 2) if sys_w else None,
        "system_sd": round(statistics.stdev(sys_w), 2) if len(sys_w) > 1 else None,
        "package_w": round(statistics.mean(pkg_w), 2) if pkg_w else None,
        "dgpu_suspended_pct": round(100 * gpu.count("suspended") / len(gpu)),
        "dgpu_state_end": gpu[-1],
    }


# -- levers: each returns an undo callable, or None if it changed nothing --

def lever_audio():
    old = read(AUDIO_PARAM)
    if old is None or old == "1":
        print(f"    audio power_save already {old}")
        return None
    write(AUDIO_PARAM, "1")
    return lambda: write(AUDIO_PARAM, old)


def lever_wifi():
    ifaces = [os.path.basename(os.path.dirname(p))
              for p in glob.glob("/sys/class/net/*/wireless")]
    undo = []
    for i in ifaces:
        r = subprocess.run(["iw", "dev", i, "get", "power_save"],
                           capture_output=True, text=True)
        if "on" in r.stdout:
            print(f"    {i} power save already on")
            continue
        if subprocess.run(["iw", "dev", i, "set", "power_save", "on"]).returncode == 0:
            undo.append(i)
    if not undo:
        return None
    return lambda: [subprocess.run(["iw", "dev", i, "set", "power_save", "off"])
                    for i in undo]


def lever_pci_runtime():
    changed = []
    for ctl in glob.glob("/sys/bus/pci/devices/*/power/control"):
        if read(ctl) == "on" and write(ctl, "auto"):
            changed.append(ctl)
    print(f"    {len(changed)} PCI devices moved from 'on' to 'auto'")
    if not changed:
        return None
    return lambda: [write(c, "on") for c in changed]


def lever_persistenced():
    active = subprocess.run(["systemctl", "is-active", "--quiet",
                             "nvidia-persistenced"]).returncode == 0
    if not active:
        print("    nvidia-persistenced not running")
        return None
    subprocess.run(["systemctl", "stop", "nvidia-persistenced"])
    for _ in range(30):
        if dgpu_state() == "suspended":
            break
        time.sleep(1)
    print(f"    RTX runtime state: {dgpu_state()}; holding it open: "
          f"{', '.join(dgpu_holders()) or 'nothing'}")
    return lambda: subprocess.run(["systemctl", "start", "nvidia-persistenced"])


def lever_cpu_limits(hw: Hardware):
    changes = hw.apply(dict(LOW_LIMITS))
    bad = [c for c in changes if not c.ok]
    for c in bad:
        print(f"    ! {c.setting}: {c.error}")
    # Restore = the saved profile, the same way boot does it.
    return lambda: subprocess.run([sys.executable, "-m", "hydroc.cli", "apply",
                                   "--reason", "power-saver-probe"], cwd=HERE)


# -- run --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--output", default=None, help="CSV to write")
    ap.add_argument("--label", default="idle",
                    help="what the machine was doing (idle, video, ...)")
    ap.add_argument("--settle", type=int, default=20,
                    help="seconds to wait after each lever (default 20)")
    ap.add_argument("--window", type=int, default=40,
                    help="seconds measured per row (default 40)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("run with sudo: levers write sysfs, systemd and the EC",
              file=sys.stderr)
        return 1
    if on_external_power():
        print("on external power -- unplug first (USB-C counts)", file=sys.stderr)
        return 1
    if not os.environ.get(INHIBITED):
        # hypridle dims to 1 % after a minute idle; that would land inside a
        # measurement window and look like a lever. Hold an idle lock.
        env = dict(os.environ, **{INHIBITED: "1"})
        os.execvpe("systemd-inhibit",
                   ["systemd-inhibit", "--what=idle:sleep",
                    "--who=power_saver_probe", "--why=measuring power draw",
                    sys.executable, *sys.argv], env)

    out = args.output or f"power-saver-{args.label}-{datetime.now():%Y%m%d-%H%M}.csv"
    hw = Hardware()
    bl = backlight()
    bl_max = int(read(f"{bl}/max_brightness") or 0) if bl else 0
    bl_old = read(f"{bl}/brightness") if bl else None

    est = (6 + len(BRIGHTNESS_STEPS)) * (args.settle + args.window)
    print(f"Measuring '{args.label}' -- about {est // 60} min. Leave it idle.\n"
          f"RTX now: {dgpu_state()}; holding it open: "
          f"{', '.join(dgpu_holders()) or 'nothing'}\n")

    rows: list[dict] = []
    undo: list = []

    def row(name: str) -> None:
        r = {"label": args.label, "step": name, **measure(hw, args.settle,
                                                            args.window)}
        prev = rows[-1]["system_w"] if rows else None
        base = rows[0]["system_w"] if rows else None
        d = (f"{r['system_w'] - prev:+6.2f} W vs previous, "
             f"{r['system_w'] - base:+6.2f} W vs baseline"
             if prev is not None and r["system_w"] is not None else "")
        print(f"  {name:<16} {r['system_w']:6.2f} W  (sd {r['system_sd']}, "
              f"pkg {r['package_w']} W, RTX suspended "
              f"{r['dgpu_suspended_pct']}%)  {d}")
        rows.append(r)

    try:
        row("baseline")
        for name, fn in (("audio", lever_audio), ("wifi", lever_wifi),
                         ("pci-runtime", lever_pci_runtime),
                         ("persistenced", lever_persistenced),
                         ("cpu-limits", lambda: lever_cpu_limits(hw))):
            print(f"  -> {name}")
            u = fn()
            if u:
                undo.append(u)
            row(name)
        if bl and bl_max:
            for pct in BRIGHTNESS_STEPS:
                write(f"{bl}/brightness", str(bl_max * pct // 100))
                row(f"brightness-{pct}")
    finally:
        print("\nRestoring ...")
        if bl and bl_old is not None:
            write(f"{bl}/brightness", bl_old)
        for u in reversed(undo):
            try:
                u()
            except Exception as e:           # restore the rest regardless
                print(f"  ! restore step failed: {e}")
        if rows:
            with open(out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0]))
                w.writeheader()
                w.writerows(rows)
            print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
