#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_curve.py -- read the EC fan tables, or load one from a vendor JSON export.

WHAT THIS IS FOR

Importing Control Center's own curve export, which `hydroc.fancurve` does not
parse. Everything else here delegates to that module rather than touching the
registers directly.

WHY IT DELEGATES NOW

It used to carry its own table code, and that code had the band order
backwards:

    REG_CPU_DOWN_T = 0x0F00      # wrong
    REG_CPU_UP_T   = 0x0F10      # wrong

0x0F00 holds the RISE threshold and 0x0F10 the FALL. The vendor's own tables
read 0x0F00 = 57 against 0x0F10 = 48, and 0x0F00[i] == 0x0F10[i+1] across the
table. `hydroc.fancurve` was fixed for this; so was `ec_state_capture.py`.
This file was the third copy and was still writing inverted bands, which is
the one direction that matters -- a fall threshold above the rise threshold
makes the fan hunt.

It also wrote raw tables with no validation, so a vendor JSON with a zero duty
at temperature went straight to the hardware. Going through
`hydroc.fancurve.validate` first is the point of having that function.

    sudo python3 fan_curve.py --read
    sudo python3 fan_curve.py --write curve.json [--dry-run]

Writing tables does not touch the enable bit. Use the daemon, or
`hydroc.fancurve.apply_curves`, for populate-then-enable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc import fancurve as fc                                # noqa: E402
from hydroc.ec import EC, ECUnavailable, ECWriteRejected         # noqa: E402


def load_vendor_json(path: str) -> dict[str, list[list[int]]]:
    """Control Center's export -> the [up_t, down_t, duty] triples the
    package uses. The vendor names the fields; we only reorder them."""
    with open(path) as f:
        data = json.load(f)
    out = {}
    for fan, key in (("cpu", "CPU"), ("gpu", "GPU")):
        pts = data[key]
        if len(pts) != fc.TABLE_LEN:
            raise ValueError(
                f"{key}: need {fc.TABLE_LEN} points, got {len(pts)}")
        out[fan] = [[p["UpT"], p["DownT"], p["Duty"]] for p in pts]
    return out


def print_curve(name: str, curve: list[list[int]]) -> None:
    print(f"\n{name}:")
    print(f"  {'ID':>3}  {'UpT':>5}  {'DownT':>5}  {'Duty%':>5}")
    for i, (up_t, down_t, duty) in enumerate(curve):
        print(f"  {i:>3}  {up_t:>5}  {down_t:>5}  {duty:>5}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--read", action="store_true", help="read current tables")
    ap.add_argument("--write", metavar="JSON", help="write curve from vendor JSON")
    ap.add_argument("--dry-run", action="store_true",
                    help="validate and print, write nothing")
    args = ap.parse_args(argv)

    if not (args.read or args.write):
        ap.print_help()
        return 1

    if os.geteuid() != 0:
        print("needs root: sudo python3 fan_curve.py ...")
        return 1

    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1
    ec = EC()

    if args.read:
        for fan in ("cpu", "gpu"):
            print_curve(fan.upper(), fc.read_curve(ec, fan))
        print(f"\nmanual control: {'on' if fc.is_enabled(ec) else 'off'}"
              f"   split tables: {'yes' if fc.is_split(ec) else 'no'}")
        return 0

    curves = load_vendor_json(args.write)
    for fan, curve in curves.items():
        print_curve(fan.upper(), curve)
        # Before writing, not after. A zero duty at temperature is a fan-stop
        # waiting for the wrong moment, and this script used to pass those
        # straight through.
        try:
            fc.validate(curve, f"{os.path.basename(args.write)}.{fan}")
        except fc.CurveError as e:
            print(f"\nrefusing to write: {e}")
            return 1

    if args.dry_run:
        print("\n[dry-run] validated; nothing written")
        return 0

    for fan, curve in curves.items():
        print(f"\nWriting {fan.upper()} table...")
        try:
            fc.write_curve(ec, fan, curve)
        except (ECUnavailable, ECWriteRejected) as e:
            print(f"  failed: {e}")
            return 1

    print("\nWritten. Reading back...")
    for fan in ("cpu", "gpu"):
        print_curve(f"{fan.upper()} (readback)", fc.read_curve(ec, fan))
    if not fc.is_enabled(ec):
        print("\nNote: manual fan control is OFF, so the firmware is still "
              "driving the fans. These tables take effect when it is enabled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
