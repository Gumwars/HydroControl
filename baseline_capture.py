#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
baseline_capture.py -- the Linux reference the Windows capture diffs against.

Run this immediately before pulling the Linux drive, and run it twice: once at
rest and once mid-charge. The mid-charge one is the one that matters, because
the charge-voltage derating only computes while 0x0490 bit 0 is set, so a
resting 0x0522:0x0523 may be stale.

WHY A SCRIPT AND NOT A COMMAND

Every register on the Windows watchlist has to be compared against a Linux
value taken under the same conditions. Temperature moves -- 0x0502:0x0503 went
3030 to 3040 inside an evening -- and the cycle count moves too, 132 to 133 in
a day. A baseline from last week against a Windows read from next week differs
by conditions, not by operating system, and there is no way to tell those apart
after the fact.

So this records the battery state and the registers in one pass, timestamped,
into a file that goes in the repository before the drive comes out.

    sudo python3 baseline_capture.py -o linux-baseline-rest.txt
    sudo python3 baseline_capture.py -o linux-baseline-charging.txt
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime

BAT = "/sys/class/power_supply/BAT0"
HERE = os.path.dirname(os.path.abspath(__file__))

SYSFS = ["capacity", "status", "current_now", "voltage_now", "charge_now",
         "charge_full", "charge_full_design", "cycle_count", "charge_types",
         "charge_control_end_threshold"]

# Every range on the Windows watchlist, plus the ranges whose emptiness is
# itself recorded evidence -- 0x08xx/0x09xx/0x0Axx read 0xFF here, and a
# Windows read that answers would be a finding.
RANGES = [
    (0x0300, 0x030F, "hw_base 0x030E:0x030F (BIG-endian) = pack max mV"),
    (0x0490, 0x0497, "0x0490 guards, 0x0491 cell-count selector, 0x0497"),
    (0x04A0, 0x04AF, "0x04A2:0x04A3 temp copy, 0x04A6:7 cycles, 0x04AB capacity"),
    (0x0500, 0x0523, "battery block; 0x0502:3 temperature, 0x0522:3 chg_target"),
    (0x0740, 0x0747, "PROJECT_ID, AP_OEM, SUPPORT_5"),
    (0x07A0, 0x07C7, "0x07A6 profile, 0x07B9 threshold, 0x07C3 ceiling gate"),
    (0x08F0, 0x08FF, "expected 0xFF -- outside the ECRR window"),
    (0x09C0, 0x09CF, "expected 0xFF -- stress accumulator lives here"),
    (0x0A50, 0x0A5F, "expected 0xFF -- derating table inputs live here"),
]


def sysfs(name: str) -> str:
    try:
        with open(os.path.join(BAT, name)) as fh:
            return fh.read().strip()
    except OSError:
        return "-"


def dump(start: int, end: int) -> str:
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "ec_dump.py"),
         "--start", hex(start), "--end", hex(end)],
        capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture the Linux reference state.")
    ap.add_argument("-o", "--output", required=True)
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")

    out = [f"# Linux baseline  {datetime.now().isoformat(timespec='seconds')}",
           f"# kernel {os.uname().release}", ""]

    out.append("## battery")
    for f in SYSFS:
        out.append(f"  {f:32} {sysfs(f)}")

    v, c = sysfs("voltage_now"), sysfs("capacity")
    if v.isdigit():
        out.append(f"  {'volts per cell (4S)':32} {int(v)/4e6:.4f}")
    out.append("")

    charging = sysfs("status") == "Charging"
    out.append(f"## charging: {charging}")
    if not charging:
        out.append("  NOTE: the derating only computes while 0x0490 bit 0 is")
        out.append("  set, so 0x0522:0x0523 may be stale. Take a mid-charge")
        out.append("  capture as well -- that is the one Windows diffs against.")
    out.append("")

    for start, end, why in RANGES:
        out.append(f"## 0x{start:04X}-0x{end:04X}   {why}")
        out.append(dump(start, end).rstrip())
        out.append("")

    text = "\n".join(out) + "\n"
    with open(args.output, "w") as fh:
        fh.write(text)
    print(text)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
