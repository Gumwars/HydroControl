#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
ec_disasm.py -- recursive-descent call graph for a banked ITE 8051 EC image.

WHY THIS EXISTS

Four attempts to answer "what calls the charge-ceiling routine" all used byte
scanning: look for 12 hi lo, or 11 xx, and call each hit a reference. On a
variable-length instruction set that is wrong in both directions.

False negatives: searching for LCALL and ACALL misses JZ, JNZ, JC, JNC, CJNE
and DJNZ. The ceiling code is entered by `70 08` -- a JNZ -- so every search
for it reported nothing, in the G1 image AND in the G2 image where the feature
reportedly works. Getting the same empty answer from a build where the code
demonstrably runs should have condemned the method immediately.

False positives: `80 11` is SJMP +17, but a scanner reading each byte as a
potential opcode sees 0x11 and reports an ACALL. That produced a caller chain
that looped back on itself.

The fix is to track instruction boundaries, which means knowing the length of
every opcode and only decoding at addresses reached by following control flow.

BANKING

The image is 256 KB in four 64 KB banks; logical 0x8000-0xFFFF is the banked
window, so file = bank * 0x10000 + logical. Below 0x8000 is common and shared.

Cross-bank calls go through a thunk in common memory:

    90 hi lo    MOV  DPTR,#target
    02 11 xx    LJMP dispatcher        0x1100/0x1114/0x1128/0x113C = bank 0..3

and the dispatcher pushes DPTR and RETs to it, having set the bank in
P1.0-P1.2. So `LCALL <thunk>` is really `call bank:target`, and this resolves
those edges rather than stopping at the thunk.

    python3 ec_disasm.py 117.ELUK --reaches 1:C8BA
    python3 ec_disasm.py 117.ELUK --callers 1:C890
    python3 ec_disasm.py 117.ELUK --compare 125.ELUK --reaches 1:C8BA
"""

from __future__ import annotations

import argparse
import sys

# Instruction length by opcode. The whole point of the file: without this,
# operand bytes get decoded as instructions.
LEN = [1]*256
for _o in range(256):
    lo = _o & 0x0F
    if lo in (0x1,):  LEN[_o] = 2          # ACALL / AJMP
    elif lo in (0x2,): LEN[_o] = 3         # LJMP / LCALL and friends
for _o, _n in {
    0x00:1, 0x02:3, 0x10:3, 0x12:3, 0x20:3, 0x22:1, 0x30:3, 0x32:1,
    0x40:2, 0x50:2, 0x60:2, 0x70:2, 0x80:2, 0x73:1, 0x83:1, 0x93:1,
    0x05:2, 0x15:2, 0x25:2, 0x35:2, 0x45:2, 0x55:2, 0x65:2, 0x75:3,
    0x24:2, 0x34:2, 0x44:2, 0x54:2, 0x64:2, 0x74:2, 0x94:2, 0xB4:3, 0xB5:3,
    0x42:2, 0x43:3, 0x52:2, 0x53:3, 0x62:2, 0x63:3, 0x72:2, 0x82:2,
    0x85:3, 0x90:3, 0x92:2, 0xA0:2, 0xA2:2, 0xB0:2, 0xB2:2,
    0xC0:2, 0xC2:2, 0xD0:2, 0xD2:2, 0xD5:3, 0x95:2, 0xA3:1, 0xA4:1,
    0xC3:1, 0xC4:1, 0xD3:1, 0xD4:1, 0xE0:1, 0xE4:1, 0xF0:1, 0xF4:1,
    0xE5:2, 0xF5:2, 0xC5:2, 0x84:1, 0xB3:1, 0x03:1, 0x13:1, 0x23:1, 0x33:1,
    0x04:1, 0x14:1, 0xA5:1, 0x06:1, 0x07:1, 0x16:1, 0x17:1,
}.items():
    LEN[_o] = _n
for _b in range(8):                        # register-operand families
    LEN[0x08+_b] = 1; LEN[0x18+_b] = 1
    LEN[0x28+_b] = 1; LEN[0x38+_b] = 1; LEN[0x48+_b] = 1; LEN[0x58+_b] = 1
    LEN[0x68+_b] = 1; LEN[0x78+_b] = 2; LEN[0x88+_b] = 2; LEN[0x98+_b] = 1
    LEN[0xA8+_b] = 2; LEN[0xB8+_b] = 3; LEN[0xC8+_b] = 1; LEN[0xD8+_b] = 2
    LEN[0xE8+_b] = 1; LEN[0xF8+_b] = 1
for _r in (0, 1):                          # @Ri families
    LEN[0x06+_r]=1; LEN[0x16+_r]=1; LEN[0x26+_r]=1; LEN[0x36+_r]=1
    LEN[0x46+_r]=1; LEN[0x56+_r]=1; LEN[0x66+_r]=1; LEN[0x76+_r]=2
    LEN[0x86+_r]=2; LEN[0x96+_r]=1; LEN[0xA6+_r]=2; LEN[0xB6+_r]=3
    LEN[0xC6+_r]=1; LEN[0xD6+_r]=1; LEN[0xE6+_r]=1; LEN[0xF6+_r]=1
for _p in range(8):                        # ACALL / AJMP pages
    LEN[(_p << 5) | 0x11] = 2
    LEN[(_p << 5) | 0x01] = 2

DISPATCHER = {0x1100: 0, 0x1114: 1, 0x1128: 2, 0x113C: 3}
BANK_SIZE = 0x10000
WINDOW = 0x8000

# rel-offset conditional branches: opcode -> offset position within the insn
CONDITIONAL = {0x40:1, 0x50:1, 0x60:1, 0x70:1, 0x80:1,   # JC JNC JZ JNZ SJMP
               0x10:2, 0x20:2, 0x30:2,                   # JBC JB JNB
               0xB4:2, 0xB5:2, 0xB6:2, 0xB7:2,           # CJNE
               0xD5:2}                                   # DJNZ dir
for _b in range(8):
    CONDITIONAL[0xB8 + _b] = 2                           # CJNE Rn
    CONDITIONAL[0xD8 + _b] = 1                           # DJNZ Rn


def phys(bank: int, addr: int) -> int:
    """Logical address in a bank -> file offset."""
    return addr if addr < WINDOW else bank * BANK_SIZE + addr


class Image:
    def __init__(self, data: bytes):
        self.d = data
        self.thunks = self._find_thunks()

    def _find_thunks(self) -> dict[int, tuple[int, int]]:
        """thunk address -> (bank, target). Common memory only."""
        out = {}
        d = self.d
        for i in range(min(len(d), WINDOW) - 6):
            if d[i] == 0x90 and d[i + 3] == 0x02:
                disp = (d[i + 4] << 8) | d[i + 5]
                if disp in DISPATCHER:
                    out[i] = (DISPATCHER[disp], (d[i + 1] << 8) | d[i + 2])
        return out

    def decode(self, bank: int, addr: int):
        """One instruction -> (length, [(kind, bank, target), ...])."""
        p = phys(bank, addr)
        if p + 3 > len(self.d):
            return None, []
        op = self.d[p]
        n = LEN[op]
        edges = []

        def resolve(kind, tgt):
            # A call into common memory may be a thunk to another bank.
            if tgt in self.thunks and kind == "call":
                b, t = self.thunks[tgt]
                edges.append(("call", b, t))
            else:
                edges.append((kind, bank if tgt >= WINDOW else bank, tgt))

        if op == 0x12:                                    # LCALL
            resolve("call", (self.d[p + 1] << 8) | self.d[p + 2])
        elif op == 0x02:                                  # LJMP
            resolve("jump", (self.d[p + 1] << 8) | self.d[p + 2])
        elif (op & 0x1F) == 0x11:                         # ACALL
            resolve("call", ((addr + 2) & 0xF800) | ((op >> 5) << 8) | self.d[p + 1])
        elif (op & 0x1F) == 0x01:                         # AJMP
            resolve("jump", ((addr + 2) & 0xF800) | ((op >> 5) << 8) | self.d[p + 1])
        elif op in CONDITIONAL:
            off = CONDITIONAL[op]
            rel = self.d[p + off]
            rel = rel - 256 if rel > 127 else rel
            edges.append(("branch", bank, (addr + n + rel) & 0xFFFF))
        return n, edges

    def walk(self, entries):
        """Recursive descent. Returns (visited, call_edges, branch_edges)."""
        seen, calls, branches = set(), {}, {}
        stack = list(entries)
        while stack:
            bank, addr = stack.pop()
            while True:
                key = (bank, addr)
                if key in seen:
                    break
                seen.add(key)
                n, edges = self.decode(bank, addr)
                if n is None:
                    break
                for kind, b, t in edges:
                    if kind == "call":
                        calls.setdefault((b, t), set()).add(key)
                        stack.append((b, t))
                    else:
                        branches.setdefault((b, t), set()).add(key)
                        if kind == "jump":
                            stack.append((b, t))
                        else:
                            stack.append((b, t))
                op = self.d[phys(bank, addr)]
                if op in (0x22, 0x32, 0x02, 0x80) or (op & 0x1F) == 0x01:
                    break                                 # RET/RETI/unconditional
                addr = (addr + n) & 0xFFFF
        return seen, calls, branches


    def sweep(self, bank: int):
        """Linear sweep of one bank's window, honouring instruction lengths.

        Recursive descent is exact but incomplete: this firmware dispatches
        through JMP @A+DPTR jump tables, and the walk dead-ends at every one.
        Measured coverage from the entry points is about a fifth of the image,
        and the region holding the charge-ceiling code is outside it -- in the
        G1 image AND in the G2, which is how we know the walk is at fault
        rather than the code being unreachable.

        A sweep decodes every address in order, stepping by instruction length,
        so operand bytes are never mistaken for opcodes. That is the error that
        made `80 11` (SJMP +17) look like an ACALL. It can mis-synchronise on
        embedded data, but 8051 code re-syncs within a few instructions, and a
        wrong boundary produces a wrong edge rather than a missing one -- the
        opposite failure from descent, and the two together bracket the truth.
        """
        calls, branches = {}, {}
        base = bank * BANK_SIZE
        addr = WINDOW
        while addr < 0x10000:
            if base + addr + 3 > len(self.d):
                break
            n, edges = self.decode(bank, addr)
            if n is None:
                break
            for kind, b, t in edges:
                (calls if kind == "call" else branches).setdefault(
                    (b, t), set()).add((bank, addr))
            addr += n
        return calls, branches


def entry_points(img: Image):
    """Reset, interrupt vectors, and every cross-bank thunk target."""
    eps = [(0, 0x0000)] + [(0, v) for v in range(0x0003, 0x0100, 8)]
    eps += [(b, t) for b, t in img.thunks.values()]
    return eps


def parse_loc(s: str) -> tuple[int, int]:
    bank, _, addr = s.partition(":")
    return int(bank), int(addr, 16)


def main() -> int:
    ap = argparse.ArgumentParser(description="Call graph for a banked ITE 8051 image.")
    ap.add_argument("image")
    ap.add_argument("--compare", metavar="OTHER")
    ap.add_argument("--reaches", metavar="BANK:ADDR",
                    help="is this address reached from any entry point?")
    ap.add_argument("--callers", metavar="BANK:ADDR")
    ap.add_argument("--sweep", action="store_true",
                    help="linear sweep instead of descent: complete coverage, "
                         "correct instruction boundaries, but may mis-sync on "
                         "embedded data")
    args = ap.parse_args()

    def analyse(path):
        img = Image(open(path, "rb").read())
        if args.sweep:
            calls, branches, seen = {}, {}, set()
            for b in range(4):
                c, br = img.sweep(b)
                for k, v in c.items():
                    calls.setdefault(k, set()).update(v)
                for k, v in br.items():
                    branches.setdefault(k, set()).update(v)
            return img, seen, calls, branches
        seen, calls, branches = img.walk(entry_points(img))
        return img, seen, calls, branches

    targets = [(args.image, "A")] + ([(args.compare, "B")] if args.compare else [])
    for path, _ in targets:
        img, seen, calls, branches = analyse(path)
        name = path.split("/")[-1]
        print(f"=== {name} ===")
        print(f"   {len(img.thunks)} thunks, {len(seen)} instructions reached, "
              f"{len(calls)} call targets")
        if args.reaches:
            loc = parse_loc(args.reaches)
            if not args.sweep:
                print(f"   bank{loc[0]}:0x{loc[1]:04X} reached: "
                      f"{'YES' if loc in seen else 'NO'}")
            for label, edges in (("called by", calls), ("branched to by", branches)):
                src = edges.get(loc)
                if src:
                    print(f"     {label}: " + ", ".join(
                        f"bank{b}:0x{a:04X}" for b, a in sorted(src)[:6]))
        if args.callers:
            loc = parse_loc(args.callers)
            src = calls.get(loc, set())
            print(f"   bank{loc[0]}:0x{loc[1]:04X} called by {len(src)}: " +
                  ", ".join(f"bank{b}:0x{a:04X}" for b, a in sorted(src)[:8]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
