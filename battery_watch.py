#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Watch the battery across a charge-mode switch to see through the EC's masking.

The problem
-----------
This EC does not expose a percentage cap the way Dell/ASUS do. In a
battery-health mode it appears to charge to a hidden ceiling and then report
100% to the OS, hiding the true state. So `capacity` and
`charge_control_end_threshold` are both unreliable witnesses.

What this watches instead
-------------------------
  current_now   Actual current. If charging genuinely resumes, this goes
                non-zero. Hard to fake without also lying about current.
  charge_full   The likely masking mechanism: if the EC shrinks this to the
                capped value, capacity = charge_now/charge_full reads 100%
                while the cell is really at charge_now/charge_full_design.
  true%         charge_now / charge_full_design -- the unmasked figure.
                Any gap between this and `capacity` IS the mask.
  0x07A6/0x07B9 EC profile bits and threshold, read via acpi_call, so the
                register state is correlated with the observed behaviour.

Usage
-----
    sudo python3 battery_watch.py               # 10s interval
    sudo python3 battery_watch.py -i 5 -o run.csv

Suggested experiment (see the header notes in the runbook):
    1. Discharge below any plausible cap (~60%) on battery.
    2. Plug in AC with the mode set to the health-saving one. Watch where
       current_now falls to zero -- that is the real ceiling.
    3. Switch to another mode. If current_now resumes and charge_now climbs
       past the previous ceiling, the mode change landed.
"""

import argparse
import os
import sys
import time

B = "/sys/class/power_supply/BAT0"
AC = "/sys/class/power_supply/AC0/online"
CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"


def rd(name, base=B):
    try:
        with open(os.path.join(base, name)) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def rdi(name):
    v = rd(name)
    try:
        return int(v)
    except ValueError:
        return None


def ec(addr):
    """Read one EC byte via acpi_call; None if unavailable."""
    if not os.path.exists(CALL):
        return None
    try:
        with open(CALL, "w") as fh:
            fh.write(f"{ECRR} 0x{addr:X}")
        with open(CALL) as fh:
            raw = fh.read().strip().rstrip("\x00")
        if raw.startswith("Error"):
            return None
        return int(raw, 16) & 0xFF
    except OSError:
        return None


def sample():
    now = rdi("charge_now")
    full = rdi("charge_full")
    design = rdi("charge_full_design")
    cur = rdi("current_now")
    volt = rdi("voltage_now")

    true_pct = (now / design * 100) if (now is not None and design) else None
    rep_pct = rdi("capacity")

    oem4 = ec(0x07A6)
    profile = None if oem4 is None else (oem4 >> 4) & 0x03
    thresh = ec(0x07B9)
    if thresh is not None:
        thresh &= 0x7F

    return {
        "t": time.strftime("%H:%M:%S"),
        "ac": rd("online", "/sys/class/power_supply/AC0"),
        "status": rd("status"),
        "cap": rep_pct,
        "true": true_pct,
        "now_mah": None if now is None else now // 1000,
        "full_mah": None if full is None else full // 1000,
        "design_mah": None if design is None else design // 1000,
        "cur_ma": None if cur is None else cur // 1000,
        "mv": None if volt is None else volt // 1000,
        # 4S pack: voltage_min_design 15.48 V = 3.87 V/cell nominal.
        # Charge voltage is the dominant lithium-ion longevity factor, so the
        # per-cell figure at Full is what distinguishes the charging profiles.
        "vcell": None if volt is None else volt / 1e6 / 4,
        "mode": rd("charge_types"),
        "profile": profile,
        "thresh": thresh,
    }


PROFILE_NAME = {0: "HIGH_CAPACITY", 1: "BALANCED", 2: "STATIONARY"}

HDR = (f"{'time':8} {'AC':2} {'status':9} {'cap%':>5} {'true%':>6} "
       f"{'now':>6} {'full':>6} {'mA':>7} {'V':>6} {'V/cell':>7} {'prof':>13} {'thr':>4}")


def fmt(s):
    prof = "-" if s["profile"] is None else PROFILE_NAME.get(s["profile"], f"?{s['profile']}")

    def n(v, w, dash="-"):
        return f"{dash:>{w}}" if v is None else f"{v:>{w}}"

    true_s = "-".rjust(6) if s["true"] is None else f"{s['true']:>6.1f}"

    volt_s = "-".rjust(6) if s["mv"] is None else f"{s['mv']/1000:>6.3f}"
    cell_s = "-".rjust(7) if s["vcell"] is None else f"{s['vcell']:>7.3f}"

    return (f"{s['t']:8} {s['ac']:2} {s['status']:9} "
            f"{n(s['cap'], 5)} {true_s} "
            f"{n(s['now_mah'], 6)} {n(s['full_mah'], 6)} {n(s['cur_ma'], 7)} "
            f"{volt_s} {cell_s} "
            f"{prof:>13} {n(s['thresh'], 4)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--interval", type=float, default=10.0)
    ap.add_argument("-o", "--output", help="append samples to this CSV")
    ap.add_argument("--all", action="store_true",
                    help="print every sample, not just changes")
    args = ap.parse_args()

    if not os.path.isdir(B):
        raise SystemExit(f"{B} not found")
    if not os.path.exists(CALL):
        print("note: acpi_call not loaded -- EC columns will show '-'\n"
              "      sudo modprobe acpi_call", file=sys.stderr)

    csv = None
    if args.output:
        new = not os.path.exists(args.output)
        csv = open(args.output, "a")
        if new:
            csv.write("time,ac,status,capacity,true_pct,charge_now_mah,"
                      "charge_full_mah,current_ma,millivolts,v_per_cell,"
                      "profile,threshold,mode\n")

    print(HDR)
    print("-" * len(HDR))

    prev_key = None
    try:
        while True:
            s = sample()
            key = (s["status"], s["cap"], s["now_mah"], s["full_mah"],
                   s["cur_ma"], s["profile"], s["thresh"])

            if args.all or key != prev_key:
                line = fmt(s)
                # Flag the mask: reported 100% while the cell is demonstrably lower.
                if s["true"] is not None and s["cap"] == 100 and s["true"] < 97:
                    line += f"   <-- MASKED (reports 100%, actually {s['true']:.1f}%)"
                print(line, flush=True)
                prev_key = key

            if csv:
                csv.write(f"{s['t']},{s['ac']},{s['status']},{s['cap']},"
                          f"{'' if s['true'] is None else round(s['true'],2)},"
                          f"{s['now_mah']},{s['full_mah']},{s['cur_ma']},"
                          f"{s['mv']},"
                          f"{'' if s['vcell'] is None else round(s['vcell'],4)},"
                          f"{s['profile']},{s['thresh']},\"{s['mode']}\"\n")
                csv.flush()

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if csv:
            csv.close()


if __name__ == "__main__":
    main()
