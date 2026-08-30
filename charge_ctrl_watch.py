#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Watch EC 0x07B9 (CHARGE_CTRL) to see whether the EC overwrites the threshold.

Observed behaviour: setting a threshold of 80% stops charging at 80%, then
charging resumes shortly after -- suggesting the value does not survive.

The register packs two things:

    bits 0-6   charge threshold, percent
    bit 7      CHARGE_CTRL_REACHED -- set by the EC itself

The uniwill driver marks this register volatile *because the EC writes to it*.
If the EC rewrites the whole byte to set bit 7 rather than touching that bit
alone, it also rewrites the threshold field with whatever value it believes in
-- clobbering ours. This samples fast enough to catch that.

Also samples 0x07A6 (charging profile) so a profile-driven rewrite is visible.

    sudo python3 charge_ctrl_watch.py                 # 0.5 s interval
    sudo python3 charge_ctrl_watch.py -i 0.25 -o log.csv
"""

import argparse
import os
import sys
import time

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

REG_CHARGE_CTRL = 0x07B9
REG_OEM_4 = 0x07A6
BAT = "/sys/class/power_supply/BAT0"

PROFILE = {0: "HIGH_CAPACITY/LongHaul", 1: "BALANCED/Balanced", 2: "STATIONARY/Stationary"}


def _call(expr):
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        return fh.read().strip().rstrip("\x00")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--interval", type=float, default=0.5)
    ap.add_argument("-o", "--output")
    ap.add_argument("--set", type=int, metavar="PCT",
                    help="write this threshold first, then watch what happens to it")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")

    if args.set is not None:
        cur = ec_read(REG_CHARGE_CTRL)
        newv = (cur & 0x80) | (args.set & 0x7F)
        ec_write(REG_CHARGE_CTRL, newv)
        time.sleep(0.2)
        back = ec_read(REG_CHARGE_CTRL)
        print(f"wrote threshold {args.set}%  (0x{newv:02X}) -> reads 0x{back:02X} "
              f"= {back & 0x7F}%, reached={'yes' if back & 0x80 else 'no'}\n")

    csv = None
    if args.output:
        csv = open(args.output, "a")
        csv.write("time,raw,threshold,reached,profile,capacity,current_ma,status\n")

    print(f"{'time':9} {'raw':>4} {'thr':>4} {'rch':>4} {'profile':<24} "
          f"{'cap':>4} {'mA':>6}  status")
    print("-" * 78)

    prev = None
    started = time.time()
    try:
        while True:
            raw = ec_read(REG_CHARGE_CTRL)
            oem4 = ec_read(REG_OEM_4)
            if raw is None:
                time.sleep(args.interval)
                continue

            thr, reached = raw & 0x7F, bool(raw & 0x80)
            prof = (oem4 >> 4) & 0x03 if oem4 is not None else None
            cap = sysfs("capacity")
            cur = sysfs("current_now")
            cur_ma = int(cur) // 1000 if cur else 0
            st = sysfs("status")
            key = (raw, prof, st)

            if key != prev:
                t = time.strftime("%H:%M:%S")
                mark = ""
                if prev is not None and (prev[0] & 0x7F) != thr:
                    mark = f"   <<< THRESHOLD CHANGED {prev[0] & 0x7F}% -> {thr}%"
                print(f"{t:9} {raw:>4X} {thr:>4} {'yes' if reached else 'no':>4} "
                      f"{PROFILE.get(prof, '?'):<24} {cap or '-':>4} {cur_ma:>6}  {st}{mark}",
                      flush=True)
                prev = key

            if csv:
                csv.write(f"{time.strftime('%H:%M:%S')},{raw},{thr},{int(reached)},"
                          f"{prof},{cap},{cur_ma},{st}\n")
                csv.flush()

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nwatched for {time.time()-started:.0f}s")
        if csv:
            csv.close()


if __name__ == "__main__":
    main()
