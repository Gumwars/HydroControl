#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ec_state_capture.py — snapshot every register that governs power and fans.

Read-only. Written for the case where settings are accepted and then silently
revert: that is never one register's fault, and guessing which one has cost
this project several rounds. Capture the lot, then compare against a snapshot
taken after a power cycle -- whatever differs is what was stuck.

    sudo python3 ec_state_capture.py                    # print
    sudo python3 ec_state_capture.py -o before.json     # save
    sudo python3 ec_state_capture.py --compare before.json

Nothing here writes, and the fan tachometer registers are deliberately not
read (DESIGN.md §4.2).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable      # noqa: E402

SINGLES = [
    (0x0741, "AP_OEM", {0: "ENABLE_MANUAL_CTRL  <-- host-in-control master flag",
                        3: "ITE_KBD_EFFECT_REACTIVE", 5: "FAN_ABNORMAL"}),
    (0x0727, "custom profile", {6: "custom-profile latch", 7: "double-PL4"}),
    (0x0783, "PL1_SETTING  (0 = firmware default)", {}),
    (0x0784, "PL2_SETTING  (0 = firmware default)", {}),
    (0x0785, "PL4_SETTING  (0 = firmware default, half scale)", {}),
    (0x046A, "PL1_LIVE", {}),
    (0x046B, "PL2_LIVE", {}),
    (0x046E, "PL3_LIVE", {}),
    (0x046F, "PL4_LIVE (half scale)", {}),
    (0x07C5, "UNIVERSAL_FAN_CTRL", {7: "SPLIT_TABLES"}),
    (0x07C6, "AP_OEM_6", {2: "ENABLE_UNIVERSAL_FAN_CTRL"}),
    (0x0751, "MANUAL_FAN_CTRL", {4: "TURBO", 5: "HIGH", 6: "BOOST", 7: "USER"}),
    (0x075B, "PWM_1 (of 200)", {}),
    (0x075C, "PWM_2 (of 200)", {}),
    (0x0768, "SWITCH_STATUS", {2: "FAN_BOOST_STATUS"}),
    (0x078E, "FAN_CTRL caps", {3: "charge profiles", 6: "HAS_UW_FAN_CTRL"}),
    (0x07A5, "OEM_3", {2: "FAN_QUIET", 4: "OVERBOOST", 7: "HIGH_POWER"}),
    (0x07A6, "OEM_4", {4: "charge profile", 5: "charge profile"}),
]

TABLES = [
    (0x0F00, "CPU DownT"), (0x0F10, "CPU UpT"), (0x0F20, "CPU Duty"),
    (0x0F30, "GPU DownT"), (0x0F40, "GPU UpT"), (0x0F50, "GPU Duty"),
]


def capture(ec: EC) -> dict:
    out: dict = {"singles": {}, "tables": {}}
    for addr, _label, _bits in SINGLES:
        try:
            out["singles"][f"0x{addr:04X}"] = ec.read(addr)
        except ECUnavailable:
            out["singles"][f"0x{addr:04X}"] = None
    for base, _label in TABLES:
        row = []
        for i in range(16):
            try:
                row.append(ec.read(base + i))
            except ECUnavailable:
                row.append(None)
        out["tables"][f"0x{base:04X}"] = row
    return out


def show(snap: dict) -> None:
    print("  registers")
    for addr, label, bitmap in SINGLES:
        v = snap["singles"].get(f"0x{addr:04X}")
        if v is None:
            print(f"    0x{addr:04X}  unreadable        {label}")
            continue
        flags = " ".join(n for b, n in sorted(bitmap.items()) if v >> b & 1)
        print(f"    0x{addr:04X}  0x{v:02X} {format(v,'08b')}  {label}"
              + (f"   [{flags}]" if flags else ""))
    print("\n  fan curve tables")
    for base, label in TABLES:
        row = snap["tables"].get(f"0x{base:04X}", [])
        vals = " ".join("--" if x is None else f"{x:02X}" for x in row)
        allzero = all(x == 0 for x in row if x is not None)
        print(f"    0x{base:04X} {label:<10} {vals}"
              + ("   (all zero)" if allzero else ""))


def compare(a: dict, b: dict) -> None:
    print("\n=== differences (reference -> now) ===")
    diff = False
    for addr, label, bitmap in SINGLES:
        k = f"0x{addr:04X}"
        x, y = a["singles"].get(k), b["singles"].get(k)
        if x != y:
            diff = True
            changed = (x ^ y) if (x is not None and y is not None) else 0
            names = " ".join(n for bit, n in sorted(bitmap.items())
                             if changed >> bit & 1)
            print(f"  {k}  0x{x:02X} -> 0x{y:02X}   {label}"
                  + (f"   [{names}]" if names else ""))
    for base, label in TABLES:
        k = f"0x{base:04X}"
        if a["tables"].get(k) != b["tables"].get(k):
            diff = True
            print(f"  {k}  {label}: table contents differ")
    if not diff:
        print("  nothing changed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output")
    ap.add_argument("--compare", metavar="FILE")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("needs root: sudo python3 ec_state_capture.py")
        return 1
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    snap = capture(EC())
    show(snap)

    if args.output:
        try:
            with open(args.output, "w") as fh:
                json.dump(snap, fh, indent=2)
            print(f"\nwrote {args.output}")
        except OSError as e:
            print(f"\ncould not write {args.output}: {e}")

    if args.compare:
        try:
            with open(args.compare) as fh:
                ref = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"\ncould not read {args.compare}: {e}")
            return 1
        compare(ref, snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
