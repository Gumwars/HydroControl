#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Does a charging profile change the *termination voltage*?

We already know the profiles do not cap by percentage: a full cycle under
`Stationary` charged to a true 100%, `CHARGE_CTRL_REACHED` never armed, and
`charge_full` still equals `charge_full_design` -- so the documented behaviour
(cap at 80, report 100) is not merely ineffective, its mechanism is absent.

That is not the whole question. Percentage caps are one way to do battery care;
lowering the charge termination voltage is the other, and it is invisible to
every measurement above. If `Stationary` stopped at 4.10 V/cell instead of 4.23,
the gauge would rescale, report 100%, and look exactly like what we recorded --
while genuinely extending pack life. Before anyone says these profiles do
nothing, that has to be ruled out.

Our existing logs cannot rule it out. Across cycle.csv and battery-v.csv there
are 68 samples at >=99% capacity and *none* with current near zero. Under charge,
`voltage_now` is the charger's applied voltage, not the cell's (HANDOFF,
"Traps"), so every reading we have is unusable for this.

WHAT THIS DOES DIFFERENTLY -- no discharge between profiles.

Testing three profiles the obvious way needs three full cycles and three
discharges. It does not have to. Charge to full under the *lowest* profile, let
the current fall to zero, and record the resting voltage. Then raise the
profile. If the lower profile really did terminate lower, the charger has no
choice but to resume -- current rises, voltage climbs -- and that resume is
itself the answer, visible in minutes instead of days.

    no resume at either step, all three rest at the same voltage
        -> the profiles do not affect termination. They are inert.
    resume after raising the profile
        -> the lower profile does cap, by voltage rather than percentage,
           and "does nothing" is the wrong description.

Writes one EC register (the charging profile at 0x07A6, bits 5:4), which is
volatile EC state a power cycle undoes, and restores the profile it found on
the way out.

    sudo python3 charge_profile_probe.py -o charge-profiles.csv
    sudo python3 charge_profile_probe.py --status        # one sample, no writes
    sudo python3 charge_profile_probe.py --dry-run       # print the plan
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

# 0x07A6 is shared: bits 5:4 are the charging profile, bit 1 is
# OVERBOOST_DYN_TEMP_OFF and bit 6 is TOUCHPAD_TOGGLE_OFF. Mask, never assign.
REG_OEM_4 = 0x07A6
PROFILE_MASK = 0x30
PROFILE_SHIFT = 4
REG_CHARGE_CTRL = 0x07B9        # recorded only, never written here

# 0x07C6 is sampled read-only, and it may matter more than 0x07B9 does.
# It is the same byte that holds ENABLE_UNIVERSAL_FAN_CTRL -- the switch that
# turned a feature we had written off as absent into a working one -- and two
# bits away it carries a battery status bit and a "full for 24 hours" flag that
# nothing in the driver reads or writes. Together with the WMI event
# UNIWILL_OSD_BAT_ERM_UPDATE (0xBF), which the keymap explicitly ignores, that
# is the shape of a battery-care subsystem which is NOT the 0x07B9 threshold.
# If the real mechanism lives here, a full charge cycle is when it would show.
# The whole byte is logged, not just the two known bits: reading the
# neighbourhood is what caught 0x0984 being unmapped rather than confirming.
REG_AP_OEM_6 = 0x07C6
BATTERY_CHARGE_FULL_OVER_24H = 1 << 3
BATTERY_ERM_STATUS_REACHED = 1 << 4

# Encodings straight out of uniwill-acpi.c. Ordered low-to-high on purpose:
# the whole design depends on raising the profile, never lowering it.
PROFILES = [
    ("stationary", 0x02, "Stationary -- the one documented as an 80% cap"),
    ("balanced", 0x01, "Balanced"),
    ("high_capacity", 0x00, "High Capacity -- the unrestricted reference"),
]
BY_NAME = {n: v for n, v, _ in PROFILES}
BY_VALUE = {v: n for n, v, _ in PROFILES}

BAT = "/sys/class/power_supply/BAT0"
AC = "/sys/class/power_supply/AC0/online"

# 6 ms between EC accesses. DESIGN.md 4.2: sustained EC traffic is the hazard,
# and this script runs for hours unattended.
EC_DELAY = 0.006
_last_call = 0.0


def _call(expr: str) -> str:
    global _last_call
    gap = time.monotonic() - _last_call
    if gap < EC_DELAY:
        time.sleep(EC_DELAY - gap)
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        raw = fh.read().strip().rstrip("\x00")
    _last_call = time.monotonic()
    return raw


def ec_read(addr: int):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def ec_write(addr: int, val: int) -> None:
    _call(f"{ECRW} 0x{addr:X} 0x{val & 0xFF:X}")


def read_profile():
    v = ec_read(REG_OEM_4)
    return None if v is None else (v & PROFILE_MASK) >> PROFILE_SHIFT


def set_profile(value: int) -> bool:
    """Read-modify-write; returns whether it read back as asked.

    Read-modify-write is not politeness. 0x07A6 also carries
    OVERBOOST_DYN_TEMP_OFF (bit 1) and TOUCHPAD_TOGGLE_OFF (bit 6), so a blind
    byte write here disables the touchpad on a machine nobody is sitting at.
    """
    cur = ec_read(REG_OEM_4)
    if cur is None:
        return False
    ec_write(REG_OEM_4, (cur & ~PROFILE_MASK) | (value << PROFILE_SHIFT))
    time.sleep(0.2)
    return read_profile() == value


def sysfs_int(name: str, base=BAT):
    """None means unreadable, which is NOT the same as zero."""
    try:
        with open(os.path.join(base, name)) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def sysfs_str(name: str):
    try:
        with open(os.path.join(BAT, name)) as fh:
            return fh.read().strip()
    except OSError:
        return None


def on_ac() -> bool:
    try:
        with open(AC) as fh:
            return fh.read().strip() == "1"
    except OSError:
        return False


def cell_count() -> int:
    """4S here, but derive it rather than assume -- this file is meant to
    survive being pointed at a sibling chassis."""
    vmin = sysfs_int("voltage_min_design")
    if not vmin:
        return 4
    return max(1, round(vmin / 1e6 / 3.7))


def sample(cells: int) -> dict:
    raw = ec_read(REG_CHARGE_CTRL)
    oem6 = ec_read(REG_AP_OEM_6)
    v_uv = sysfs_int("voltage_now")
    i_ua = sysfs_int("current_now")
    return {
        "t": datetime.now().isoformat(timespec="seconds"),
        "ac": int(on_ac()),
        "status": sysfs_str("status"),
        "capacity": sysfs_int("capacity"),
        "charge_now": sysfs_int("charge_now"),
        "charge_full": sysfs_int("charge_full"),
        "current_ma": None if i_ua is None else round(i_ua / 1000),
        "voltage_uv": v_uv,
        "v_per_cell": None if v_uv is None else round(v_uv / 1e6 / cells, 4),
        "profile": read_profile(),
        "threshold": None if raw is None else raw & 0x7F,
        "reached": None if raw is None else int(bool(raw & 0x80)),
        "oem6": None if oem6 is None else f"0x{oem6:02X}",
        "erm_reached": None if oem6 is None else int(bool(oem6 & BATTERY_ERM_STATUS_REACHED)),
        "full_24h": None if oem6 is None else int(bool(oem6 & BATTERY_CHARGE_FULL_OVER_24H)),
    }


class Log:
    COLS = ["t", "phase", "ac", "status", "capacity", "charge_now", "charge_full",
            "current_ma", "voltage_uv", "v_per_cell", "profile", "threshold",
            "reached", "oem6", "erm_reached", "full_24h"]

    def __init__(self, path):
        self.oem6_seen: dict[str, int] = {}
        self.erm_events: list[dict] = []
        self.full24_events: list[dict] = []
        fresh = not path or not os.path.exists(path) or os.path.getsize(path) == 0
        self.fh = open(path, "a") if path else None
        if self.fh and fresh:
            self.fh.write(",".join(self.COLS) + "\n")
            self.fh.flush()

    def write(self, s: dict, phase: str) -> None:
        self.note(s, phase)
        if not self.fh:
            return
        row = dict(s, phase=phase)
        self.fh.write(",".join("" if row.get(c) is None else str(row[c])
                               for c in self.COLS) + "\n")
        self.fh.flush()          # hours unattended; never buffer the evidence


    def note(self, s: dict, phase: str) -> None:
        """Record what 0x07C6 did, whether or not a CSV is being written.

        Kept as first-occurrence events rather than a count: the question is
        whether these bits EVER arm, and one armed sample is the finding.
        """
        raw = s.get("oem6")
        if raw is None:
            return
        self.oem6_seen[raw] = self.oem6_seen.get(raw, 0) + 1
        for bit, store in (("erm_reached", self.erm_events),
                           ("full_24h", self.full24_events)):
            if s.get(bit) and not store:
                store.append({"t": s["t"], "phase": phase, "oem6": raw,
                              "capacity": s.get("capacity")})


def show(s: dict, phase: str) -> None:
    print(f"{s['t'][11:]}  {phase:<14} {str(s['status'] or '?'):<12} "
          f"cap={str(s['capacity']):>3}%  {str(s['current_ma']):>6} mA  "
          f"{s['v_per_cell']} V/cell  prof={BY_VALUE.get(s['profile'], s['profile'])}"
          f"  oem6={s.get('oem6') or '--'}"
          f"{'  << ERM ARMED' if s.get('erm_reached') else ''}"
          f"{'  << FULL 24H' if s.get('full_24h') else ''}",
          flush=True)


def wait_for_ac(log, cells, args) -> None:
    if on_ac():
        return
    print("on battery -- waiting for the charger. Plug in whenever you like.\n",
          flush=True)
    while not on_ac():
        s = sample(cells)
        log.write(s, "wait_ac")
        time.sleep(args.idle_interval)


def settle(log, cells, args, phase: str) -> dict | None:
    """Wait until the pack is full and the current has genuinely stopped.

    'Genuinely' is the whole point. A momentary dip below the threshold is not
    termination -- the charger tapers for a long time near the top -- so this
    demands a sustained quiet period before it believes the reading, and it
    reports the median of that window rather than one sample.
    """
    quiet: list[dict] = []
    quiet_since = None
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        s = sample(cells)
        log.write(s, phase)

        if not s["ac"]:
            print("  ! charger removed -- this reading is void. Plug back in.",
                  flush=True)
            quiet, quiet_since = [], None
            time.sleep(args.idle_interval)
            continue

        cur = s["current_ma"]
        near_full = (s["capacity"] or 0) >= args.full_pct
        is_quiet = cur is not None and abs(cur) <= args.settle_ma and near_full

        if is_quiet:
            quiet.append(s)
            quiet_since = quiet_since or time.monotonic()
            held = time.monotonic() - quiet_since
            if len(quiet) >= args.settle_samples and held >= args.settle_seconds:
                vs = [q["v_per_cell"] for q in quiet if q["v_per_cell"] is not None]
                out = dict(quiet[-1])
                out["v_per_cell"] = round(statistics.median(vs), 4) if vs else None
                out["settled_samples"] = len(quiet)
                out["settled_seconds"] = round(held)
                print(f"  settled: {out['v_per_cell']} V/cell over "
                      f"{len(quiet)} samples / {round(held)}s at "
                      f"{out['capacity']}%\n", flush=True)
                return out
        else:
            if quiet:
                print("  (current came back -- still tapering)", flush=True)
            quiet, quiet_since = [], None

        show(s, phase)
        time.sleep(args.interval if near_full else args.idle_interval)

    print(f"  ! gave up waiting after {args.timeout/3600:.1f} h", flush=True)
    return None


def watch_resume(log, cells, args, phase: str) -> bool:
    """After raising the profile: does the charger start again?

    This is the measurement. If the previous profile terminated lower, the pack
    is now below the new profile's target and charging MUST restart. Silence
    here is not a null result -- it is the answer.
    """
    deadline = time.monotonic() + args.resume_window
    while time.monotonic() < deadline:
        s = sample(cells)
        log.write(s, phase)
        show(s, phase)
        cur = s["current_ma"]
        if cur is not None and cur > args.resume_ma:
            print(f"  -> RESUMED at {cur} mA. The previous profile was "
                  f"terminating lower.\n", flush=True)
            return True
        time.sleep(args.interval)
    print(f"  -> no resume in {args.resume_window/60:.0f} min "
          f"(above {args.resume_ma} mA).\n", flush=True)
    return False


def verdict(results: list[dict], args) -> dict:
    """Turn the readings into a claim, and say when there is not one to make."""
    good = [r for r in results if r.get("settled") and r["settled"].get("v_per_cell")]
    if len(good) < 2:
        return {"conclusion": "inconclusive",
                "detail": "fewer than two profiles produced a settled reading"}

    vs = {r["profile"]: r["settled"]["v_per_cell"] for r in good}
    spread_mv = round((max(vs.values()) - min(vs.values())) * 1000, 1)
    resumed = [r["profile"] for r in results if r.get("resumed")]

    if resumed:
        return {"conclusion": "profiles affect termination voltage",
                "detail": f"charging resumed after raising to {', '.join(resumed)}; "
                          f"resting spread {spread_mv} mV/cell",
                "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": resumed}
    if spread_mv <= args.same_mv:
        return {"conclusion": "profiles are inert",
                "detail": f"no profile change restarted the charger, and all "
                          f"settled within {spread_mv} mV/cell "
                          f"(noise floor {args.same_mv} mV)",
                "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": [],
                # Stated as a caveat rather than buried, because it is the one
                # way this null result could be wrong: an EC that latches the
                # profile at plug-in would ignore a mid-charge change and look
                # exactly like an EC that ignores the profile entirely.
                "caveat": "assumes the EC re-evaluates the profile while on AC. "
                          "To rule out a plug-in-latched profile, rerun with "
                          "--order high_capacity,stationary and unplug/replug "
                          "after the switch."}
    return {"conclusion": "unclear",
            "detail": f"nothing resumed, but resting voltages differ by "
                      f"{spread_mv} mV/cell -- more than the {args.same_mv} mV "
                      f"noise floor. Repeat before trusting it.",
            "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": []}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Do charging profiles change the termination voltage?")
    ap.add_argument("-o", "--output", default="charge-profiles.csv")
    ap.add_argument("--json", default=None,
                    help="summary path (default: alongside --output)")
    ap.add_argument("--order", default="stationary,balanced,high_capacity",
                    help="profiles low-to-high; raising is what forces a resume")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--idle-interval", type=float, default=60.0)
    ap.add_argument("--full-pct", type=int, default=99,
                    help="capacity that counts as full")
    ap.add_argument("--settle-ma", type=int, default=60,
                    help="|current| at or below this counts as terminated")
    ap.add_argument("--settle-samples", type=int, default=12)
    ap.add_argument("--settle-seconds", type=float, default=300,
                    help="how long the current must stay down before believing it")
    ap.add_argument("--resume-ma", type=int, default=120,
                    help="current above this after a profile raise counts as a resume")
    ap.add_argument("--resume-window", type=float, default=900)
    ap.add_argument("--timeout", type=float, default=6 * 3600,
                    help="give up waiting for one settle after this long")
    ap.add_argument("--same-mv", type=float, default=15,
                    help="mV/cell within which two resting voltages are the same")
    ap.add_argument("--status", action="store_true", help="one sample, no writes")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = ap.parse_args()

    if not args.dry_run:
        if os.geteuid() != 0:
            raise SystemExit("must run as root")
        if not os.path.exists(CALL):
            raise SystemExit("run: sudo modprobe acpi_call")

    cells = cell_count()
    order = [p.strip() for p in args.order.split(",") if p.strip()]
    for p in order:
        if p not in BY_NAME:
            raise SystemExit(f"unknown profile {p!r}; choose from {list(BY_NAME)}")

    if args.status:
        s = sample(cells)
        print(json.dumps(dict(s, cells=cells), indent=2))
        return 0

    if args.dry_run:
        print(f"{cells}S pack. Plan:")
        print(f"  1. wait for AC (currently {'on' if on_ac() else 'off'})")
        for i, p in enumerate(order, 1):
            print(f"  {i+1}. set {p}, charge to >={args.full_pct}%, wait for "
                  f"<={args.settle_ma} mA held {args.settle_seconds:.0f}s, "
                  f"record resting V/cell"
                  + ("" if i == 1 else " (watching first for a resume)"))
        print(f"  {len(order)+2}. restore the original profile and summarise")
        return 0

    original = read_profile()
    if original is None:
        raise SystemExit("cannot read the profile register -- refusing to write blind")
    print(f"{cells}S pack. Starting profile: "
          f"{BY_VALUE.get(original, original)}. It will be restored on exit.\n")

    log = Log(args.output)
    results: list[dict] = []

    try:
        wait_for_ac(log, cells, args)

        for i, name in enumerate(order):
            print(f"=== {name} ===", flush=True)
            if not set_profile(BY_NAME[name]):
                print(f"  ! could not set {name} -- skipping\n", flush=True)
                results.append({"profile": name, "set": False})
                continue

            entry = {"profile": name, "set": True, "resumed": None}
            # The first profile has nothing to resume from; every later one is
            # a raise, and that is the actual experiment.
            if i > 0:
                entry["resumed"] = watch_resume(log, cells, args, f"raise:{name}")
            entry["settled"] = settle(log, cells, args, name)
            results.append(entry)

    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        if set_profile(original):
            print(f"restored profile {BY_VALUE.get(original, original)}")
        else:
            print(f"!! could not restore profile {original} -- set it in the app, "
                  f"or power cycle (this register is volatile)")

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "cells": cells,
        "charge_full": sysfs_int("charge_full"),
        "charge_full_design": sysfs_int("charge_full_design"),
        "order": order,
        "results": results,
        # Reported independently of the voltage verdict. This is a separate
        # question that happens to share a charge cycle, and it stands on its
        # own whichever way the profiles turn out.
        "oem6": {
            "register": "0x07C6",
            "values_seen": log.oem6_seen,
            "erm_first_armed": log.erm_events[0] if log.erm_events else None,
            "full_24h_first_set": log.full24_events[0] if log.full24_events else None,
        },
        "verdict": verdict(results, args),
    }
    path = args.json or (os.path.splitext(args.output)[0] + ".json")
    with open(path, "w") as fh:
        json.dump(summary, fh, indent=2)

    v = summary["verdict"]
    print(f"\n{'='*64}\n{v['conclusion'].upper()}\n  {v['detail']}")
    if v.get("caveat"):
        print(f"  caveat: {v['caveat']}")

    o = summary["oem6"]
    print(f"\n0x07C6 over the run: {', '.join(f'{k} x{n}' for k, n in o['values_seen'].items()) or 'never read'}")
    if o["erm_first_armed"]:
        e = o["erm_first_armed"]
        print(f"  ERM STATUS ARMED at {e['t']} ({e['phase']}, {e['capacity']}%, {e['oem6']})"
              f" -- follow this, it is a live battery mechanism the driver ignores")
    else:
        print("  ERM status never armed")
    if o["full_24h_first_set"]:
        print(f"  full-for-24h set at {o['full_24h_first_set']['t']}")
    print(f"{'='*64}")
    print(f"samples: {args.output}\nsummary: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
