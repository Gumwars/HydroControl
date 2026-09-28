#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ec_image_scan.py -- find register logic in an ITE 8051 EC firmware image.

This project spent a month asking whether the charging profiles work on a
HYDROC-16 G1 by poking the EC and watching the battery. Three write doors set
0x07A6 and verified it, the mailbox showed the EC servicing the transaction,
and the pack charged to 100% every time. Every remaining explanation lived
inside the EC, where no probe from Linux can see.

An EC firmware image is the inside. Eluktronics ship theirs as a plain file in
the EFI update package -- `125.ELUK` for the G2 -- and it is not compressed or
encrypted (entropy 5.6-6.9 across the G2 image), so the code is readable.

WHAT IT LOOKS FOR

On an 8051, extended RAM is reached through the data pointer, and loading it
with a constant is a three-byte instruction:

    90 hi lo    MOV DPTR,#addr

So every place the firmware touches 0x07A6 begins with the bytes 90 07 A6.
Counting and locating those is enough to answer "does this build contain code
that reads the charging profile at all", which is the question.

WHAT IT IS FOR

Comparing two images. A reference count in isolation means little; G1 versus G2
means a great deal, because the two are consecutive Uniwill projects -- 0x19 and
0x1A -- built from the same source tree one iteration apart. If the G2 image
carries the capacity-versus-threshold comparison and the profile branch and the
G1 image does not, the feature was never implemented for that build and no
choice of write door was ever going to matter.

    python3 ec_image_scan.py 125.ELUK
    python3 ec_image_scan.py 117.ELUK --compare 125.ELUK

This reads files. It does not flash anything, and it cannot: cross-flashing an
EC image between projects is how a laptop stops POSTing, with no recovery short
of a clip on the flash part. The vendor's own README says the same.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter

# MOV DPTR,#imm16
LOAD_DPTR = 0x90

# Registers this project has mapped, with what a hit would mean. Keeping the
# "why it matters" here rather than in the output means a future reader does not
# have to reconstruct a month of argument from a number.
REGISTERS = {
    0x0740: "PROJECT_ID -- the build's own identity",
    0x0741: "AP_OEM; bit 0 = ENABLE_MANUAL_CTRL (fans), bit 2 undocumented",
    0x0742: "SUPPORT_5",
    0x078E: "capability byte; bit 3 gates the charging profiles",
    0x07A6: "charging profile, bits 5:4 (also touchpad bit 6)",
    0x07B9: "charge threshold, bits 6:0, + CHARGE_CTRL_REACHED bit 7",
    0x07C5: "universal fan ctrl / SPLIT_TABLES",
    0x07C6: "AP_OEM_6 -- fan enable, ERM, full-24h",
    0x07CD: "threshold readback slot",
    0x0783: "PL1 watts",
    0x0784: "PL2 watts",
    0x0490: "battery status; bits 0 and 2 gate the charge ceiling",
    0x0497: "charge-limit-mode flag",
    0x04A6: "cycle count, low byte",
    0x04AB: "battery capacity percent",
}

# Byte patterns that identify a decision rather than an access. A reference to
# 0x07A6 proves the address is known to the build; masking it with 0x30 proves
# the build cares which profile is selected.
#
# Each is (name, regex over the bytes that FOLLOW the DPTR load, why).
SEQUENCES = {
    0x07A6: [
        ("profile compared against STATIONARY",
         rb"\xe0\x54\x30\x64\x20",
         "MOVX A,@DPTR / ANL A,#30h / XRL A,#20h -- isolate bits 5:4 and test "
         "for 2. The profile is not merely stored, it is branched on."),
        ("profile compared against BALANCED",
         rb"\xe0\x54\x30\x64\x10",
         "same, testing for 1."),
        ("profile bits cleared",
         rb"\xe0\x54\xcf\xf0",
         "ANL A,#0CFh -- forcing the profile back to high_capacity, which is "
         "what the capability gate does when the feature is unsupported."),
    ],
    0x07B9: [
        ("CHARGE_CTRL_REACHED set",
         rb"\xe0\x40.\x44\x80\xf0",
         "MOVX A,@DPTR / JC / ORL A,#80h -- arming bit 7 on a carry, i.e. as "
         "the result of a comparison. This is the bit that has never armed on "
         "the G1 at any capacity, including 100%."),
        ("CHARGE_CTRL_REACHED cleared",
         rb"\xe0\x54\x7f\xf0",
         "ANL A,#7Fh -- the matching disarm."),
    ],
    0x04AB: [
        ("capacity read then subtracted",
         rb"\xe0\xc3\x9b",
         "MOVX A,@DPTR / CLR C / SUBB A,R3 -- capacity minus a register, which "
         "is the comparison a percentage ceiling is made of."),
    ],
    0x0490: [
        ("status bit 0 read as a gate",
         rb"\xe0\x54\x01\x22",
         "MOVX A,@DPTR / ANL A,#01h / RET -- an accessor returning bit 0, and "
         "the first of the two guards in front of the capacity comparison. It "
         "leaves DPTR on 0x0490, which is why the caller's next MOVX reads the "
         "same register to test bit 2."),
        ("status bit 2 read as a gate",
         rb"\xe0\x54\x04\x22",
         "the second guard. Both bits read 1 on this machine while charging, "
         "so neither guard is what stops the ceiling."),
    ],
    0x078E: [
        ("capability bit 3 set unconditionally",
         rb"\xe0\x44\x08\x12",
         "ORL A,#08h then LCALL to a callee beginning with MOVX @DPTR,A -- so "
         "the capability bit is WRITTEN BACK, not tested. Previously "
         "mislabelled here as a gate. The EC advertises charging-profile "
         "support unconditionally, which is why tuxedo's "
         "uw_has_charging_profile() reading this bit finds it set on a machine "
         "where the profiles do nothing. What the caller then branches on is "
         "0x0741 bit 0, ENABLE_MANUAL_CTRL -- a fan flag -- on a path that "
         "goes on to clear three more unrelated locations. A restore-defaults "
         "routine, not a feature gate. No gate has been found."),
    ],
    0x0741: [
        ("ENABLE_MANUAL_CTRL bit 0 read",
         rb"\xe0\x54\x01\x22",
         "MOVX A,@DPTR / ANL A,#01h / RET. Bit 0 is ENABLE_MANUAL_CTRL -- "
         "manual FAN control, per uniwill-acpi.c, which this project's own fan "
         "code toggles. This file briefly called it the charging-profile "
         "enable because one caller checks it and then clears the profile "
         "bits; reading three instructions further shows that caller going on "
         "to clear 0x09C7, 0x09C8 and 0x09C9 as well, which makes it a "
         "restore-defaults path with the profile as one item on a list."),
    ],
}


def entropy(b: bytes) -> float:
    if not b:
        return 0.0
    c = Counter(b)
    return -sum((n / len(b)) * math.log2(n / len(b)) for n in c.values())


def find_loads(data: bytes, addr: int) -> list[int]:
    """Every offset where the image loads DPTR with this address."""
    pat = bytes([LOAD_DPTR, (addr >> 8) & 0xFF, addr & 0xFF])
    out, i = [], data.find(pat)
    while i != -1:
        out.append(i)
        i = data.find(pat, i + 1)
    return out


def find_sequences(data: bytes, addr: int) -> list[tuple[str, int, str]]:
    """Decisions, not accesses. Returns (name, offset, why) per hit."""
    hits = []
    for off in find_loads(data, addr):
        tail = data[off + 3:off + 3 + 16]
        for name, pat, why in SEQUENCES.get(addr, []):
            if re.match(pat, tail):
                hits.append((name, off, why))
    return hits


def project_id(data: bytes) -> int | None:
    """The id the build writes into 0x0740 at init: 90 07 40 / 74 xx / F0."""
    for off in find_loads(data, 0x0740):
        if data[off + 3] == 0x74 and data[off + 5] == 0xF0:
            return data[off + 4]
    return None


def scan(data: bytes) -> dict:
    return {
        "size": len(data),
        "entropy": entropy(data),
        "project_id": project_id(data),
        "strings": [m.group().decode() for m in re.finditer(rb"[ -~]{6,}", data)
                    if b"ITE" in m.group()][:4],
        "loads": {a: find_loads(data, a) for a in REGISTERS},
        "sequences": {a: find_sequences(data, a) for a in SEQUENCES},
    }


def report(r: dict, name: str) -> None:
    print(f"=== {name} ===")
    print(f"  {r['size']} bytes, entropy {r['entropy']:.2f}", end="")
    if r["entropy"] > 7.5:
        print("  -- compressed or encrypted; byte scanning will find nothing")
    else:
        print("  -- plain code")
    pid = r["project_id"]
    print(f"  PROJECT_ID written at init: "
          f"{'0x%02X' % pid if pid is not None else 'not found'}")
    for s in r["strings"]:
        print(f"  {s.strip()!r}")

    print("\n  register references")
    for addr, note in sorted(REGISTERS.items()):
        hits = r["loads"][addr]
        print(f"    0x{addr:04X}  {len(hits):3d}  {note}")

    print("\n  decisions")
    any_hit = False
    for addr in sorted(SEQUENCES):
        for nm, off, _why in r["sequences"][addr]:
            any_hit = True
            print(f"    0x{addr:04X} @0x{off:05X}  {nm}")
    if not any_hit:
        print("    none found")


def compare(a: dict, b: dict, na: str, nb: str) -> int:
    print(f"\n=== {na} vs {nb} ===")
    pa, pb = a["project_id"], b["project_id"]
    print(f"  PROJECT_ID  {'0x%02X' % pa if pa is not None else '?':>6}  "
          f"{'0x%02X' % pb if pb is not None else '?':>6}")

    print(f"\n  {'register':>10} {na[:12]:>13} {nb[:12]:>13}")
    for addr, note in sorted(REGISTERS.items()):
        ca, cb = len(a["loads"][addr]), len(b["loads"][addr])
        flag = "  <-- only one side" if (ca == 0) != (cb == 0) else ""
        print(f"  0x{addr:04X}     {ca:>13} {cb:>13}{flag}")

    print("\n  decisions")
    missing = []
    for addr in sorted(SEQUENCES):
        for nm, _pat, why in SEQUENCES[addr]:
            ha = any(h[0] == nm for h in a["sequences"][addr])
            hb = any(h[0] == nm for h in b["sequences"][addr])
            mark = "both" if ha and hb else (na if ha else (nb if hb else "NEITHER"))
            print(f"    0x{addr:04X}  {nm:<42} {mark}")
            if ha != hb:
                missing.append((nm, na if hb else nb, why))

    if missing:
        print("\n  Present in one build and not the other:\n")
        for nm, absent_from, why in missing:
            print(f"    {nm}\n      absent from {absent_from}\n      {why}\n")
        print("  A sequence missing from one image is the strongest evidence\n"
              "  available that the feature was never implemented in that\n"
              "  build -- which no amount of probing from the OS could show.")
    else:
        print("\n  Both builds contain the same decisions. If the behaviour\n"
              "  still differs, the cause is in the data these paths act on,\n"
              "  not in whether the code exists.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan an ITE EC image. Read-only.")
    ap.add_argument("image")
    ap.add_argument("--compare", metavar="OTHER")
    ap.add_argument("--why", action="store_true",
                    help="explain what each decision pattern means")
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    a = scan(data)
    report(a, args.image.split("/")[-1])

    if args.why:
        print("\n=== what the patterns mean ===")
        for addr in sorted(SEQUENCES):
            for nm, _pat, why in SEQUENCES[addr]:
                print(f"\n  0x{addr:04X}  {nm}\n    {why}")

    if args.compare:
        b = scan(open(args.compare, "rb").read())
        print()
        report(b, args.compare.split("/")[-1])
        return compare(a, b, args.image.split("/")[-1],
                       args.compare.split("/")[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
