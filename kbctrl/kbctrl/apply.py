#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
kbctrl.apply — One-shot profile restore for use by systemd.

Loads ~/.config/kbctrl/profile.json and applies the saved state to hardware.
Run as root (required for hidraw access).
"""

from __future__ import annotations

import sys


def main() -> None:
    from .config import decode_key_colors, load_profile
    from .hardware import HardwareDriver, lb_color, lb_set

    profile = load_profile()
    if not profile:
        print("kbctrl-apply: no profile found, nothing to restore")
        sys.exit(0)

    hw = HardwareDriver()
    if not hw.connected():
        print(f"kbctrl-apply: hardware not available — {hw.error}", file=sys.stderr)
        sys.exit(1)

    # Restore brightness + per-key colours
    key_colors = decode_key_colors(profile.get("key_colors", {}))
    brightness = profile.get("brightness", 25)

    if key_colors:
        hw.apply_per_key(key_colors, brightness=brightness)
        print(f"kbctrl-apply: restored {len(key_colors)} key colours at brightness {brightness}")
    else:
        # Fallback: just apply brightness
        hw.set_brightness(brightness)
        print(f"kbctrl-apply: set brightness to {brightness}")

    # Restore lightbar (color if set, else brightness of last color)
    lb = profile.get("lightbar", {})
    if lb:
        level = lb.get("brightness", 25)
        color = lb.get("color")
        if color:
            err = lb_color(color[0], color[1], color[2], level)
            if err:
                print(f"kbctrl-apply: lightbar error: {err}", file=sys.stderr)
            else:
                print(f"kbctrl-apply: lightbar color set at brightness {level}")
        else:
            err = lb_set(level)
            if err:
                print(f"kbctrl-apply: lightbar error: {err}", file=sys.stderr)
            else:
                print(f"kbctrl-apply: lightbar brightness set to {level}")

    print("kbctrl-apply: done")


if __name__ == "__main__":
    main()
