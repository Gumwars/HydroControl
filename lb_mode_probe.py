#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
lb_mode_probe.py — confirm which chin bar effects the ITE 8233 actually runs.

tuxedo-drivers implements only mono for PID 0x7001; breathe/wave/clash/catchup/
flash all return -ENOSYS there. Their mode codes are known from the 0x7000 and
0x6010 tables, so the codes are almost certainly right, but nothing has ever
observed them on this bar. This asks the bar, one mode at a time, and prints a
table you can paste back.

Two things are under test:

  1. Does the mode do anything distinguishable from static?
  2. Does it take the colour from palette slot 1?  Each mode is shown twice,
     red then blue -- if the bar changes colour between the two, the palette
     write is being honoured and the UI colour swatch is meaningful.

    sudo python3 lb_mode_probe.py

Nothing here is persistent: the bar is volatile, and a power cycle clears it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kbctrl"))

import kbctrl.hardware as h   # noqa: E402

RED, BLUE = (0xFF, 0x00, 0x00), (0x00, 0x40, 0xFF)
HOLD = 4          # seconds the operator gets to look at each step

MODES = list(h.LB_MODES)          # flash is gone: probed off at every source/direction


def ask(prompt: str, options: str) -> str:
    while True:
        a = input(f"    {prompt} [{options}] ").strip().lower()
        if a and a[0] in options:
            return a[0]


# Round 2: the two modes that did not behave on the first pass. Upstream drives
# breathe and flash from the 8-entry colour LIST (source 0x08), not from palette
# slot 1, and flash carries a direction byte -- so "does nothing" may just mean
# "asked with the wrong colour source".
VARIANTS = [
    ("breathing, source 0x00 (the original packet, no palette write)",
     dict(mode="breathing", source=0x00, rgb=None), False),
    ("breathing, source 0x08 (8-colour list)",
     dict(mode="breathing", source=h.LB_SOURCE_LIST, rgb=None), True),
    ("flash, source 0x08, direction 0x01 (right)",
     dict(mode="flash", source=h.LB_SOURCE_LIST, rgb=None, direction=0x01), True),
    ("flash, source 0x08, direction 0x02 (left)",
     dict(mode="flash", source=h.LB_SOURCE_LIST, rgb=None, direction=0x02), True),
    ("flash, source 0x01, direction 0x01 (right, single colour)",
     dict(mode="flash", source=h.LB_SOURCE_PALETTE, rgb=RED, direction=0x01), False),
]

LIST_COLOURS = [(0xFF, 0, 0), (0xFF, 0x60, 0), (0xFF, 0xFF, 0), (0, 0xFF, 0),
                (0, 0xFF, 0xFF), (0, 0x40, 0xFF), (0x80, 0, 0xFF), (0xFF, 0, 0xFF)]


def probe_variants() -> int:
    print("Round 2 — breathing and flash, driven from the colour list.\n")
    results = {}
    for label, kwargs, needs_list in VARIANTS:
        print(f"--- {label}")
        if needs_list:
            for i, c in enumerate(LIST_COLOURS, start=1):
                h.lb_palette(i, *c)
        err = h.lb_effect(brightness=100, speed=5, **kwargs)
        if err:
            print(f"    write failed: {err}\n")
            results[label] = f"write failed: {err}"
            continue
        os.system(f"sleep {HOLD + 3}")
        a = ask("bar is: (a)nimated, (s)tatic-lit, or (o)ff?", "aso")
        results[label] = {"a": "ANIMATED", "s": "static lit", "o": "off"}[a]
        print()

    h.lb_off()
    print("\n=== paste this back ===")
    for label, _, _ in VARIANTS:
        print(f"  {results.get(label, 'skipped'):<12}  {label}")
    return 0


def main() -> int:
    if os.geteuid() != 0:
        print("needs root to write /dev/hidraw*: sudo python3 lb_mode_probe.py")
        return 1

    dev = h.find_lightbar()
    if not dev:
        print("chin bar (048d:7001) not found")
        return 1
    print(f"chin bar at {dev}\n")
    if "--variants" in sys.argv:
        return probe_variants()
    print("Watch the bar under the front lip. For each mode you will see it in")
    print(f"red for ~{HOLD}s, then blue for ~{HOLD}s.\n")

    results = {}
    for mode in MODES:
        print(f"--- {mode}  (0x08 {h.LB_VARIANT:02X} {h.LB_MODES[mode][0]:02X} ...)")
        for label, colour in (("red", RED), ("blue", BLUE)):
            err = h.lb_effect(mode, speed=5, brightness=100, rgb=colour)
            if err:
                print(f"    write failed: {err}")
                results[mode] = f"write failed: {err}"
                break
            print(f"    showing {label} ...")
            os.system(f"sleep {HOLD}")
        else:
            animated = ask("did it animate (move/pulse), or sit still?", "as")
            coloured = ask("did the colour change from red to blue?", "yn")
            results[mode] = (("animated" if animated == "a" else "static") +
                             ", colour " + ("follows palette" if coloured == "y"
                                            else "IGNORED"))
        print()

    h.lb_off()
    print("\n=== paste this back ===")
    width = max(len(m) for m in MODES)
    for mode in MODES:
        print(f"  {mode:<{width}}  {results.get(mode, 'skipped')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
