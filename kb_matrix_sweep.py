#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
kb_matrix_sweep.py — light the matrix cells that no key maps to.

The ITE 8291 matrix is 6x21 = 126 cells, but this keyboard only has 101 keys.
That leaves 25 addressable positions doing nothing as far as our layout is
concerned. Most are gaps under wide keys, but some laptops route extra
indicator LEDs -- power button, logo, profile LED -- into spare matrix cells,
and if the profile LED is one of them we can drive its colour directly.

Worth being clear about what this does NOT test. The profile button reaches us
as KEY_F14, but that keycode is synthesised by uniwill-laptop from WMI event
0xB0 -- it never passes through the ITE 8291 keyboard controller at all. The
button is not a matrix key. Its LED being in the matrix is a separate question,
and the only reason to think it might be is that the LED is multi-colour and
something has to drive it.

    sudo python3 kb_matrix_sweep.py           # all spare cells at once
    sudo python3 kb_matrix_sweep.py --each    # one at a time, to localise

Purely visual and volatile: nothing is saved to the controller's flash, and the
keyboard is restored on exit.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "kbctrl"))

from hydroc.rgb import COLUMN_ORDER          # noqa: E402

ROWS, COLS = 6, 21
BRIGHT = (255, 255, 255)
DIM = (0, 0, 0)
RESTORE = (0x8C, 0xBF, 0x73)
HOLD = 3


def spare_cells() -> list[tuple[int, int]]:
    used = {(r, c) for r, cl in COLUMN_ORDER.items() for c in cl}
    return sorted({(r, c) for r in range(ROWS) for c in range(COLS)} - used)


def all_cells(colour) -> dict:
    return {(r, c): colour for r in range(ROWS) for c in range(COLS)}


def ask(prompt: str, options: str) -> str:
    while True:
        a = input(f"    {prompt} [{options}] ").strip().lower()
        if a and a[0] in options:
            return a[0]


def main() -> int:
    if os.geteuid() != 0:
        print("needs root for raw USB: sudo python3 kb_matrix_sweep.py")
        return 1

    from kbctrl.hardware import HardwareDriver
    drv = HardwareDriver()
    if not drv.connected():
        print(f"keyboard not connected: {drv.error}")
        return 1

    spare = spare_cells()
    print(f"{len(spare)} matrix cells map to no key:")
    by_row: dict[int, list[int]] = {}
    for r, c in spare:
        by_row.setdefault(r, []).append(c)
    for r in sorted(by_row):
        print(f"  row {r}: cols {by_row[r]}")

    try:
        print("\nBlanking the keyboard, then lighting only the spare cells white.")
        print("Watch everything EXCEPT the keys: the profile LED beside the power")
        print("button, the power button itself, any logo or edge lighting.\n")
        frame = all_cells(DIM)
        drv.apply_per_key(frame, brightness=50)
        time.sleep(1.0)

        frame.update({cell: BRIGHT for cell in spare})
        drv.apply_per_key(frame, brightness=50)
        time.sleep(HOLD)

        lit = ask("Did ANYTHING light up outside the normal keys?", "yn")
        if lit == "n":
            print("\n  The spare cells drive nothing visible. The profile LED is not")
            print("  on the keyboard matrix -- it is EC-driven, and the only state")
            print("  we can currently reach is white via the 0x0727 latch.")
            return 0

        what = input("    What lit up? ").strip()
        print(f"\n  noted: {what}")

        if "--each" not in sys.argv:
            print("\n  Re-run with --each to find which cell it is.")
            return 0

        print("\nStepping through one cell at a time.\n")
        hits = []
        for r, c in spare:
            frame = all_cells(DIM)
            frame[(r, c)] = BRIGHT
            drv.apply_per_key(frame, brightness=50)
            time.sleep(0.6)
            a = ask(f"row {r} col {c}: lit? ", "yn")
            if a == "y":
                hits.append((r, c))
                print(f"      HIT: ({r}, {c})")
        print("\n=== paste this back ===")
        print(f"  spare cells that drive something: {hits or 'none'}")
        print(f"  described as: {what}")
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            drv.apply_per_key(all_cells(RESTORE), brightness=34)
            print("\nkeyboard restored")
        except Exception as e:
            print(f"\ncould not restore keyboard: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
