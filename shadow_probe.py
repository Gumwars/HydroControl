#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
shadow_probe.py -- does the EC's charge-limit code run? Ask it a question.

Every test this project has run on the charge ceiling has been passive: set
something, watch, and see whether the EC reacts. Two full charge cycles later
CHARGE_CTRL_REACHED has never armed and 0x0742 has never moved, and neither
result distinguishes "the code does not run" from "the code runs and has
nothing to do". A witness that only fires on a state change is silent when the
state does not change, which is the flaw in the 0x0742 footprint.

This asks a question instead.

Decoded from 117.ELUK at 0x1C8F5, in the same routine as the capacity
comparison:

    90 08 7F   MOV  DPTR,#087Fh
    E0         MOVX A,@DPTR       ; the shadow copy of the threshold
    6B         XRL  A,R3          ; against the live threshold
    60 3D      JZ   +61           ; identical -- nothing to do
    CF EB CF
    EF         MOV  A,R7
    F0         MOVX @DPTR,A       ; otherwise copy it across

0x087F mirrors 0x07B9, and the EC only writes it when they disagree. So make
them disagree. Change the threshold, wait, and read 0x087F:

    it follows   the routine ran. The code is live, and the ceiling failing is
                 about what it decides, not about whether it executes.
    it does not  the routine did not run while the threshold changed under it,
                 which is a positive result rather than another silence.

STATUS: DOES NOT WORK ON THE HYDROC-16 G1. 0x087F is not readable here.

It reads 0xFF, which on this EC means unmapped address space rather than a
value -- DESIGN.md 3.2 recorded exactly that on 2026-08-27, in a table, and
this file was written anyway. The G2 build keeps the same variable at 0x9164,
far outside anything ECRR exposes, which should have been the second clue.

The probe now refuses to run rather than reporting a verdict, and it refuses
BEFORE touching the threshold, because a test whose witness is invisible
should not be changing anything. Kept rather than deleted: the approach is
right and the address may be mapped on another Uniwill project, so a machine
where 0x087F reads something other than 0xFF can use it as written.

On this machine the visible witnesses inside the ECRR window are exhausted.
The ceiling routine's only observable output is CHARGE_CTRL_REACHED, which
has never armed; everything else it writes -- 0x087F, 0x09C7-0x09C9, 0x0A51 --
is outside the window. That is an argument for watching the Control Center
service on Windows rather than for another probe from here.

WHAT THIS WRITES

The threshold, once, through the kernel driver's own sysfs interface --
charge_control_end_threshold, the same path TLP and GNOME use. Not a raw EC
write. The original value is restored before exit, including on Ctrl-C.

It does NOT write 0x087F, 0x07A6, or any EC register directly, and it does not
hold anything. Sustained re-writing of the charge registers is the one
operation known to have damaged this machine (DESIGN.md 4.1b).

    sudo python3 shadow_probe.py
    sudo python3 shadow_probe.py --to 65 --settle 20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
SYSFS = "/sys/class/power_supply/BAT0/charge_control_end_threshold"

REG_CHARGE_CTRL = 0x07B9        # read only, never written here
REG_SHADOW = 0x087F             # the mirror

EC_DELAY = 0.006
_last = 0.0


def ec_read(addr: int):
    """One paced ECRR read. This file has no write counterpart."""
    global _last
    gap = time.monotonic() - _last
    if gap < EC_DELAY:
        time.sleep(EC_DELAY - gap)
    with open(CALL, "w") as fh:
        fh.write(f"{ECRR} 0x{addr:X}")
    with open(CALL) as fh:
        raw = fh.read().strip().rstrip("\x00")
    _last = time.monotonic()
    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def read_threshold() -> int | None:
    try:
        with open(SYSFS) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def write_threshold(value: int) -> str | None:
    """Through the driver, not the EC. Returns an error string, or None."""
    try:
        with open(SYSFS, "w") as fh:
            fh.write(str(value))
        return None
    except OSError as e:
        return str(e)


def pick_target(current: int, requested: int | None) -> int:
    """A target that actually differs, and stays in a sane range.

    Writing the value that is already there would make the EC's own comparison
    fail and produce exactly the silence this probe exists to break.
    """
    if requested is not None:
        return requested
    return 70 if current != 70 else 75


def verdict(before: int | None, after: int | None, target: int) -> dict:
    followed = after is not None and after == target
    return {
        "followed": followed,
        "reading": (
            "unreadable" if after is None else
            f"0x087F followed 0x07B9 to {target}. The routine RAN -- it is "
            f"live code, reached while the machine was charging, and the "
            f"ceiling failing is about what it decides rather than whether it "
            f"executes."
            if followed else
            f"0x087F stayed at {before} while the threshold moved to {target}. "
            f"The routine did not run in this window. Unlike a flat 0x0742 "
            f"this is a positive result: the EC was given something to do and "
            f"did not do it."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Does the EC charge-limit code run?")
    ap.add_argument("--to", type=int, default=None,
                    help="threshold to move to (default: something different)")
    ap.add_argument("--settle", type=float, default=15.0,
                    help="seconds to wait before re-reading the shadow")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")
    if not os.path.exists(SYSFS):
        raise SystemExit(f"{SYSFS} is absent; is uniwill_laptop loaded?")

    original = read_threshold()
    if original is None:
        raise SystemExit("cannot read the threshold -- refusing to change it blind")

    reg = ec_read(REG_CHARGE_CTRL)
    shadow_before = ec_read(REG_SHADOW)
    print(f"before:  sysfs={original}  0x07B9={reg if reg is None else hex(reg)}"
          f" (threshold {None if reg is None else reg & 0x7F},"
          f" reached {None if reg is None else (reg >> 7) & 1})"
          f"  0x087F={shadow_before}")

    if shadow_before is None:
        raise SystemExit("0x087F did not answer; nothing to compare")
    if shadow_before == 0xFF:
        raise SystemExit(
            "0x087F reads 0xFF, which on this EC is unmapped address space and\n"
            "not a value (DESIGN.md 3.2: '0x087F sits outside both windows').\n"
            "The witness is invisible, so the test cannot say anything, and the\n"
            "threshold has NOT been changed.\n\n"
            "Reporting 'the routine did not run' from this would be the same\n"
            "mistake as reading 0x0984 as a hardware interlock when it was\n"
            "unmapped space with every bit set.")

    target = pick_target(original, args.to)
    if target == original:
        raise SystemExit(f"threshold is already {target}; pick a different --to")

    print(f"\nmoving the threshold {original} -> {target} through the driver "
          f"(not a raw EC write)")
    err = write_threshold(target)
    if err:
        raise SystemExit(f"sysfs write failed: {err}")

    try:
        deadline = time.monotonic() + args.settle
        shadow_after = shadow_before
        while time.monotonic() < deadline:
            time.sleep(1.0)
            shadow_after = ec_read(REG_SHADOW)
            reg_now = ec_read(REG_CHARGE_CTRL)
            print(f"  +{args.settle - (deadline - time.monotonic()):4.0f}s  "
                  f"0x07B9={None if reg_now is None else reg_now & 0x7F}  "
                  f"0x087F={shadow_after}", flush=True)
            if shadow_after == target:
                break
    finally:
        print(f"\nrestoring the threshold to {original}")
        write_threshold(original)

    v = verdict(shadow_before, shadow_after, target)
    print(f"\n{v['reading']}")

    out = {"when": datetime.now().isoformat(timespec="seconds"),
           "original": original, "target": target,
           "shadow_before": shadow_before, "shadow_after": shadow_after,
           **v}
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0 if v["followed"] else 1


if __name__ == "__main__":
    sys.exit(main())
