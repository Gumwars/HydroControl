#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Watch a charge cycle and answer one question: does capacity climb past the
threshold?

That is the discriminator, and nobody has ever recorded it. The only negative
evidence for the charge threshold is "80% stops charging briefly, then it
resumes" -- never instrumented, and *also exactly what a working threshold looks
like*. The driver exposes only an end threshold, with no
charge_control_start_threshold, so whatever hysteresis the EC uses is internal
and invisible: stop at 80, self-discharge to 78, top up, stop. Watching `status`
flip reads as "it does not hold". It is not. Capacity exceeding the threshold is.

Secondary question: does the EC clobber the threshold? 0x07B9 packs

    bits 0-6   charge threshold, percent
    bit 7      CHARGE_CTRL_REACHED -- set by the EC itself

and the uniwill driver marks the register volatile *because the EC writes to it*.
If the EC rewrites the whole byte to set bit 7 rather than touching that bit
alone, the threshold field goes with it.

Sampling is adaptive. DESIGN.md §4.2: sustained EC traffic is the hazard, and
reading the EC too hard has stopped the fans. The event of interest happens near
the threshold, so this samples slowly when capacity is far away and quickly when
it is close, instead of hammering the EC for hours to catch one moment. Every
access is 6 ms paced, the same floor the driver and hydroc/ec.py enforce.

    sudo python3 charge_ctrl_watch.py -o cycle.csv
    sudo python3 charge_ctrl_watch.py --set 80 -o cycle.csv
"""

import argparse
import os
import sys
import time
from datetime import datetime

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

REG_CHARGE_CTRL = 0x07B9
REG_OEM_4 = 0x07A6
BAT = "/sys/class/power_supply/BAT0"

PROFILE = {0: "HIGH_CAPACITY/LongHaul", 1: "BALANCED/Balanced", 2: "STATIONARY/Stationary"}

# The OEM software sleeps up to 6 ms after every EC access and uniwill-acpi.c
# emulates it; hydroc/ec.py enforces it class-wide. This script does not import
# hydroc.ec, so it enforces the same floor itself. Without it, two back-to-back
# reads per sample for several hours is exactly the traffic pattern §4.2 blames
# for stalling the fans.
EC_DELAY = 0.006
_last_call = 0.0


def _call(expr):
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


def ec_read(addr):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def ec_write(addr, val):
    _call(f"{ECRW} 0x{addr:X} 0x{val & 0xFF:X}")


def sysfs(name):
    try:
        with open(os.path.join(BAT, name)) as fh:
            return fh.read().strip()
    except OSError:
        return None


def sysfs_int(name):
    """None means unreadable, which is NOT the same as zero."""
    v = sysfs(name)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--interval", type=float, default=1.0,
                    help="seconds between samples near the threshold")
    ap.add_argument("--idle-interval", type=float, default=15.0,
                    help="seconds between samples when far from the threshold")
    ap.add_argument("--near", type=int, default=5,
                    help="percent from the threshold that counts as near")
    ap.add_argument("--heartbeat", type=float, default=120.0,
                    help="print a line at least this often even if nothing "
                         "changed, so a steady charge still shows progress")
    ap.add_argument("-o", "--output")
    ap.add_argument("--set", type=int, metavar="PCT",
                    help="write this threshold first, then watch what happens to it")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")

    if args.set is not None:
        if not 1 <= args.set <= 100:
            raise SystemExit("--set must be 1-100")
        cur = ec_read(REG_CHARGE_CTRL)
        if cur is None:
            raise SystemExit("cannot read 0x07B9 -- refusing to write blind")
        newv = (cur & 0x80) | (args.set & 0x7F)
        ec_write(REG_CHARGE_CTRL, newv)
        time.sleep(0.2)
        back = ec_read(REG_CHARGE_CTRL)
        if back is None:
            raise SystemExit("wrote the threshold but cannot read it back")
        print(f"wrote threshold {args.set}%  (0x{newv:02X}) -> reads 0x{back:02X} "
              f"= {back & 0x7F}%, reached={'yes' if back & 0x80 else 'no'}\n")

    csv = None
    if args.output:
        csv = open(args.output, "a")
        csv.write("timestamp,raw,threshold,reached,profile,capacity,"
                  "current_ma,voltage_uv,status\n")
        csv.flush()

    print(f"{'time':9} {'raw':>4} {'thr':>4} {'rch':>4} {'profile':<24} "
          f"{'cap':>4} {'mA':>7}  status")
    print("-" * 80)

    prev_key = None
    last_print = 0.0
    started = time.time()
    thr0 = None                 # the threshold we started with
    max_cap = None              # the answer to the actual question
    cap_at_max = None
    exceeded = False
    clobbered = []
    fails = 0

    try:
        while True:
            raw = ec_read(REG_CHARGE_CTRL)
            oem4 = ec_read(REG_OEM_4)
            if raw is None:
                fails += 1
                if fails in (1, 10, 100):
                    print(f"  (EC read failed x{fails})", flush=True)
                time.sleep(args.interval)
                continue
            fails = 0

            thr, reached = raw & 0x7F, bool(raw & 0x80)
            prof = (oem4 >> 4) & 0x03 if oem4 is not None else None
            cap = sysfs_int("capacity")
            cur_ua = sysfs_int("current_now")
            volt = sysfs_int("voltage_now")
            st = sysfs("status")
            cur_ma = None if cur_ua is None else cur_ua // 1000

            if thr0 is None:
                thr0 = thr
            elif thr != thr0 and (thr, raw) not in clobbered:
                clobbered.append((thr, raw))

            if cap is not None and (max_cap is None or cap > max_cap):
                max_cap = cap
                cap_at_max = thr
                if cap > thr:
                    exceeded = True

            now = time.monotonic()
            # Capacity is the variable under test, so it belongs in the change
            # key -- leaving it out is what made a steady charge print nothing
            # for minutes while the battery climbed. The heartbeat covers the
            # rest: a plateau is data too, and silence is indistinguishable from
            # a crashed script.
            key = (raw, prof, st, cap)
            due = (now - last_print) >= args.heartbeat
            if key != prev_key or due:
                mark = ""
                if prev_key is not None and (prev_key[0] & 0x7F) != thr:
                    mark = f"   <<< THRESHOLD CHANGED {prev_key[0] & 0x7F}% -> {thr}%"
                elif cap is not None and cap > thr:
                    mark = f"   <<< ABOVE THRESHOLD ({cap}% > {thr}%)"
                print(f"{datetime.now():%H:%M:%S} {raw:>4X} {thr:>4} "
                      f"{'yes' if reached else 'no':>4} "
                      f"{PROFILE.get(prof, '?'):<24} "
                      f"{'-' if cap is None else cap:>4} "
                      f"{'-' if cur_ma is None else cur_ma:>7}  {st}{mark}",
                      flush=True)
                prev_key, last_print = key, now

            if csv:
                csv.write(f"{datetime.now().isoformat(timespec='seconds')},"
                          f"{raw},{thr},{int(reached)},{prof},"
                          f"{'' if cap is None else cap},"
                          f"{'' if cur_ma is None else cur_ma},"
                          f"{'' if volt is None else volt},{st}\n")
                csv.flush()

            # Sample hard only where the answer lives.
            near = cap is not None and abs(cap - thr) <= args.near
            time.sleep(args.interval if near else args.idle_interval)

    except KeyboardInterrupt:
        pass
    finally:
        if csv:
            csv.close()
        mins = (time.time() - started) / 60
        print(f"\n--- watched {mins:.1f} min ---")
        if thr0 is not None:
            print(f"threshold at start:      {thr0}%")
        if max_cap is None:
            print("capacity:                never read")
        else:
            print(f"highest capacity seen:   {max_cap}%")
        if clobbered:
            print(f"THRESHOLD WAS REWRITTEN: {thr0}% -> "
                  + ", ".join(f"{t}% (raw 0x{r:02X})" for t, r in clobbered))
        else:
            print("threshold held:          yes, unchanged throughout")

        # The verdict, computed rather than eyeballed.
        if max_cap is None or thr0 is None:
            print("\nverdict: not enough data.")
        elif exceeded:
            print(f"\nverdict: capacity reached {max_cap}%, ABOVE the {cap_at_max}% "
                  "threshold -- the threshold is NOT being enforced.")
        elif max_cap >= thr0 - 2:
            print(f"\nverdict: capacity plateaued at {max_cap}% against a {thr0}% "
                  "threshold and never exceeded it -- the threshold IS holding. "
                  "Charging that stops and resumes within this band is the EC's "
                  "own hysteresis, not a failure.")
        else:
            print(f"\nverdict: capacity only reached {max_cap}%, well below the "
                  f"{thr0}% threshold -- the cycle did not run long enough to "
                  "test anything. Charge closer to the threshold and re-run.")


if __name__ == "__main__":
    main()
