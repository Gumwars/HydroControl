#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
lb_set_color.py — Static color on the ITE 8233 lightbar (048d:7001).

Protocol verified against tuxedo-drivers ite_8291_lb.c + OpenRGB MR 3166
(same PID 0x7001, tested on real hardware):
    [0x14, 0x00, 0x01, R, G, B, 0x00, 0x00]      set palette slot 1
    [0x08, 0x22, 0x01, 0x01, bri, 0x01, 0, 0]    static, brightness 0-100
    [0x14, 0x00, 1..8, R, G, B, 0x00, 0x00]      set all eight colour-list slots
    [0x08, 0x22, 0x02, speed, bri, 0x08, 0, 0]   breathing (speed 1-10)
Off: 12 00 03 .. -> 08 05 .. -> 08 01 .. -> 1A .. 01

Usage:
    sudo python3 lb_set_color.py [--test]
    sudo python3 lb_set_color.py <RRGGBB> [brightness]
    sudo python3 lb_set_color.py --breathe [speed] [brightness] [RRGGBB]
    sudo python3 lb_set_color.py --off
"""

import array
import fcntl
import glob
import os
import sys
import time

HIDIOCSFEATURE_9 = 0xC0094806
LOG = "/home/gumwars/HydroControl/lb_set_color_log.json"

BRI_MAX = 100


def find_lightbar() -> str:
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            blob = open(os.path.join(p, "device", "uevent")).read().upper()
        except OSError:
            continue
        if "0000048D:00007001" in blob:
            return "/dev/" + os.path.basename(p)
    raise SystemExit("ITE 8233 lightbar (048d:7001) not found")


def ctrl(dev: str, pkt8: bytes) -> None:
    """Send an 8-byte control feature report (report-id 0 prefix)."""
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")
    fd = os.open(dev, os.O_RDWR)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
    finally:
        os.close(fd)


def set_color(dev: str, r, g, b, brightness=BRI_MAX) -> None:
    ctrl(dev, bytes([0x14, 0x00, 0x01, r, g, b, 0x00, 0x00]))
    ctrl(dev, bytes([0x08, 0x22, 0x01, 0x01, brightness, 0x01, 0x00, 0x00]))


def breathe(dev: str, speed=5, brightness=BRI_MAX, rgb=None) -> None:
    """Pulse the bar. Colour source 0x08 (the colour list), not 0x01.

    Under source 0x01 mode 0x02 renders as a solid unchanging colour and does
    not animate at all. Writing the same colour to all eight list slots
    breathes in that one colour instead of cycling eight.
    """
    if rgb is not None:
        for slot in range(1, 9):
            ctrl(dev, bytes([0x14, 0x00, slot, rgb[0], rgb[1], rgb[2], 0x00, 0x00]))
    ctrl(dev, bytes([0x08, 0x22, 0x02, speed, brightness, 0x08, 0x00, 0x00]))


def off(dev: str) -> None:
    ctrl(dev, bytes([0x12, 0x00, 0x03, 0, 0, 0, 0, 0]))
    ctrl(dev, bytes([0x08, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
    ctrl(dev, bytes([0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
    ctrl(dev, bytes([0x1A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]))


def test(dev: str) -> None:
    colors = [("RED", 0xFF, 0, 0), ("GREEN", 0, 0xFF, 0), ("BLUE", 0, 0, 0xFF),
              ("CYAN", 0, 0xFF, 0xFF), ("WHITE", 0xFF, 0xFF, 0xFF)]
    print("Watching the bar, answer per colour:  s=that colour  o=off/dark  c=cycling  ?=other")
    for name, r, g, b in colors:
        set_color(dev, r, g, b, BRI_MAX)
        time.sleep(1.0)
        try:
            obs = input(f"  {name:6s}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            obs = "aborted"
        if obs == "s":
            print(f"  *** HIT: static {name} works ***")
            return
    print("All solid colours done. Now breathing (should pulse RED):")
    breathe(dev, 5, BRI_MAX, (0xFF, 0, 0))
    time.sleep(2.0)
    input("  breathing?  [s=yes working / ?=describe]: ").strip()
    off(dev)
    print("Off sequence sent. Bar should be dark.")
    input("  off?  [s=yes / ?=describe]: ").strip()
    set_color(dev, 0xFF, 0xFF, 0xFF, BRI_MAX)  # restore on state
    print("Restored to solid white @100.")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_set_color.py)")
    dev = find_lightbar()
    if "--test" in sys.argv:
        test(dev)
        return
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--off" in sys.argv:
        off(dev)
        print("off")
    elif "--breathe" in sys.argv:
        speed = int(args[0]) if args else 5
        bri = int(args[1]) if len(args) > 1 else BRI_MAX
        rgb = None
        if len(args) > 2:
            h = args[2].lstrip("#")
            rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        breathe(dev, speed, bri, rgb)
        print(f"breathing speed={speed} bri={bri}"
              + (f" colour=#{args[2].lstrip('#')}" if rgb else ""))
    elif args:
        h = args[0].lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        bri = int(args[1]) if len(args) > 1 else BRI_MAX
        set_color(dev, r, g, b, bri)
        print(f"color #{h} bri={bri}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
