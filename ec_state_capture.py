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
    # Not a bitfield of fan flags. 0x0751 is the EC's performance mode:
    # 0xA0 Office, 0x00 Balanced, 0x10 Beast, +0x40 fan boost. Read through
    # the old bit labels, this machine's 0xA0 came out as "HIGH + USER",
    # which is how it was misread for weeks.
    (0x0751, "FAN_MODE / performance mode "
             "(0xA0 Office, 0x00 Balanced, 0x10 Beast, |0x40 boost)",
     {6: "fan boost"}),
    (0x075B, "PWM_1 (of 200)", {}),
    (0x075C, "PWM_2 (of 200)", {}),
    (0x0768, "SWITCH_STATUS", {2: "FAN_BOOST_STATUS"}),
    (0x078E, "FAN_CTRL caps", {3: "charge profiles", 6: "HAS_UW_FAN_CTRL"}),
    (0x07A5, "OEM_3", {2: "FAN_QUIET", 4: "OVERBOOST", 7: "HIGH_POWER"}),
    (0x07A6, "OEM_4", {4: "charge profile", 5: "charge profile"}),

    # --- added for the 1.17 -> 1.18 baseline -------------------------------
    # Everything the daemon writes, plus the charge chain. The original list
    # predates native modes and the charge investigation, so a capture taken
    # for a firmware comparison was missing the per-mode limits, the charge
    # threshold and the ceiling gate -- the registers this project learned
    # most recently and would most want to diff.
    (0x0466, "TURBO", {0: "turbo"}),
    (0x04A6, "CYCLE_LO (real count; sysfs cycle_count reads 0)", {}),
    (0x04A7, "CYCLE_HI", {}),
    (0x04AB, "battery capacity %", {}),
    (0x0490, "charge guards", {0: "charging active", 2: "guard"}),
    (0x0497, "battery-read sync gate", {0: "enables 0xC81E sync"}),
    # Little-endian, like 0x0522:0x0523 and unlike 0x030E:0x030F. These
    # labels were the other way round, which read 0x120C = 462.0 K = 188 C
    # where the pack was at 35.85 C. The known-good baseline settles it:
    # 0x0502 = 0xD6, 0x0503 = 0x0B is 0x0BD6 = 3030 = 29.85 C, so the LOW
    # byte is first.
    (0x0502, "temperature lo (0.1 K, LE pair with 0x0503)", {}),
    (0x0503, "temperature hi", {}),
    (0x0522, "charge-control output lo (little-endian pair)", {}),
    (0x0523, "charge-control output hi", {}),
    (0x0730, "balanced PL1"), (0x0731, "balanced PL2"), (0x0732, "balanced PL4"),
    (0x0733, "balanced +3 (unknown)"),
    (0x0734, "office PL1"), (0x0735, "office PL2"), (0x0736, "office PL4"),
    (0x0737, "office +3 (unknown)"),
    (0x073C, "KBID (panel type select)", {}),
    (0x073D, "LED vendor byte (vestigial -- never read by GCUService)", {}),
    (0x0742, "panel type / charge-ctrl latch", {2: "charge-ctrl active"}),
    (0x07A7, "beast PL1"), (0x07A8, "beast PL2"), (0x07A9, "beast PL4"),
    (0x07AA, "beast +3 (unknown)"),
    (0x07B9, "CHARGE_CTRL threshold", {7: "CHARGE_CTRL_REACHED"}),
    (0x07C3, "ceiling master gate (== 4 arms it)", {}),
    (0x07D8, "balanced TCC"), (0x07D9, "office TCC"), (0x07DA, "beast TCC"),

    # --- the SBS block, named by uniwill-acpi.c ----------------------------
    # 0x0402:0x0403 and 0x0404:0x0405 are DESIGN capacity and FULL capacity
    # as separate registers. sysfs reports both as 6400 mAh, which is either
    # a gauge that has not learned this pack or a constant that never will.
    # Capturing both before and after a full discharge-and-recharge answers
    # it: if full capacity moves off design, the gauge learns and a relearn
    # cycle on a new pack is real advice. If it never moves, charge_full
    # carries no information and every wear figure this project has quoted
    # is a restatement of the design figure.
    (0x0400, "BAT_POWER_UNIT lo"), (0x0401, "BAT_POWER_UNIT hi"),
    (0x0402, "BAT_DESIGN_CAPACITY lo"), (0x0403, "BAT_DESIGN_CAPACITY hi"),
    (0x0404, "BAT_FULL_CAPACITY lo"), (0x0405, "BAT_FULL_CAPACITY hi"),
    (0x0408, "BAT_DESIGN_VOLTAGE lo"), (0x0409, "BAT_DESIGN_VOLTAGE hi"),
    (0x0432, "BAT_STATUS lo"), (0x0433, "BAT_STATUS hi"),
    (0x0434, "BAT_CURRENT lo"), (0x0435, "BAT_CURRENT hi"),
    (0x0436, "BAT_REMAIN_CAPACITY lo"), (0x0437, "BAT_REMAIN_CAPACITY hi"),
    (0x0438, "BAT_VOLTAGE lo"), (0x0439, "BAT_VOLTAGE hi"),
    (0x0494, "BAT_ALERT"),
]

# Entries above may omit the bitmap; normalise so the readers can assume it.
SINGLES = [(e + ({},))[:3] if len(e) == 2 else e for e in SINGLES]

# 0x0F00 holds the RISE threshold and 0x0F10 the FALL. These labels were the
# other way round, the same inversion fancurve.py carried: the vendor's own
# tables read 0x0F00 = 57 against 0x0F10 = 48, and 0x0F00[i] == 0x0F10[i+1]
# across the table, so 0x0F10 opens a band and 0x0F00 closes it. Left wrong,
# a post-flash comparison would read every curve backwards.
TABLES = [
    (0x0F00, "CPU UpT (rise)"), (0x0F10, "CPU DownT (fall)"), (0x0F20, "CPU Duty"),
    (0x0F30, "GPU UpT (rise)"), (0x0F40, "GPU DownT (fall)"), (0x0F50, "GPU Duty"),
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
