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
    try:
        from kbctrl.hardware import HardwareDriver
    except ImportError as e:
        raise SystemExit(f"cannot import kbctrl: {e}\n"
                         "run from the repository root")

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
        print(f"\n  NOT type 21 (high byte is 0x{high:02X}, not 0x20).")
        print("  The vendor selects a different table, or none, for this")
        print("  panel. hydroc.rgb.CHEAT_RGB is HIDKeyboard3's and should")
        print("  be turned off here -- applying another panel's white")
        print("  balance makes colours worse, which is consistent with")
        print("  white reading purplish after it was enabled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
