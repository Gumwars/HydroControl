# SPDX-License-Identifier: MIT
"""
kbctrl.hardware — ITE 8291 keyboard + ITE 8233 lightbar drivers.

Handles the bcdDevice bypass needed for HYDROC-16 (PID 0x600B) and the
pipx venv path injection required when running under sudo.
"""

from __future__ import annotations

import array
import fcntl
import glob as _glob
import os
import sys

# ── pipx venv path injection (needed under sudo) ─────────────────────────────
# Under sudo, HOME changes and pipx's venv leaves sys.path. Search the invoking
# user's home (SUDO_USER) as well as the current one, so this works for anybody.
# Where to look for ite8291r3-ctl.
#
# $HOME plus ~$SUDO_USER is NOT enough. Under `sudo` SUDO_USER is set and the
# user's pipx venv is found; started by systemd (which is how the desktop app
# starts it, via pkexec) HOME is /root and SUDO_USER is unset, so the same
# machine reports "ite8291r3_ctl not installed" while the library sits happily
# in the user's home. That produced a bug report where the advice -- reinstall
# it -- could never have worked.
#
# Root can read every home, so search them all rather than guessing which user
# installed it.
_homes = [os.path.expanduser("~"), "/root"]
if os.environ.get("SUDO_USER"):
    _homes.append(os.path.expanduser("~" + os.environ["SUDO_USER"]))
_homes += sorted(_glob.glob("/home/*"))
_candidates = []
for _h in dict.fromkeys(_homes):          # de-duplicated, order preserved
    _candidates += _glob.glob(
        os.path.join(_h, ".local/share/pipx/venvs/ite8291r3-ctl/lib/python*/site-packages"))
    # `pip install --user` puts it here instead; root's sys.path has neither.
    _candidates += _glob.glob(
        os.path.join(_h, ".local/lib/python*/site-packages"))
for _p in _candidates:
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from ite8291r3_ctl import ite8291r3 as _ite
    HAS_ITE = True
except ImportError:
    HAS_ITE = False

# USB product IDs we will drive, INDEPENDENT of the installed library's list.
# Upstream ite8291r3-ctl 0.3 ships PRODUCT_IDS = [0x6004, 0xCE00]; the HYDROC-16
# keyboard is 0x600B and is not in it. Deriving the match from _ite.PRODUCT_IDS
# meant the keyboard was only found on a machine whose copy of the library had
# been hand-patched -- everywhere else it reported "keyboard not connected".
# Own the list here so a pristine pip/pipx install works.
KEYBOARD_VENDOR_ID = 0x048D
KEYBOARD_PRODUCT_IDS = (0x600B, 0x6004, 0xCE00)

# ── ITE 8291 keyboard ─────────────────────────────────────────────────────────

EFFECT_PARAMS: dict[str, list[str]] = {
    "breathing": ["speed", "brightness", "color", "save"],
    "wave":      ["speed", "brightness", "direction", "save"],
    "random":    ["speed", "brightness", "color", "reactive", "save"],
    "rainbow":   ["brightness", "save"],
    "ripple":    ["speed", "brightness", "color", "reactive", "save"],
    "marquee":   ["speed", "brightness", "save"],
    "raindrop":  ["speed", "brightness", "color", "save"],
    "aurora":    ["speed", "brightness", "color", "reactive", "save"],
    "fireworks": ["speed", "brightness", "color", "reactive", "save"],
}

EFFECTS = list(EFFECT_PARAMS.keys())
COLOR_NAMES = ["none", "red", "orange", "yellow", "green", "blue", "teal", "purple", "random"]
DIRECTION_NAMES = ["none", "right", "left", "up", "down"]


def ite_version() -> str:
    """Installed ite8291r3-ctl version, best effort. For diagnostics."""
    try:
        from importlib.metadata import version
        return version("ite8291r3-ctl")
    except Exception:
        return "unknown"


def _ite_handle(dev, out_endpoint):
    """Build the library's device handle across its 0.3 / 0.4 API break.

        0.3   ite8291r3(usb_channel(dev, out_endpoint))
        0.4   ite8291r3(dev, out_endpoint, traffic_callback)   -- usb_channel gone

    A tester on 0.4 hit `module 'ite8291r3_ctl.ite8291r3' has no attribute
    'usb_channel'` because pipx installs whatever is newest and the version was
    never pinned. Detect by capability rather than by version string, so a
    future release that restores or renames things still has a chance.

    traffic_callback is a debug hook; upstream guards it with `if
    self.traffic_callback`, so None is safe.
    """
    if hasattr(_ite, "usb_channel"):
        return _ite.ite8291r3(_ite.usb_channel(dev, out_endpoint))
    try:
        return _ite.ite8291r3(dev, out_endpoint, None)
    except TypeError as e:
        raise RuntimeError(
            f"ite8291r3-ctl {ite_version()} has an API this build does not know "
            f"how to drive ({e}). Known good: 0.3 and 0.4. Please report this "
            f"version." ) from e


def _get_ite_device(loc=None):
    """Open the ITE 8291 keyboard controller.

    We never call the library's own `get()`. It matches on its PRODUCT_IDS --
    which contains 0x600B in no released version -- and additionally requires
    bcdDevice == REV_NUMBER, so on this machine it can only ever fail. Worse,
    its signature changed in 0.4 (`get(loc, traffic_callback)`, both required),
    so calling it is a second source of version breakage for no benefit.

    loc: (bus, addr) to target a specific device, or None to auto-detect.
    """
    import usb.core, usb.util  # type: ignore

    if loc:
        bus, addr = loc
        dev = usb.core.find(bus=bus, address=addr)
        where = f"bus {bus} address {addr}"
    else:
        dev = usb.core.find(
            idVendor=KEYBOARD_VENDOR_ID,
            custom_match=lambda d: d.idProduct in KEYBOARD_PRODUCT_IDS,
        )
        where = "048d:600b"

    if not dev:
        raise FileNotFoundError(
            f"ITE keyboard ({where}) not found on the USB bus. "
            "Check: lsusb | grep -i 048d"
        )
    if dev.is_kernel_driver_active(1):
        dev.detach_kernel_driver(1)
    cfg = dev.get_active_configuration()
    out_endpoint = usb.util.find_descriptor(
        cfg[(1, 0)],
        custom_match=lambda e: (
            usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        ),
    )
    return _ite_handle(dev, out_endpoint)


class HardwareDriver:
    """Wraps ite8291r3 handle; all calls are safe even when disconnected."""

    def __init__(self, loc=None):
        self.handle = None
        self.error: str | None = None
        self._try_connect(loc)

    def _try_connect(self, loc=None):
        if not HAS_ITE:
            self.error = "ite8291r3_ctl not installed"
            return
        try:
            self.handle = _get_ite_device(loc)
        except Exception as e:
            self.error = str(e)

    def connected(self) -> bool:
        return self.handle is not None

    def get_brightness(self) -> int:
        if self.handle:
            try:
                return self.handle.get_brightness()
            except Exception:
                pass
        return 25

    def set_brightness(self, value: int) -> None:
        if self.handle:
            self.handle.set_brightness(max(0, min(50, value)))

    def set_effect(self, name: str, speed=5, brightness=25,
                   color_idx=8, direction_idx=1, reactive=False, save=False) -> None:
        if not self.handle:
            return
        eff_cls = _ite.effects.get(name)
        if not eff_cls:
            raise ValueError(f"Unknown effect: {name}")
        params = EFFECT_PARAMS.get(name, [])
        kwargs: dict = {}
        if "speed"      in params: kwargs["speed"]      = speed
        if "brightness" in params: kwargs["brightness"]  = brightness
        if "color"      in params: kwargs["color"]       = color_idx
        if "direction"  in params: kwargs["direction"]   = direction_idx
        if "reactive"   in params: kwargs["reactive"]    = 1 if reactive else 0
        if "save"       in params: kwargs["save"]        = 1 if save else 0
        self.handle.set_effect(eff_cls(**kwargs))

    def apply_per_key(self, key_colors: dict, brightness: int | None = None,
                      save: bool = False) -> None:
        if not self.handle:
            return
        if brightness is not None:
            self.handle.set_brightness(brightness)
        self.handle.set_key_colors(key_colors, save=save)

    def turn_off(self) -> None:
        if self.handle:
            self.handle.turn_off()

    def restore_palette(self) -> None:
        if self.handle:
            self.handle.restore_default_palette()


# ── ITE 8233 lightbar (048d:7001) ─────────────────────────────────────────────
# HIDIOCSFEATURE(9) = 0xC0094806; 8-byte control feature reports.
#
# Protocol hardware-verified on HYDROC-16; matches tuxedo-drivers ite_8291_lb.c
# and OpenRGB MR 3166 (both cover PID 0x7001):
#   [0x14, 0x00, 0x01, R, G, B, 0, 0]      set palette slot 1
#   [0x08, 0x22, 0x01, 0x01, bri, 0x01, 0, 0]  static, brightness 0-100
# Off: 12 00 03 .. -> 08 05 .. -> 08 01 .. -> 1A .. 01
# Setting color/brightness turns the bar on; no commit needed.

HIDIOCSFEATURE_9 = 0xC0094806

# HID_ID field for the ITE 8233 lightbar, as it appears in the hidraw uevent:
#   HID_ID=0003:0000048D:00007001
# The hidraw index is NOT stable -- it depends on USB enumeration order, so a
# different set of plugged-in devices (trackball, headset, mic) shifts it.
# Resolve by VID/PID instead of hardcoding /dev/hidraw1.
LIGHTBAR_HID_ID = "0000048D:00007001"

_lb_device: str | None = None


def find_lightbar(refresh: bool = False) -> str | None:
    """Resolve the lightbar hidraw node by VID/PID. Cached; None if absent."""
    global _lb_device

    if _lb_device is not None and not refresh:
        return _lb_device

    for path in sorted(_glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(path, "device", "uevent")) as fh:
                blob = fh.read().upper()
        except OSError:
            continue
        if LIGHTBAR_HID_ID in blob:
            _lb_device = "/dev/" + os.path.basename(path)
            return _lb_device

    _lb_device = None
    return None


def _lb_ctrl(pkt8: bytes) -> str:
    """Send an 8-byte control feature report. Returns '' on success."""
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")

    # Retry once against a freshly resolved node: the device may have been
    # re-enumerated (suspend/resume, hub reset) since the path was cached.
    for attempt in (0, 1):
        dev = find_lightbar(refresh=bool(attempt))
        if dev is None:
            if attempt:
                return "ITE 8233 lightbar (048d:7001) not found"
            continue
        try:
            fd = os.open(dev, os.O_RDWR)
            try:
                fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
            finally:
                os.close(fd)
            return ""
        except OSError as e:
            if attempt:
                return f"{dev}: {e}"

    return "ITE 8233 lightbar (048d:7001) not found"


def lb_color(red: int, green: int, blue: int, brightness: int = 100) -> str:
    """Set lightbar to a solid color. Brightness 0-100; 0 = off."""
    if brightness <= 0:
        return lb_off()
    b = max(0, min(100, int(brightness)))
    err = _lb_ctrl(bytes([0x14, 0x00, 0x01,
                          red & 0xFF, green & 0xFF, blue & 0xFF, 0x00, 0x00]))
    if err:
        return err
    return _lb_ctrl(bytes([0x08, 0x22, 0x01, 0x01, b, 0x01, 0x00, 0x00]))


# Effect packet layout, from tuxedo-drivers ite_8291_lb.c:
#
#   [0x08, variant, mode, speed, brightness, colour_source, direction, 0x00]
#
#   variant        0x22 for PID 0x7001 (0x02 = 0x6010, 0x21 = 0x7000)
#   mode           0x01 mono   0x02 breathe  0x03 wave
#                  0x04 clash  0x05 catchup  0x11 flash
#   speed          0x01 fastest .. 0x0a slowest; mono passes 0x01
#   colour_source  0x01 = palette slot 1, 0x08 = the 8-entry colour list
#   direction      flash only: 0x00 none, 0x01 right, 0x02 left
#
# Only mono is implemented upstream for 0x7001 -- every other effect hits
# `default: return -ENOSYS` there. The mode codes came from the 0x7000/0x6010
# tables; the table below is what lb_mode_probe.py actually observed on the bar.
LB_VARIANT = 0x22

LB_SOURCE_PALETTE = 0x01     # single colour, from slot 1
LB_SOURCE_LIST    = 0x08     # the 8-entry colour list
LB_LIST_LENGTH    = 8

# mode -> (code, colour source, where the colour comes from)
#
# The colour source is not cosmetic: breathing renders as a solid unchanging
# colour under 0x01 and only animates under 0x08, so it is driven from the
# colour list with every slot set to the same colour -- that breathes in one
# chosen colour instead of cycling eight. Wave and catchup run their own
# colours and ignore whatever is written.
#
# Flash (0x11) is absent deliberately: it was probed at sources 0x01 and 0x08
# and directions 0x00/0x01/0x02, and turns the bar off every time. It exists
# upstream only for PID 0x6010.
LB_MODES = {
    "static":    (0x01, LB_SOURCE_PALETTE, "slot"),
    "breathing": (0x02, LB_SOURCE_LIST,    "list"),
    "wave":      (0x03, LB_SOURCE_PALETTE, None),
    "clash":     (0x04, LB_SOURCE_PALETTE, "slot"),
    "catchup":   (0x05, LB_SOURCE_PALETTE, None),
}


def lb_palette(index: int, red: int, green: int, blue: int) -> str:
    """Write one colour-list slot (1-8). Slot 1 is what mono/single-colour uses."""
    return _lb_ctrl(bytes([0x14, 0x00, index & 0xFF,
                           red & 0xFF, green & 0xFF, blue & 0xFF, 0x00, 0x00]))


def lb_color_list(colors) -> str:
    """Write all eight colour-list slots. Returns '' on success."""
    for i, c in enumerate(colors[:LB_LIST_LENGTH], start=1):
        err = lb_palette(i, *c)
        if err:
            return err
    return ""


def lb_effect(mode: str, speed: int = 5, brightness: int = 100,
              rgb: tuple[int, int, int] | None = None,
              source: int | None = None, direction: int = 0) -> str:
    """Run a lightbar effect. Returns '' on success.

    The colour must be written before the effect packet: the previous breathing
    implementation sent the effect alone, so the bar kept whatever colour it
    already had and the colour swatch appeared to do nothing.

    `source` overrides the mode's colour source, for probing only.
    """
    entry = LB_MODES.get(mode)
    if entry is None:
        return f"unknown lightbar mode {mode!r}"
    code, default_source, colour_from = entry
    src = default_source if source is None else source

    b = max(0, min(100, int(brightness)))
    if b <= 0:
        return lb_off()

    if rgb is not None and colour_from == "slot":
        err = lb_palette(1, *rgb)
        if err:
            return err
    elif rgb is not None and colour_from == "list":
        err = lb_color_list([rgb] * LB_LIST_LENGTH)
        if err:
            return err

    spd = 0x01 if code == 0x01 else max(1, min(10, int(speed)))
    return _lb_ctrl(bytes([0x08, LB_VARIANT, code, spd, b,
                           src & 0xFF, direction & 0xFF, 0x00]))


def lb_set(brightness: int) -> str:
    """Set lightbar brightness (0 = off, 1-100 = static at last set color)."""
    if brightness <= 0:
        return lb_off()
    b = max(0, min(100, int(brightness)))
    return _lb_ctrl(bytes([0x08, 0x22, 0x01, 0x01, b, 0x01, 0x00, 0x00]))


def lb_off() -> str:
    """Turn the lightbar off."""
    for pkt in (
        bytes([0x12, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00]),
        bytes([0x08, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        bytes([0x08, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]),
        bytes([0x1A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]),
    ):
        err = _lb_ctrl(pkt)
        if err:
            return err
    return ""
