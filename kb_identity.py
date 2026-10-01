#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
kb_identity.py -- which keyboard panel is this, and does the vendor's white
balance apply to it? READ-ONLY.

Control Center runs requested colours through a lookup (WKDColor.cheatRGB_*)
before sending them, but only for some device types. It picks the type from
the controller's firmware version on usage page 0xFF02, sub-keyed by the EC's
KBID, and switches on nothing else.

hydroc.rgb.CHEAT_RGB is one of those tables, cheatRGB_HIDKeyboard3, which
belongs to types 21/22 -- white FF FF FF sent as 7D FF B9. Applying it to a
panel it is not for makes colours worse, not better, so these two bytes
decide whether the correction belongs on this machine.

Several types, including this one, have no branch at all and are sent raw.
For those the answer is not "we have not found the table yet", it is that
Windows does not correct them either.

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

# Which cheatRGB_* table HID_Set_Color_14H runs each type through. The switch
# is on m_ITE_KB_Type and nothing else -- the LED-vendor fields the notes once
# credited (Liteon glossy/cloudy/CIE, Everlight) are declared in GCUService
# and never read.
#
# A type that is absent here has no branch and falls through to the default,
# which sends raw RGB. That includes 7/8/9/10: the constructor defines them as
# MEZone_3p1ndSeries, and the dispatcher never asks.
TYPE_TABLE = {
    2: "_4Zone", 3: "_4Zone",
    5: "_2ndME", 6: "_2ndME",
    11: "_2p1ndME", 12: "_2p1ndME", 13: "_2p1ndME", 14: "_2p1ndME",
    16: "_HIDLightbar",
    17: "_2p2ndME", 18: "_2p2ndME", 19: "_2p2ndME", 20: "_2p2ndME",
    21: "_HIDKeyboard3", 22: "_HIDKeyboard3",
    23: "_HIDLightbar2", 24: "_HIDLightbar3",
}

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
    tables = {TYPE_TABLE.get(t) for t in types}

    if tables == {"_HIDKeyboard3"}:
        print("\n  HIDKeyboard3. hydroc.rgb.CHEAT_RGB is the table for this")
        print("  panel; pass correct=True to apply it.")
    elif tables == {None}:
        # The answer for this machine. Raw is not a fallback here, it is what
        # Control Center does, so "uncorrected" is the finished state.
        print("\n  This type has no branch in HID_Set_Color_14H: Control")
        print("  Center sends it raw. There is no table to find, and no")
        print("  correction to apply -- uncorrected RGB is correct here.")
        print("  Leave hydroc.rgb.CHEAT_RGB off. It is cheatRGB_HIDKeyboard3,")
        print("  the types 21/22 table, and is the wrong table for this")
        print("  panel; applying it is what turns white purple.")
    else:
        named = "/".join(sorted(t for t in tables if t)) or "?"
        print(f"\n  This type is corrected through cheatRGB{named}, which we")
        print("  have not extracted. hydroc.rgb.CHEAT_RGB is")
        print("  cheatRGB_HIDKeyboard3 and is the wrong table for this")
        print("  panel. It is off by default; leave it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
