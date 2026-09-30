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

# Ver_High -> panel type family, from ILM_RGBKB_Init's ConfirmStart overload.
# Where a family is keyed further, KBID is EC 0x073C.
#
# The trap here cost a wrong table: the notes said "Ver_High == 0x20 gives
# type 21", and this panel reports 0x22 -- which looks adjacent and is not.
# 0x20 is 32 decimal and 0x22 is 34, and they are separate branches.
VER_HIGH_TYPES = {
    0x12: ((5, 6), {}),
    0x13: ((11, 12, 13, 14), {25: 11, 17: 12, 73: 13, 65: 14}),
    0x16: ((11, 12, 13, 14), {25: 11, 17: 12, 73: 13, 65: 14}),
    0x14: ((17, 18, 19, 20), {}),
    0x20: ((21, 22), {}),                       # HIDKeyboard3
    0x22: ((7, 8, 9, 10), {24: 7, 16: 8, 72: 9, 64: 10}),
}

# The types CHEAT_RGB (cheatRGB_HIDKeyboard3) is the table for.
HIDKEYBOARD3_TYPES = (21, 22)

REG_KBID = 0x073C


def _read_kbid() -> int | None:
    """EC 0x073C, which sub-selects within a Ver_High family. Read-only."""
    try:
        from hydroc.ec import EC
        return EC().read(REG_KBID)
    except Exception:
        return None


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
          f"   (high = 0x{high:02X} = {high})")

    family, by_kbid = VER_HIGH_TYPES.get(high, ((), {}))
    if not family:
        print(f"\n  Ver_High 0x{high:02X} is not in the known mapping.")
        print("  hydroc.rgb.CHEAT_RGB is for types 21/22 and should stay off.")
        return 0

    kbid = _read_kbid()
    resolved = by_kbid.get(kbid) if (by_kbid and kbid is not None) else None
    shown = resolved if resolved else "/".join(str(t) for t in family)
    print(f"panel type {shown}"
          + (f"   (KBID 0x{kbid:02X})" if kbid is not None else
             "   (KBID unread -- needs EC access)"))

    types = (resolved,) if resolved else family
    if set(types) & set(HIDKEYBOARD3_TYPES):
        print("\n  HIDKeyboard3. hydroc.rgb.CHEAT_RGB is the table for this")
        print("  panel; pass correct=True to apply it.")
    else:
        print(f"\n  NOT HIDKeyboard3 (that is types "
              f"{'/'.join(str(t) for t in HIDKEYBOARD3_TYPES)}).")
        print("  hydroc.rgb.CHEAT_RGB is cheatRGB_HIDKeyboard3 and is the")
        print("  wrong table for this panel. It is off by default; leave it.")
        print("\n  The right one is whichever cheatRGB_* the service maps")
        print("  this type to -- five keyboard tables exist (_2ndME,")
        print("  _2p1ndME, _2p2ndME, _4Zone, _HIDKeyboard3) and the")
        print("  dispatcher has not been extracted yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
