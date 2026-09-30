#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
kb_identity.py -- which keyboard panel is this, and does the vendor's white
balance apply to it? READ-ONLY.

Control Center runs requested colours through a lookup (WKDColor.cheatRGB_*)
before sending them, but only for some device types. It picks the type from
the controller's firmware version: usage page 0xFF02 with the high byte at
0x20 gives type 21, HIDKeyboard3, which is the table hydroc.rgb.CHEAT_RGB
carries -- white FF FF FF sent as 7D FF B9.

Applying that table to a panel it is not for makes colours worse, not better,
so the byte decides whether the correction belongs on this machine.

    sudo python3 kb_identity.py

WHY NOT THE STOCK CLI

`ite8291r3-ctl query --fw-version` reports "no suitable device found" here.
The library matches PRODUCT_IDS = [0x6004, 0x6006, 0xCE00] and this keyboard
is 048d:600b, so its own finder never sees it. kbctrl builds the handle
directly, which is why the rest of this project can talk to it at all.

This reads. It sends GET_FW_VERSION and nothing else, sets no colour, writes
no flash, and touches no EC register.
"""

from __future__ import annotations

import sys

# Type 21 is HIDKeyboard3, the one CHEAT_RGB was read from.
HIDKEYBOARD3_VER_HIGH = 0x20


def main() -> int:
    # kbctrl is a nested package: kbctrl/kbctrl/hardware.py, so the path
    # entry is the OUTER kbctrl directory, not the repository root. Importing
    # hydroc.rgb performs that insertion -- it is the same resolution the
    # daemon uses, including the HYDROC_KBCTRL_PATH override, and duplicating
    # it here would be a second thing to keep in step.
    try:
        from hydroc import rgb as _rgb            # noqa: F401  (sys.path)
        from kbctrl.hardware import HardwareDriver
    except ImportError as e:
        raise SystemExit(
            f"cannot import kbctrl: {e}\n"
            "run from the repository root, e.g.\n"
            "  cd /path/to/HydroControl && sudo python3 kb_identity.py")

    drv = HardwareDriver()
    if not drv.connected():
        raise SystemExit(
            "keyboard not reachable. If hydroc-server is running it may hold "
            "the handle:\n  sudo systemctl stop hydroc-server.service\n"
            "...then run this, then start it again.")

    try:
        ver = drv.handle.get_fw_version()
    except Exception as e:
        raise SystemExit(f"GET_FW_VERSION failed: {e}")

    high, low, test, customer = ver
    print(f"firmware  {high}.{low}.{test}.{customer}"
          f"   (high = 0x{high:02X})")

    if high == HIDKEYBOARD3_VER_HIGH:
        print("\n  Type 21 (HIDKeyboard3).")
        print("  The vendor's white balance APPLIES to this panel, and")
        print("  hydroc.rgb.CHEAT_RGB is the right table for it.")
        print("  If white still looks wrong, the table is not the problem.")
    else:
        print(f"\n  Not the 0x20 that selects HIDKeyboard3 (this is "
              f"0x{high:02X}).")
        print("  What 0x22 selects is not recorded: the notes describe the")
        print("  table as covering 'type 21/22' but only say 0x20 gives 21.")
        print("  And there is more than one table -- the service picks by")
        print("  LED vendor (Liteon glossy/cloudy/CIE, Everlight/CIE) from")
        print("  EC 0x073D, 0x0742, 0x078E or device firmware -- and we have")
        print("  one variant.")
        print("\n  So hydroc.rgb.CHEAT_RGB is off by default. Turn it on")
        print("  per call with correct=True if you want to compare.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
