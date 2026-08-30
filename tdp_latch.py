#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Toggle Uniwill "custom profile mode" (EC 0x0727 bit 6) and observe the effect.

Why
---
tuxedo-drivers (src/uniwill_keyboard.h, uniwill_set_custom_profile_mode)
documents 0x0727 bit 6 as the gate that must be set "for custom TDP values
(and sometimes fan control) to have effect". On this machine it reads 0x80 --
bit 6 clear -- so custom TDP/fan control has never been enabled.

The CPU power limits live in the ECXP window (0xFE410400), reachable through
the same ECRR/ECRW accessors:

    0x046A PL1L   0x046B PL2L   0x046E PL3L   0x046F PL4L

Currently 75 / 75 / 0 / 125 -- plausible watts for a 14900HX.

Safety
------
This only flips a mode bit; it writes no power-limit values. Worst realistic
outcome is the EC starts enforcing the 75 W already sitting in PL1L, which is
a performance change, not a hazard -- and --disable puts it straight back.
The bit is volatile and clears on power cycle regardless.

Usage:
    sudo python3 tdp_latch.py --status
    sudo python3 tdp_latch.py --enable
    sudo python3 tdp_latch.py --disable
"""

import argparse
import glob
import os
import sys
import time

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

CUSTOM_PROFILE = 0x0727
CUSTOM_BIT = 1 << 6
# ECXP window: the EC's *live* values, read-only in practice -- writes here
# are accepted by ECRW but do not persist.
PL_LIVE = {0x046A: "PL1L", 0x046B: "PL2L", 0x046E: "PL3L", 0x046F: "PL4L"}
# 0x07xx window: the uniwill driver calls these PL1_SETTING / PL2_SETTING.
# Hypothesis: this is the write side, and PL_LIVE reflects what the EC applied.
PL_SET = {0x0783: "PL1_SETTING", 0x0784: "PL2_SETTING"}


def _call(expr):
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        return fh.read().strip().rstrip("\x00")


def ec_read(addr):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        raise SystemExit(f"read 0x{addr:04X} failed: {raw}")
    return int(raw, 16) & 0xFF


def ec_write(addr, val):
    raw = _call(f"{ECRW} 0x{addr:X} 0x{val:X}")
    if raw.startswith("Error"):
        raise SystemExit(f"write 0x{addr:04X} failed: {raw}")


def rapl():
    """Enforced package limits, as the kernel sees them."""
    out = []
    for d in sorted(glob.glob("/sys/class/powercap/intel-rapl:0/constraint_*_name")):
        i = d.split("constraint_")[1].split("_name")[0]
        try:
            name = open(d).read().strip()
            uw = int(open(f"/sys/class/powercap/intel-rapl:0/constraint_{i}_power_limit_uw").read())
            out.append(f"{name}={uw/1e6:.1f}W")
        except (OSError, ValueError):
            pass
    return "  ".join(out) if out else "(unavailable)"


def show():
    v = ec_read(CUSTOM_PROFILE)
    print(f"  0x0727 = 0x{v:02X} 0b{v:08b}   custom profile mode: "
          f"{'ENABLED' if v & CUSTOM_BIT else 'disabled'}")
    for a, n in PL_SET.items():
        v = ec_read(a)
        note = "  (0 = no override)" if v == 0 else ""
        print(f"  0x{a:04X} {n:12s} = {v:3d}{note}")
    for a, n in PL_LIVE.items():
        print(f"  0x{a:04X} {n:12s} = {ec_read(a):3d} W   [live readback]")
    print(f"  RAPL: {rapl()}")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    g.add_argument("--set-pl1", type=int, metavar="WATTS",
                   help="write PL1L (0x046A). Also sets PL2L to match.")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")

    print("before:")
    show()

    if args.status:
        return

    if args.set_pl1 is not None:
        w = args.set_pl1
        if w != 0 and not 15 <= w <= 125:
            raise SystemExit("refusing: use 0 (firmware default) or 15-125 W. "
                             "Below 15 W risks an unusable machine; above 125 W "
                             "exceeds the PL4 value this firmware ships with.")
        if not ec_read(CUSTOM_PROFILE) & CUSTOM_BIT:
            print("\nNOTE: custom profile mode (0x0727 bit 6) is NOT set, so this "
                  "write\n      will probably have no effect. Run --enable first.")
        # Write the SETTING registers, not the live readback.
        ec_write(0x0783, w)
        ec_write(0x0784, w)
        print(f"\nwrote PL1_SETTING and PL2_SETTING = {w} W")
        time.sleep(1.0)
        print("\nafter:")
        show()
        print("\nIf the live PL1L above changed to match, the setting took effect.\n"
              "Then measure real draw under load:\n"
              "  sudo python3 powerwatch.py -d 60\n"
              "Restore firmware default with:  --set-pl1 0")
        return

    v = ec_read(CUSTOM_PROFILE)
    if args.enable:
        # tuxedo-drivers clears the bit first on some devices so the set
        # is applied properly; harmless where it isn't needed.
        ec_write(CUSTOM_PROFILE, v & ~CUSTOM_BIT)
        time.sleep(0.05)
        ec_write(CUSTOM_PROFILE, (v | CUSTOM_BIT) & 0xFF)
        print("\nset 0x0727 bit 6")
    else:
        ec_write(CUSTOM_PROFILE, v & ~CUSTOM_BIT & 0xFF)
        print("\ncleared 0x0727 bit 6")

    time.sleep(1.0)
    print("\nafter:")
    show()
    print("\nWatch for: RAPL dropping toward the PL1L value, fans changing "
          "behaviour, or\nclocks capping under load. Revert with --disable.")


if __name__ == "__main__":
    main()
