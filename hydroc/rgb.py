# SPDX-License-Identifier: MIT
"""
hydroc.rgb — bridge from the UI's visual key IDs to the RGB transports.

Two separate devices, neither reachable through the EC:

    ITE 8291  048d:600b  keyboard  -- libusb, via kbctrl/ite8291r3
    ITE 8233  048d:7001  chin bar  -- hidraw feature reports

The UI addresses keys by *visual* position (`m3_7` = row 3, 8th main key). The
hardware addresses them by *matrix* coordinate, which is a different space
entirely: matrix row 0 is the BOTTOM row, and the columns are sparse because
wide keys leave gaps. This module owns that translation.

Within each visual row the design lists main-block keys left to right, then
numpad keys left to right -- which matches the matrix columns in ascending
order for every row except the bottom one, where the design places the down
arrow between up and right while the matrix puts it at column 18.
"""

from __future__ import annotations

import os
import sys

# kbctrl lives beside this package in the checkout. Resolve relative to this
# file so the tree works wherever it is cloned, and allow an override.
KBCTRL_PATH = os.environ.get(
    "HYDROC_KBCTRL_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kbctrl"))
if os.path.isdir(KBCTRL_PATH) and KBCTRL_PATH not in sys.path:
    sys.path.insert(0, KBCTRL_PATH)

# Visual row index (0 = number row, 4 = bottom row) -> matrix row.
# The function row is handled separately as matrix row 5.
VISUAL_TO_MATRIX_ROW = {0: 4, 1: 3, 2: 2, 3: 1, 4: 0}

# Matrix columns in the order the design lists that row's keys.
COLUMN_ORDER = {
    5: list(range(20)),
    4: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18],
    3: [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    2: [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17],
    1: [0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18],
    # bottom row: down-arrow sits at column 18, after right-arrow at 15
    0: [0, 2, 3, 4, 7, 10, 12, 13, 14, 18, 15, 16, 17],
}

# How many main-block keys each visual row has, so `p<row>_<i>` numpad ids
# continue the same sequence.
MAIN_COUNT = {0: 14, 1: 14, 2: 13, 3: 12, 4: 11}


def key_id_to_matrix(key_id: str) -> tuple[int, int] | None:
    """'m3_7' / 'p0_2' / 'f5'  ->  (matrix_row, matrix_col)."""
    try:
        if key_id.startswith("f"):
            idx = int(key_id[1:])
            return (5, COLUMN_ORDER[5][idx])

        kind, rest = key_id[0], key_id[1:]
        vrow_s, idx_s = rest.split("_")
        vrow, idx = int(vrow_s), int(idx_s)
        mrow = VISUAL_TO_MATRIX_ROW[vrow]
        seq = idx if kind == "m" else MAIN_COUNT[vrow] + idx
        return (mrow, COLUMN_ORDER[mrow][seq])
    except (ValueError, KeyError, IndexError):
        return None


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# --- keyboard ------------------------------------------------------------

_kbd = None


def _keyboard(retry: bool = True):
    """The keyboard handle, reconnecting if the last attempt failed.

    A failed connect must not be cached forever: the daemon starts at boot and
    a driver built during early USB enumeration would report "not connected"
    until the service was restarted, long after the device was ready.
    """
    global _kbd
    from kbctrl.hardware import HardwareDriver
    if _kbd is None or (retry and not _kbd.connected()):
        _kbd = HardwareDriver()
    return _kbd


def keyboard_available() -> tuple[bool, str]:
    try:
        import kbctrl.hardware as h
    except ImportError as e:
        return False, f"kbctrl not importable: {e}"
    if not h.HAS_ITE:
        return False, ("ite8291r3_ctl not importable. Install it as your normal "
                       "user (NOT as root):  pipx install ite8291r3-ctl")
    ver = h.ite_version()
    try:
        kbd = _keyboard()
        if kbd.connected():
            return True, f"ite8291r3-ctl {ver}"
        # Report the actual reason, and the library version with it. The
        # library's API changed between 0.3 and 0.4 and pipx installs whatever
        # is newest, so "which version" is the first question every time.
        err = kbd.error or "keyboard not connected"
        # A permission error from an unprivileged caller is expected, not a
        # fault. Saying "unavailable" here is how someone ends up reinstalling
        # a library that was never the problem.
        if os.geteuid() != 0 and ("denied" in err.lower() or "permission" in err.lower()):
            return False, (f"needs root to open the USB device -- the daemon "
                           f"runs as root, so this is expected here "
                           f"(ite8291r3-ctl {ver})")
        return False, f"{err} (ite8291r3-ctl {ver})"
    except Exception as e:
        return False, f"{e} (ite8291r3-ctl {ver})"


def apply_per_key(colors: dict[str, str], brightness: int | None = None,
                  save: bool = False) -> dict:
    """colors: {visual_key_id: '#RRGGBB'}"""
    mapped, unmapped = {}, []
    for kid, hexcol in colors.items():
        pos = key_id_to_matrix(kid)
        if pos is None:
            unmapped.append(kid)
            continue
        mapped[pos] = hex_to_rgb(hexcol)
    try:
        _keyboard().apply_per_key(mapped, brightness=brightness, save=save)
    except Exception as e:
        return {"ok": False, "error": str(e), "keys": len(mapped)}
    return {"ok": True, "keys": len(mapped), "unmapped": unmapped, "saved": save}


def apply_effect(name: str, speed: int = 5, brightness: int = 25,
                 color_idx: int = 8, direction_idx: int = 1,
                 save: bool = False) -> dict:
    try:
        _keyboard().set_effect(name, speed=speed, brightness=brightness,
                               color_idx=color_idx, direction_idx=direction_idx,
                               save=save)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "effect": name}


def set_brightness(value: int) -> dict:
    try:
        _keyboard().set_brightness(max(0, min(50, int(value))))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "brightness": value}


# --- chin bar ------------------------------------------------------------

def chinbar_available() -> tuple[bool, str]:
    try:
        import kbctrl.hardware as h
    except ImportError as e:
        return False, f"kbctrl not importable: {e}"
    dev = h.find_lightbar()
    return (True, "") if dev else (False, "ITE 8233 (048d:7001) not found")


# Chin bar settings that belong in the saved profile. The bar is volatile like
# the EC -- it comes up dark after a power cycle and reports nothing back, so
# there is no drift to detect, only intent to re-send. read_state() must never
# claim to know these; drift() drops keys the hardware does not report, which is
# what keeps them out.
CHIN_KEYS = ("chin_mode", "chin_color", "chin_brightness", "chin_speed")

CHIN_DEFAULTS = {
    "chin_mode": "static",
    "chin_color": "#8CBF73",
    "chin_brightness": 100,
    "chin_speed": 5,
}


def effect_params() -> dict:
    """Which parameters each keyboard effect actually accepts.

    Served to the UI so it can hide controls an effect ignores, from the one
    table that decides it -- rather than a second copy in JavaScript that drifts
    out of step (see the effect-colour indices, which did exactly that).
    """
    try:
        import kbctrl.hardware as h
        return dict(h.EFFECT_PARAMS)
    except ImportError:
        return {}


def apply_chinbar_profile(profile: dict) -> dict:
    """Re-send saved chin bar intent. Used at boot and after resume."""
    settings = {k: profile.get(k, CHIN_DEFAULTS[k]) for k in CHIN_KEYS}
    ok, why = chinbar_available()
    if not ok:
        return {"ok": False, "error": why, "skipped": True}
    return chinbar(settings["chin_mode"],
                   hexcol=settings["chin_color"],
                   brightness=int(settings["chin_brightness"]),
                   speed=int(settings["chin_speed"]))


def chinbar(mode: str, hexcol: str = "#8CBF73", brightness: int = 100,
            speed: int = 5, direction: int = 0) -> dict:
    """Run a chin bar mode. Unknown modes are refused rather than substituted.

    Silently falling back to static for an unimplemented mode is worse than
    failing: the bar changes colour, so the request looks like it worked, and
    the mode that did nothing is indistinguishable from the one that did.
    """
    import kbctrl.hardware as h
    try:
        if mode == "off":
            err = h.lb_off()
        elif mode in h.LB_MODES:
            err = h.lb_effect(mode, speed=speed, brightness=brightness,
                              rgb=hex_to_rgb(hexcol), direction=direction)
        else:
            return {"ok": False, "mode": mode,
                    "error": f"unknown chin bar mode {mode!r}"}
        return {"ok": not err, "error": err, "mode": mode}
    except Exception as e:
        return {"ok": False, "error": str(e), "mode": mode}
