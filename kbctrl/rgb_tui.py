#!/usr/bin/env python3
"""
rgb_tui.py — Full-featured curses TUI for ITE 8291 RGB keyboard (HYDROC-16)

Tabs:
  F1 = Keys      — per-key color editor with visual keyboard display
  F2 = Effects   — built-in lighting effects with full parameter control
  F3 = Lightbar  — ITE 8233 light bar control

Run with: sudo python3 rgb_tui.py
"""

import curses
import sys
import os
import colorsys
import fcntl
import array
import struct

# ─── Import ite8291r3 (PRODUCT_IDS already patched in ite8291r3.py) ───────────
# pipx installs to the user's home, which sudo can't see. Add the venv
# site-packages explicitly so this works under both regular and root Python.
import glob as _glob
for _p in _glob.glob("/home/gumwars/.local/share/pipx/venvs/ite8291r3-ctl/lib/python*/site-packages"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from ite8291r3_ctl import ite8291r3 as _ite
    HAS_ITE = True
except ImportError:
    HAS_ITE = False

def _get_ite_device(loc=None):
    """
    Find ITE keyboard controller.
    loc: (bus, addr) tuple for explicit targeting, or None for auto-detect.

    ite8291r3.get() also gates on bcdDevice==0x0003 which fails on some
    firmware variants (e.g. HYDROC-16 PID 0x600B).  We fall back to a
    VID/PID-only search when the strict check finds nothing.
    """
    import usb.core, usb.util

    if loc:
        return _ite.get(loc)   # explicit bus/addr bypasses bcdDevice check

    # Standard auto-detect (includes bcdDevice check)
    try:
        return _ite.get()
    except (FileNotFoundError, Exception):
        pass

    # Permissive fallback: match VID + PID, ignore bcdDevice
    dev = usb.core.find(idVendor=_ite.VENDOR_ID,
                        custom_match=lambda d: d.idProduct in _ite.PRODUCT_IDS)
    if not dev:
        raise FileNotFoundError(
            "ITE keyboard not found. Try: sudo python3 rgb_tui.py --device BUS/ADDR\n"
            "  (find BUS/ADDR with: lsusb | grep -i 048d)"
        )

    if dev.is_kernel_driver_active(1):
        dev.detach_kernel_driver(1)

    cfg = dev.get_active_configuration()
    fd_out = usb.util.find_descriptor(
        cfg[(1, 0)],
        custom_match=lambda e: (
            usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
    )
    return _ite.ite8291r3(_ite.usb_channel(dev, fd_out))

# ─── Lightbar (ITE 8233, hidraw1) ────────────────────────────────────────────
# HIDIOCSFEATURE(9) = _IOC(_IOC_WRITE|_IOC_READ, 'H', 0x06, 9)
#   = (3 << 30) | (9 << 16) | (0x48 << 8) | 0x06  = 0xC0094806
# Matches kbctrl.py exactly — the "9" is 1-byte report ID + 8-byte payload.
HIDIOCSFEATURE_9 = 0xC0094806
LIGHTBAR_DEVICE = "/dev/hidraw1"

# Commit packet — required by ITE 8233 to latch staged changes
_LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])

def lightbar_send(pkt8: bytes, commit: bool = True) -> str:
    """
    Send an 8-byte ITE command to the lightbar as a 9-byte feature report
    (report-ID 0x00 prepended), then optionally send the commit packet.
    Returns "" on success, error string on failure.
    """
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b'\x00')
    try:
        fd = os.open(LIGHTBAR_DEVICE, os.O_RDWR)
        try:
            buf = array.array('B', report)
            fcntl.ioctl(fd, HIDIOCSFEATURE_9, buf, True)
            if commit:
                buf2 = array.array('B', bytes([0x00]) + _LB_COMMIT)
                fcntl.ioctl(fd, HIDIOCSFEATURE_9, buf2, True)
        finally:
            os.close(fd)
        return ""
    except OSError as e:
        return str(e)

# Convenience helpers
def lb_off():
    return lightbar_send(bytes([0x09, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))

def lb_brightness(level: int):
    """level 0-50"""
    return lightbar_send(bytes([0x09, 0x02, max(0, min(50, level)),
                                0x00, 0x00, 0x00, 0x00, 0x00]))

def lb_palette(slot: int, r: int, g: int, b: int):
    """Set palette slot (1-7) to an RGB color."""
    return lightbar_send(bytes([0x14, 0x00, slot & 0xFF, r, g, b, 0x00, 0x00]),
                         commit=False)

def lb_effect(effect_id: int, speed: int, brightness: int,
              color_slot: int, direction: int = 0, save: int = 0):
    return lightbar_send(bytes([0x08, 0x02, effect_id & 0xFF,
                                speed & 0xFF, brightness & 0xFF,
                                color_slot & 0xFF, direction & 0xFF,
                                save & 0xFF]))

# ─── Keyboard layout ──────────────────────────────────────────────────────────
# Each key: (label, matrix_row, matrix_col, display_width_chars)
# Based on ITE 8291 6×21 matrix for a standard TKL-style laptop keyboard.

KEYBOARD_ROWS = [
    # Row 0 — Fn / top row + numpad top
    [("Esc",  0, 0, 4), ("F1",  0, 1, 3), ("F2",  0, 2, 3), ("F3",  0, 3, 3),
     ("F4",   0, 4, 3), ("F5",  0, 5, 3), ("F6",  0, 6, 3), ("F7",  0, 7, 3),
     ("F8",   0, 8, 3), ("F9",  0, 9, 3), ("F10", 0,10, 3), ("F11", 0,11, 3),
     ("F12",  0,12, 3), ("Ins", 0,13, 3), ("Del", 0,14, 3),
     ("NmLk", 0,15, 4), ("NP/", 0,16, 4), ("NP*", 0,17, 4), ("NP-", 0,18, 4)],
    # Row 1 — Number row + numpad 7-9
    [("`",    1, 0, 3), ("1",   1, 1, 3), ("2",   1, 2, 3), ("3",   1, 3, 3),
     ("4",    1, 4, 3), ("5",   1, 5, 3), ("6",   1, 6, 3), ("7",   1, 7, 3),
     ("8",    1, 8, 3), ("9",   1, 9, 3), ("0",   1,10, 3), ("-",   1,11, 3),
     ("=",    1,12, 3), ("BkSp",1,13, 6),
     ("NP7",  1,15, 4), ("NP8", 1,16, 4), ("NP9", 1,17, 4), ("NP+", 1,18, 4)],
    # Row 2 — QWERTY + numpad 4-6
    [("Tab",  2, 0, 5), ("Q",   2, 1, 3), ("W",   2, 2, 3), ("E",   2, 3, 3),
     ("R",    2, 4, 3), ("T",   2, 5, 3), ("Y",   2, 6, 3), ("U",   2, 7, 3),
     ("I",    2, 8, 3), ("O",   2, 9, 3), ("P",   2,10, 3), ("[",   2,11, 3),
     ("]",    2,12, 3), ("\\",  2,13, 4),
     ("NP4",  2,15, 4), ("NP5", 2,16, 4), ("NP6", 2,17, 4)],
    # Row 3 — ASDF + numpad 1-3
    [("Caps", 3, 0, 6), ("A",   3, 1, 3), ("S",   3, 2, 3), ("D",   3, 3, 3),
     ("F",    3, 4, 3), ("G",   3, 5, 3), ("H",   3, 6, 3), ("J",   3, 7, 3),
     ("K",    3, 8, 3), ("L",   3, 9, 3), (";",   3,10, 3), ("'",   3,11, 3),
     ("Ent",  3,12, 6),
     ("NP1",  3,15, 4), ("NP2", 3,16, 4), ("NP3", 3,17, 4), ("NPE", 3,18, 4)],
    # Row 4 — ZXCV + numpad 0 and .
    [("Shift",4, 0, 7), ("Z",   4, 1, 3), ("X",   4, 2, 3), ("C",   4, 3, 3),
     ("V",    4, 4, 3), ("B",   4, 5, 3), ("N",   4, 6, 3), ("M",   4, 7, 3),
     (",",    4, 8, 3), (".",   4, 9, 3), ("/",   4,10, 3), ("Shft",4,11, 6),
     ("NP0",  4,15, 5), ("NP.", 4,16, 4)],
    # Row 5 — Modifier row + arrows
    [("Ctrl", 5, 0, 5), ("Win", 5, 1, 4), ("Alt", 5, 2, 4), ("Space",5,3,14),
     ("Alt",  5, 4, 4), ("Fn",  5, 5, 3),
     ("←",    5,15, 4), ("↑",   5,16, 4), ("↓",   5,17, 4), ("→",   5,18, 4)],
]

EFFECTS = ["breathing","wave","random","rainbow","ripple","marquee","raindrop","aurora","fireworks"]

EFFECT_PARAMS = {
    "breathing": ["speed","brightness","color","save"],
    "wave":      ["speed","brightness","direction","save"],
    "random":    ["speed","brightness","color","reactive","save"],
    "rainbow":   ["brightness","save"],
    "ripple":    ["speed","brightness","color","reactive","save"],
    "marquee":   ["speed","brightness","save"],
    "raindrop":  ["speed","brightness","color","save"],
    "aurora":    ["speed","brightness","color","reactive","save"],
    "fireworks": ["speed","brightness","color","reactive","save"],
}

COLOR_NAMES = ["none","red","orange","yellow","green","blue","teal","purple","random"]
DIRECTION_NAMES = ["none","right","left","up","down"]

# ─── Key-matrix loader ────────────────────────────────────────────────────────
# Maps verbose labels from key_matrix.json to short display strings and widths.
_KM_LABELS = {
    "Left Arrow": "←",   "Right Arrow": "→",
    "Up Arrow":   "↑",   "Down Arrow":  "↓",
    "Left Ctrl":  "Ctrl","Right Ctrl":  "Ctrl",
    "Left Alt":   "Alt", "Right Alt":   "Alt",
    "Left Shift": "Shift","Right Shift":"Shift",
    "Caps Lock":  "Caps","Backspace":   "BkSp",
    "Delete":     "Del", "Print Screen":"PrtSc",
    "ScreenCap":  "SnipT","Page Up":    "PgUp",
    "Page Down":  "PgDn","Num Lock":    "NmLk",
    "Num Enter":  "NPEnt","Num +":      "NP+",
    "Num -":      "NP-", "Num *":       "NP*",
    "Num /":      "NP/", "Escape":      "Esc",
    "Num 0":      "NP0", "Num .":       "NP.",
    "Num 1":      "NP1", "Num 2":       "NP2",
    "Num 3":      "NP3", "Num 4":       "NP4",
    "Num 5":      "NP5", "Num 6":       "NP6",
    "Num 7":      "NP7", "Num 8":       "NP8",
    "Num 9":      "NP9", "Super":       "Win",
    "Enter":      "Ent",
}
_KM_WIDTHS = {
    "Space": 12, "Left Shift": 7, "Right Shift": 7,
    "Backspace": 6, "Caps Lock": 6, "Enter": 6,
    "Num Enter": 5, "Tab": 5,
    "Left Ctrl": 5, "Right Ctrl": 5,
    "Left Alt": 5,  "Right Alt": 5,
    "Super": 5,     "Fn": 4,
    "Num 0": 5,     "Escape": 4,
    "Delete": 4,    "Home": 4,     "End": 4,
    "Page Up": 5,   "Page Down": 5,
    "Print Screen": 5, "ScreenCap": 5,
    "Num Lock": 5,  "Num /": 4,
    "Num *": 4,     "Num -": 4,    "Num +": 5,
}

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_MATRIX_PATH = os.path.join(_SCRIPT_DIR, "key_matrix.json")

def _load_key_matrix(path=None):
    """
    Read key_matrix.json produced by the F4 scan and return a KEYBOARD_ROWS-
    compatible list.  The scan stores matrix-row order (row 5 = F-keys at the
    top of the physical keyboard, row 0 = modifiers at the bottom), so we
    reverse the row order for display.
    """
    import json
    if path is None:
        path = KEY_MATRIX_PATH
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    # Group by matrix row
    by_row = {}
    for key_str, label in data.items():
        r, c = map(int, key_str.split(","))
        by_row.setdefault(r, []).append((c, label))

    # Sort each row by column
    for r in by_row:
        by_row[r].sort()

    # Build display rows in visual order (matrix 5→0 = top→bottom on keyboard)
    result = []
    for matrix_row in sorted(by_row.keys(), reverse=True):
        row_keys = []
        for col, label in by_row[matrix_row]:
            display = _KM_LABELS.get(label, label)
            width   = _KM_WIDTHS.get(label, 4)
            row_keys.append((display, matrix_row, col, width))
        result.append(row_keys)
    return result

# Lightbar effect menu: (display_name, effect_id, supports_color)
LB_EFFECTS = [
    ("Off",        None,  False),
    ("Breathing",  0x02,  True),
    ("Wave",       0x03,  False),
    ("Random",     0x04,  True),
    ("Rainbow",    0x05,  False),
    ("Ripple",     0x06,  True),
    ("Raindrop",   0x0A,  True),
    ("Aurora",     0x0E,  True),
    ("Fireworks",  0x11,  True),
]

# ─── Color helpers ────────────────────────────────────────────────────────────

def hsv_to_rgb(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
    return (int(r * 255), int(g * 255), int(b * 255))

def rgb_to_hsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return (round(h * 360), round(s * 100), round(v * 100))

def luminance(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b

def ansi_color_block(r, g, b, text, bold_border=False):
    """Return ANSI true-color background string."""
    tr, tg, tb = (0, 0, 0) if luminance(r, g, b) > 140 else (255, 255, 255)
    return f"\033[48;2;{r};{g};{b}m\033[38;2;{tr};{tg};{tb}m{text}\033[0m"

# ─── Hardware driver ──────────────────────────────────────────────────────────

class HardwareDriver:
    def __init__(self, loc=None):
        self.handle = None
        self.error = None
        self.loc = loc
        self._try_connect()

    def _try_connect(self):
        if not HAS_ITE:
            self.error = "ite8291r3_ctl not installed"
            return
        try:
            self.handle = _get_ite_device(self.loc)
        except FileNotFoundError as e:
            self.error = str(e)
        except Exception as e:
            self.error = str(e)

    def connected(self):
        return self.handle is not None

    def set_brightness(self, value):
        if self.handle:
            self.handle.set_brightness(max(0, min(50, value)))

    def get_brightness(self):
        if self.handle:
            try:
                return self.handle.get_brightness()
            except Exception:
                pass
        return 25

    def set_effect(self, effect_name, speed=5, brightness=25, color_idx=8,
                   direction_idx=1, reactive=False, save=False):
        if not self.handle:
            raise RuntimeError("Not connected to hardware")
        eff = _ite.effects.get(effect_name)
        if not eff:
            raise ValueError(f"Unknown effect: {effect_name}")
        params = EFFECT_PARAMS.get(effect_name, [])
        kwargs = {}
        if "speed" in params:       kwargs["speed"] = speed
        if "brightness" in params:  kwargs["brightness"] = brightness
        if "color" in params:       kwargs["color"] = color_idx
        if "direction" in params:   kwargs["direction"] = direction_idx
        if "reactive" in params:    kwargs["reactive"] = 1 if reactive else 0
        if "save" in params:        kwargs["save"] = 1 if save else 0
        self.handle.set_effect(eff(**kwargs))

    def apply_per_key(self, key_colors, brightness=None, save=False):
        """Send per-key color map to keyboard. key_colors: {(row,col): (r,g,b)}"""
        if not self.handle:
            return
        if brightness is not None:
            self.handle.set_brightness(brightness)
        self.handle.set_key_colors(key_colors, save=save)

    def turn_off(self):
        if self.handle:
            self.handle.turn_off()

    def restore_palette(self):
        if self.handle:
            self.handle.restore_default_palette()

# ─── App state ────────────────────────────────────────────────────────────────

class AppState:
    def __init__(self, hw: HardwareDriver):
        self.hw = hw
        self.tab = 0  # 0=Keys, 1=Effects, 2=Lightbar

        # Per-key colors {(matrix_row, matrix_col): (r,g,b)}
        self.key_colors = {}

        # Keys tab — selected key index into flat key list
        self.keys_flat = []   # populated in draw
        self.key_sel = 0      # index into keys_flat
        self.keys_focus = "keyboard"  # "keyboard" | "h" | "s" | "v" | "r" | "g" | "b" | "brightness"

        # Color picker (HSV, separate from the selected key's actual color)
        self.picker_h = 0
        self.picker_s = 100
        self.picker_v = 100
        self._sync_picker_from_key()

        # Global brightness (0-50)
        self.brightness = hw.get_brightness()

        # Effects tab
        self.eff_idx = 0
        self.eff_speed = 5
        self.eff_brightness = 25
        self.eff_color_idx = 8   # "random" — matches ite8291r3 effect defaults
        self.eff_dir_idx = 1     # "right"
        self.eff_reactive = False
        self.eff_save = False
        self.eff_focus = "list"  # "list"|"speed"|"brightness"|"color"|"direction"|"reactive"|"save"

        # Lightbar tab
        self.lb_r = 0
        self.lb_g = 0
        self.lb_b = 255
        self.lb_focus = "effect"
        self.lb_brightness = 25
        self.lb_speed = 5
        self.lb_effect_idx = 0   # index into LB_EFFECTS

        # Matrix scan (F4 tab)
        self.scan_row = 0
        self.scan_col = 0
        self.scan_input = ""          # key label being typed
        self.scan_map = {}            # {(row,col): label}
        self.scan_done = False

        self.status = ""
        self._set_status_default()

    def _set_status_default(self):
        if not self.hw.connected():
            self.status = f"[NO HW: {self.hw.error}]  q=Quit"
        else:
            self.status = "F1=Keys  F2=Effects  F3=Lightbar  q=Quit  Tab=Focus  Enter=Apply"

    def selected_key_pos(self):
        if not self.keys_flat or self.key_sel >= len(self.keys_flat):
            return None
        return self.keys_flat[self.key_sel][1], self.keys_flat[self.key_sel][2]  # (row, col)

    def _sync_picker_from_key(self):
        pos = self.selected_key_pos() if self.keys_flat else None
        if pos and pos in self.key_colors:
            r, g, b = self.key_colors[pos]
            self.picker_h, self.picker_s, self.picker_v = rgb_to_hsv(r, g, b)
        # else leave picker as-is

    def picker_rgb(self):
        return hsv_to_rgb(self.picker_h, self.picker_s, self.picker_v)

    def apply_picker_to_selected(self):
        pos = self.selected_key_pos()
        if pos:
            self.key_colors[pos] = self.picker_rgb()

    def apply_color_to_all(self):
        r, g, b = self.picker_rgb()
        for (_, row, col, _) in self.keys_flat:
            self.key_colors[(row, col)] = (r, g, b)

# ─── TUI Renderer ─────────────────────────────────────────────────────────────

class TuiApp:
    TAB_NAMES = ["[F1] Keys", "[F2] Effects", "[F3] Lightbar", "[F4] Scan"]

    # curses color pair IDs
    CP_DEFAULT   = 1
    CP_SELECTED  = 2
    CP_HEADER    = 3
    CP_STATUS    = 4
    CP_BORDER    = 5
    CP_INACTIVE  = 6
    CP_GREEN     = 7
    CP_RED       = 8

    def __init__(self, stdscr, device_loc=None):
        self.scr = stdscr
        self.hw = HardwareDriver(loc=device_loc)
        self.state = AppState(self.hw)
        self._init_colors()
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        self.scr.timeout(100)

        # Load key layout from scan file if available, else fall back to defaults
        loaded = _load_key_matrix()
        self.keyboard_rows = loaded if loaded is not None else KEYBOARD_ROWS
        if loaded is not None:
            self.state.status = f"Loaded key layout from {KEY_MATRIX_PATH}"

        # Build flat key list once
        self.state.keys_flat = [
            (label, row, col, width)
            for kb_row in self.keyboard_rows
            for (label, row, col, width) in kb_row
        ]
        # Map (screen_row_offset, screen_col_offset) → key_idx (built during draw)
        self.hitmap = {}
        # Deferred ANSI writes: list of (row, col, ansi_str) to emit after refresh
        self._ansi_queue = []

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(self.CP_DEFAULT,  curses.COLOR_WHITE,   -1)
        curses.init_pair(self.CP_SELECTED, curses.COLOR_BLACK,   curses.COLOR_CYAN)
        curses.init_pair(self.CP_HEADER,   curses.COLOR_BLACK,   curses.COLOR_WHITE)
        curses.init_pair(self.CP_STATUS,   curses.COLOR_BLACK,   curses.COLOR_YELLOW)
        curses.init_pair(self.CP_BORDER,   curses.COLOR_CYAN,    -1)
        curses.init_pair(self.CP_INACTIVE, curses.COLOR_WHITE,   curses.COLOR_BLUE)
        curses.init_pair(self.CP_GREEN,    curses.COLOR_GREEN,   -1)
        curses.init_pair(self.CP_RED,      curses.COLOR_RED,     -1)

    def run(self):
        while True:
            try:
                self._ansi_queue = []
                self.scr.erase()
                h, w = self.scr.getmaxyx()
                self._draw_header(w)
                self._draw_tab_bar(w)
                if not self.hw.connected():
                    self._draw_no_hw(h, w)
                elif self.state.tab == 0:
                    self._draw_keys_tab(h, w)
                elif self.state.tab == 1:
                    self._draw_effects_tab(h, w)
                elif self.state.tab == 2:
                    self._draw_lightbar_tab(h, w)
                elif self.state.tab == 3:
                    self._draw_scan_tab(h, w)
                self._draw_status(h, w)
                # touchwin() marks every cell dirty so refresh() resends ALL cells —
                # including background spaces — which overwrites stale ANSI content
                # left by _flush_ansi() from the previous frame.
                self.scr.touchwin()
                self.scr.refresh()
                # Write ANSI true-color overlays after curses refresh
                self._flush_ansi()

                key = self.scr.getch()
                if key == -1:
                    continue
                self._handle_key(key)
            except KeyboardInterrupt:
                sys.exit(0)
            except curses.error:
                pass  # ignore resize/redraw glitches

    def _flush_ansi(self):
        if not self._ansi_queue:
            return
        buf = []
        for (row, col, text) in self._ansi_queue:
            buf.append(f"\033[{row+1};{col+1}H{text}")
        # Hide cursor during write to avoid flicker
        sys.stdout.write("\033[?25l" + "".join(buf) + "\033[?25h")
        sys.stdout.flush()

    # ── Drawing helpers ────────────────────────────────────────────────────────

    def _safe_addstr(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        try:
            self.scr.addstr(y, x, text[:max(0, w - x)], attr)
        except curses.error:
            pass

    def _safe_addstr_ansi(self, y, x, ansi_text):
        """Queue an ANSI true-color string for deferred writing after curses refresh."""
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        self._ansi_queue.append((y, x, ansi_text))

    def _hline(self, y, x, ch, n, attr=0):
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h:
            return
        n = min(n, w - x)
        try:
            self.scr.hline(y, x, ch, n, attr)
        except curses.error:
            pass

    def _draw_no_hw(self, h, w):
        """Full content-area display when hardware is not connected."""
        err = self.hw.error or "unknown error"
        lines = [
            "",
            "  KEYBOARD NOT CONNECTED",
            "",
            f"  {err}",
            "",
            "  To connect by explicit USB address:",
            "    sudo python3 rgb_tui.py --device BUS/ADDR",
            "",
            "  Find the address with:",
            "    lsusb | grep -i 048d",
            "  Output looks like:  Bus 001 Device 004  →  --device 1/4",
            "",
            "  R = retry connection    q = quit",
        ]
        attr_err  = curses.color_pair(self.CP_RED) | curses.A_BOLD
        attr_norm = curses.color_pair(self.CP_DEFAULT)
        for i, line in enumerate(lines):
            y = 3 + i
            if y >= h - 1:
                break
            attr = attr_err if "NOT CONNECTED" in line or "048d" in line else attr_norm
            self._safe_addstr(y, 0, line, attr)

    def _draw_header(self, w):
        title = " HYDROC-16 RGB Controller "
        if self.hw.connected():
            hw_status = " [HW: OK] "
            attr = curses.color_pair(self.CP_HEADER) | curses.A_BOLD
        else:
            hw_status = f" [NO HW: {self.hw.error or 'unknown'}] "
            attr = curses.color_pair(self.CP_RED) | curses.A_BOLD | curses.A_REVERSE
        line = title + " " * max(0, w - len(title) - len(hw_status)) + hw_status
        self._safe_addstr(0, 0, line[:w], attr)

    def _draw_tab_bar(self, w):
        x = 0
        for i, name in enumerate(self.TAB_NAMES):
            label = f" {name} "
            if i == self.state.tab:
                attr = curses.color_pair(self.CP_SELECTED) | curses.A_BOLD
            else:
                attr = curses.color_pair(self.CP_INACTIVE)
            self._safe_addstr(1, x, label, attr)
            x += len(label) + 1

    def _draw_status(self, h, w):
        msg = self.state.status[:w]
        msg += " " * (w - len(msg))
        self._safe_addstr(h - 1, 0, msg[:w], curses.color_pair(self.CP_STATUS))

    # ── Keys tab ───────────────────────────────────────────────────────────────

    def _draw_keys_tab(self, h, w):
        self.hitmap = {}
        y_start = 3

        self._safe_addstr(y_start, 2, "Keyboard  (Tab=focus, arrows=navigate, Enter=set color, A=all keys)",
                          curses.color_pair(self.CP_BORDER))

        y = y_start + 1
        for kb_row in self.keyboard_rows:
            x = 2
            for ki, (label, row, col, kw) in enumerate(kb_row):
                key_pos = (row, col)
                key_idx = self.state.keys_flat.index((label, row, col, kw)) if (label, row, col, kw) in self.state.keys_flat else -1

                is_selected = (self.state.keys_focus == "keyboard" and
                               key_idx == self.state.key_sel)

                r, g, b = self.state.key_colors.get(key_pos, (30, 30, 30))

                # Pad label to fill key width
                pad = kw - 1
                text = label[:pad].center(pad)

                if is_selected:
                    # Highlight selected key border
                    block = ansi_color_block(r, g, b, f"[{text}]"[:kw])
                else:
                    block = ansi_color_block(r, g, b, f" {text}"[:kw])

                # Record hitmap: map screen cells to key_idx
                for dx in range(kw):
                    self.hitmap[(y, x + dx)] = key_idx

                self._safe_addstr_ansi(y, x, block)
                x += kw

            y += 2  # two rows per keyboard row for spacing

        # Color picker panel
        picker_y = y + 1
        self._draw_color_picker(picker_y, 2, h, w)

    def _draw_color_picker(self, y, x, h, w):
        state = self.state
        r, g, b = state.picker_rgb()

        self._safe_addstr(y, x, "─── Color Picker ───────────────────────────────────",
                          curses.color_pair(self.CP_BORDER))
        y += 1

        # Wide colour swatch
        swatch = ansi_color_block(r, g, b, f"   R:{r:3d}  G:{g:3d}  B:{b:3d}   ")
        self._safe_addstr_ansi(y, x, swatch)
        y += 1

        BAR = 26

        def _slider(label, fname, val, lo, hi, unit=""):
            nonlocal y
            focused = state.keys_focus == fname
            filled = int((val - lo) / max(1, hi - lo) * BAR)
            bar = "█" * filled + "░" * (BAR - filled)
            attr = curses.A_REVERSE if focused else 0
            self._safe_addstr(y, x, f" {label}: ", 0)
            self._safe_addstr(y, x + 4, f"[{bar}] {val:4d}{unit}", attr)
            y += 1

        # HSV sliders
        _slider("H", "h", state.picker_h, 0, 360, "°")
        _slider("S", "s", state.picker_s, 0, 100, "%")
        _slider("V", "v", state.picker_v, 0, 100, "%")

        # Blank line between HSV and RGB
        y += 1

        # RGB sliders  (Tab focuses these; ←→ adjusts in steps of 5)
        _slider("R", "r", r, 0, 255)
        _slider("G", "g", g, 0, 255)
        _slider("B", "b", b, 0, 255)

        y += 1

        # Brightness slider
        _slider("Bri", "brightness", state.brightness, 0, 50)
        y += 1

        self._safe_addstr(y, x,
            " Enter=set key  A=all keys  P=push to keyboard  O=restore palette  Spc=off",
            curses.color_pair(self.CP_BORDER))

    # ── Effects tab ────────────────────────────────────────────────────────────

    def _draw_effects_tab(self, h, w):
        y = 3
        self._safe_addstr(y, 2, "Effects  (Tab=cycle focus, ↑↓=change, Enter=apply, S=save+apply)",
                          curses.color_pair(self.CP_BORDER))
        y += 1

        # Effect list
        list_x = 2
        for i, eff in enumerate(EFFECTS):
            is_sel = i == self.state.eff_idx
            is_focus = self.state.eff_focus == "list"
            if is_sel and is_focus:
                attr = curses.color_pair(self.CP_SELECTED) | curses.A_BOLD
            elif is_sel:
                attr = curses.color_pair(self.CP_SELECTED)
            else:
                attr = curses.color_pair(self.CP_DEFAULT)
            self._safe_addstr(y + i, list_x, f"  {eff:<12s}  ", attr)

        # Params panel
        px = list_x + 18
        py = y
        eff_name = EFFECTS[self.state.eff_idx]
        params = EFFECT_PARAMS.get(eff_name, [])

        self._safe_addstr(py - 1, px, f"─── {eff_name} parameters ───────────",
                          curses.color_pair(self.CP_BORDER))

        param_widgets = []
        if "speed" in params:
            param_widgets.append(("speed", "Speed",
                                  f"{self.state.eff_speed}/10",
                                  self._draw_hbar(self.state.eff_speed, 0, 10, 20)))
        if "brightness" in params:
            param_widgets.append(("brightness", "Brightness",
                                  f"{self.state.eff_brightness}/50",
                                  self._draw_hbar(self.state.eff_brightness, 0, 50, 20)))
        if "color" in params:
            param_widgets.append(("color", "Color",
                                  COLOR_NAMES[self.state.eff_color_idx], None))
        if "direction" in params:
            param_widgets.append(("direction", "Direction",
                                  DIRECTION_NAMES[self.state.eff_dir_idx], None))
        if "reactive" in params:
            param_widgets.append(("reactive", "Reactive",
                                  "ON" if self.state.eff_reactive else "OFF", None))
        if "save" in params:
            param_widgets.append(("save", "Save to flash",
                                  "YES" if self.state.eff_save else "no", None))

        for (fname, flabel, fval, fbar) in param_widgets:
            focused = self.state.eff_focus == fname
            attr = curses.A_REVERSE if focused else curses.color_pair(self.CP_DEFAULT)
            line = f"  {flabel:<16}: "
            self._safe_addstr(py, px, line, curses.color_pair(self.CP_DEFAULT))
            val_str = f" {fval} "
            if fbar:
                val_str = f"[{fbar}] {fval} "
            self._safe_addstr(py, px + len(line), val_str, attr)
            py += 1

        py += 1
        self._safe_addstr(py, px, "  Enter / E = Apply effect now",
                          curses.color_pair(self.CP_GREEN))
        self._safe_addstr(py + 1, px, "  S         = Apply + save to flash",
                          curses.color_pair(self.CP_GREEN))
        self._safe_addstr(py + 2, px, "  O         = Restore default palette",
                          curses.color_pair(self.CP_BORDER))

    def _draw_hbar(self, val, lo, hi, width):
        filled = int((val - lo) / max(1, hi - lo) * width)
        return "█" * filled + "░" * (width - filled)

    # ── Lightbar tab ───────────────────────────────────────────────────────────

    # ── Matrix Scan tab ────────────────────────────────────────────────────────

    def _draw_scan_tab(self, h, w):
        s = self.state
        y = 3
        self._safe_addstr(y, 2,
            "Matrix Scan — identify correct row/col for each physical key",
            curses.color_pair(self.CP_BORDER))
        y += 1
        self._safe_addstr(y, 2,
            "The keyboard will light ONE position red at a time.",
            curses.color_pair(self.CP_DEFAULT))
        y += 2

        if s.scan_done:
            self._safe_addstr(y, 2, "Scan complete! Map saved to key_matrix.json",
                              curses.color_pair(self.CP_GREEN) | curses.A_BOLD)
            y += 2
            self._safe_addstr(y, 2, "Restart the TUI to load the new map.",
                              curses.color_pair(self.CP_DEFAULT))
            return

        total = _ite.NUM_ROWS * _ite.NUM_COLS if HAS_ITE else 126
        done  = s.scan_row * (total // _ite.NUM_ROWS if HAS_ITE else 21) + s.scan_col if HAS_ITE else 0
        done  = s.scan_row * 21 + s.scan_col

        self._safe_addstr(y, 2,
            f"  Lighting up:  Row {s.scan_row}, Col {s.scan_col}   "
            f"({done+1} of {total})",
            curses.color_pair(self.CP_SELECTED) | curses.A_BOLD)
        y += 2

        self._safe_addstr(y, 2,
            "  Type the label of the key that lit up, then press Enter.",
            0)
        y += 1
        self._safe_addstr(y, 2,
            "  Space = skip (or add space mid-label)    Backspace = clear"
            "    Ctrl+D = finish & save    Esc = quit",
            curses.color_pair(self.CP_BORDER))
        y += 2

        self._safe_addstr(y, 2, f"  Label: [{s.scan_input}_]",
                          curses.A_BOLD)
        y += 2

        # Progress bar
        bar_w = 40
        filled = int(done / total * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        self._safe_addstr(y, 2, f"  [{bar}] {done}/{total}", 0)
        y += 2

        # Recent mappings
        self._safe_addstr(y, 2, "  Recent:", curses.color_pair(self.CP_BORDER))
        recent = list(s.scan_map.items())[-8:]
        for i, ((rr, cc), lbl) in enumerate(reversed(recent)):
            self._safe_addstr(y + 1 + i, 4, f"({rr},{cc:2d}) = {lbl}", 0)

    def _handle_scan_tab(self, key):
        s = self.state
        if s.scan_done:
            return

        if key in (4, 6):      # Ctrl+D or Ctrl+F — finish & save
            self._finish_scan()
            return

        # Text input
        if key == ord(' '):
            if s.scan_input:
                s.scan_input += ' '   # allow spaces mid-label (e.g. "Left Ctrl")
            else:
                self._scan_advance()  # Space on empty input = skip position
        elif 33 <= key <= 126:        # other printable ASCII (excludes space)
            s.scan_input += chr(key)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            s.scan_input = s.scan_input[:-1]
        elif key in (10, 13):         # Enter — confirm label and advance
            if s.scan_input.strip():
                s.scan_map[(s.scan_row, s.scan_col)] = s.scan_input.strip()
            s.scan_input = ""
            self._scan_advance()

    def _scan_advance(self):
        s = self.state
        # Light up the NEXT position
        s.scan_col += 1
        if s.scan_col >= 21:
            s.scan_col = 0
            s.scan_row += 1
        if s.scan_row >= 6:
            self._finish_scan()
            return
        self._scan_light_current()

    def _scan_light_current(self):
        s = self.state
        if not self.hw.connected():
            return
        try:
            # All black, then one red key
            colors = {(s.scan_row, s.scan_col): (255, 0, 0)}
            self.hw.handle.set_key_colors(colors, save=False)
        except Exception as e:
            s.status = f"Scan HW error: {e}"

    def _finish_scan(self):
        import json
        s = self.state
        s.scan_done = True
        out = {f"{r},{c}": label for (r, c), label in sorted(s.scan_map.items())}
        try:
            with open(KEY_MATRIX_PATH, "w") as f:
                json.dump(out, f, indent=2)
        except Exception as e:
            s.status = f"Could not save key_matrix.json: {e}"
            return
        s.status = f"Scan saved to {KEY_MATRIX_PATH}"

    # ── Lightbar tab ───────────────────────────────────────────────────────────

    def _draw_lightbar_tab(self, h, w):
        s = self.state
        y = 3
        self._safe_addstr(y, 2,
            "Lightbar (ITE 8233 @ hidraw1) — Tab=focus  ↑↓/←→=change  Enter=apply  O=off",
            curses.color_pair(self.CP_BORDER))
        y += 2

        # Effect list
        for i, (name, eid, _) in enumerate(LB_EFFECTS):
            is_sel = i == s.lb_effect_idx
            is_focus = s.lb_focus == "effect"
            if is_sel and is_focus:
                attr = curses.color_pair(self.CP_SELECTED) | curses.A_BOLD
            elif is_sel:
                attr = curses.color_pair(self.CP_SELECTED)
            else:
                attr = curses.color_pair(self.CP_DEFAULT)
            self._safe_addstr(y + i, 2, f"  {name:<12}  ", attr)

        px = 22
        py = y

        _, eff_id, supports_color = LB_EFFECTS[s.lb_effect_idx]

        # Color swatch (only when effect supports color)
        if supports_color:
            r, g, b = s.lb_r, s.lb_g, s.lb_b
            swatch = ansi_color_block(r, g, b, f"  R:{r:3d} G:{g:3d} B:{b:3d}  ")
            self._safe_addstr_ansi(py, px, swatch)
            py += 2

            sliders = [("R","r",r,255), ("G","g",g,255), ("B","b",b,255)]
            for label, fname, val, hi in sliders:
                focused = s.lb_focus == fname
                bar_w = 26
                filled = int(val / hi * bar_w)
                bar = "█" * filled + "░" * (bar_w - filled)
                attr = curses.A_REVERSE if focused else 0
                self._safe_addstr(py, px, f" {label}: [{bar}] {val:3d} ", attr)
                py += 1
            py += 1

        # Brightness
        focused = s.lb_focus == "brightness"
        bar_w = 26
        filled = int(s.lb_brightness / 50 * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        attr = curses.A_REVERSE if focused else 0
        self._safe_addstr(py, px, f" Brightness: [{bar}] {s.lb_brightness:2d}/50 ", attr)
        py += 1

        # Speed
        focused = s.lb_focus == "speed"
        filled = int(s.lb_speed / 10 * bar_w)
        bar = "█" * filled + "░" * (bar_w - filled)
        attr = curses.A_REVERSE if focused else 0
        self._safe_addstr(py, px, f" Speed:      [{bar}] {s.lb_speed:2d}/10 ", attr)
        py += 2

        self._safe_addstr(py, px,
            " Enter = apply   O = off",
            curses.color_pair(self.CP_GREEN))

    # ── Input handling ─────────────────────────────────────────────────────────

    def _handle_key(self, key):
        s = self.state

        # Scan tab gets full key priority while a scan is running so that every
        # printable character (including Q) can be used as a key label.
        # F1-F4 still switch tabs; Escape quits the app from scan mode.
        if s.tab == 3 and not s.scan_done:
            if key == 27:          # Escape — exit app from scan mode
                sys.exit(0)
            elif key == curses.KEY_F1:
                s.tab = 0; s._set_status_default(); return
            elif key == curses.KEY_F2:
                s.tab = 1; s._set_status_default(); return
            elif key == curses.KEY_F3:
                s.tab = 2; s._set_status_default(); return
            elif key == curses.KEY_F4:
                s.tab = 3; return
            self._handle_scan_tab(key)
            return

        # Global keys
        if key in (ord('q'), ord('Q')):
            sys.exit(0)
        elif key in (ord('r'), ord('R')) and not self.hw.connected():
            # Retry hardware connection
            self.hw = HardwareDriver(loc=self.hw.loc)
            self.state.hw = self.hw
            self.state.status = ("Connected!" if self.hw.connected()
                                 else f"Still no HW: {self.hw.error}")
            return
        elif key == curses.KEY_F1:
            s.tab = 0
            s._set_status_default()
            return
        elif key == curses.KEY_F2:
            s.tab = 1
            s._set_status_default()
            return
        elif key == curses.KEY_F3:
            s.tab = 2
            s._set_status_default()
            return
        elif key == curses.KEY_F4:
            s.tab = 3
            s.scan_done = False
            s.scan_row = 0
            s.scan_col = 0
            s.scan_input = ""
            s.scan_map = {}
            self._scan_light_current()
            s._set_status_default()
            return
        elif key == curses.KEY_MOUSE:
            self._handle_mouse()
            return

        if s.tab == 0:
            self._handle_keys_tab(key)
        elif s.tab == 1:
            self._handle_effects_tab(key)
        elif s.tab == 2:
            self._handle_lightbar_tab(key)
        elif s.tab == 3:
            self._handle_scan_tab(key)

    def _handle_mouse(self):
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return
        if (my, mx) in self.hitmap and self.state.tab == 0:
            idx = self.hitmap[(my, mx)]
            if 0 <= idx < len(self.state.keys_flat):
                self.state.key_sel = idx
                self.state.keys_focus = "keyboard"
                self.state._sync_picker_from_key()
                if bstate & curses.BUTTON1_DOUBLE_CLICKED:
                    self.state.apply_picker_to_selected()

    def _handle_keys_tab(self, key):
        s = self.state
        focus_order = ["keyboard", "h", "s", "v", "r", "g", "b", "brightness"]
        focus_idx = focus_order.index(s.keys_focus) if s.keys_focus in focus_order else 0

        if key == ord('\t'):  # Tab cycles focus
            s.keys_focus = focus_order[(focus_idx + 1) % len(focus_order)]
            return

        if key == curses.KEY_BTAB:  # Shift-Tab
            s.keys_focus = focus_order[(focus_idx - 1) % len(focus_order)]
            return

        if s.keys_focus == "keyboard":
            self._keys_nav(key)
        elif s.keys_focus in ("h", "s", "v"):
            self._adjust_hsv(s.keys_focus, key)
        elif s.keys_focus in ("r", "g", "b"):
            self._adjust_rgb_component(s.keys_focus, key)
        elif s.keys_focus == "brightness":
            self._adjust_brightness(key)

        # Global keys in keys tab
        if key in (ord('\n'), curses.KEY_ENTER, 10, 13):
            s.apply_picker_to_selected()
            s.status = "Color set. Press P to push to keyboard."
        elif key in (ord('a'), ord('A')):
            s.apply_color_to_all()
            s.status = "Color set on all keys. Press P to push to keyboard."
        elif key in (ord('p'), ord('P')):
            self._push_per_key()
        elif key in (ord('o'), ord('O')):
            if self.hw.connected():
                self.hw.restore_palette()
                s.status = "Palette restored."
        elif key == ord(' '):
            if self.hw.connected():
                self.hw.turn_off()
                s.status = "Keyboard backlight turned off."

    def _keys_nav(self, key):
        s = self.state
        n = len(s.keys_flat)
        if key in (curses.KEY_RIGHT, ord('l')):
            s.key_sel = (s.key_sel + 1) % n
        elif key in (curses.KEY_LEFT, ord('h')):
            s.key_sel = (s.key_sel - 1) % n
        elif key in (curses.KEY_DOWN, ord('j')):
            # Move to next row (same approximate column position)
            cur = s.keys_flat[s.key_sel]
            cur_row = cur[1]
            for i in range(s.key_sel + 1, n):
                if s.keys_flat[i][1] > cur_row:
                    s.key_sel = i
                    break
        elif key in (curses.KEY_UP, ord('k')):
            cur = s.keys_flat[s.key_sel]
            cur_row = cur[1]
            for i in range(s.key_sel - 1, -1, -1):
                if s.keys_flat[i][1] < cur_row:
                    s.key_sel = i
                    break
        s._sync_picker_from_key()

    def _adjust_hsv(self, comp, key):
        s = self.state
        step = 5 if key in (curses.KEY_RIGHT, curses.KEY_UP) else -5
        if comp == "h":
            s.picker_h = max(0, min(360, s.picker_h + step))
        elif comp == "s":
            s.picker_s = max(0, min(100, s.picker_s + step))
        elif comp == "v":
            s.picker_v = max(0, min(100, s.picker_v + step))
        s.apply_picker_to_selected()   # live preview

    def _adjust_rgb_component(self, comp, key):
        s = self.state
        r, g, b = s.picker_rgb()
        delta = 5 if key in (curses.KEY_RIGHT, curses.KEY_UP) else -5
        if comp == "r": r = max(0, min(255, r + delta))
        elif comp == "g": g = max(0, min(255, g + delta))
        elif comp == "b": b = max(0, min(255, b + delta))
        s.picker_h, s.picker_s, s.picker_v = rgb_to_hsv(r, g, b)
        s.apply_picker_to_selected()   # live preview

    def _adjust_brightness(self, key):
        s = self.state
        delta = 1 if key in (curses.KEY_RIGHT, curses.KEY_UP) else -1
        s.brightness = max(0, min(50, s.brightness + delta))
        if self.hw.connected():
            self.hw.set_brightness(s.brightness)
            s.status = f"Brightness set to {s.brightness}"

    def _push_per_key(self):
        s = self.state
        if not self.hw.connected():
            s.status = "No hardware connection."
            return
        try:
            self.hw.apply_per_key(s.key_colors, brightness=s.brightness)
            s.status = f"Per-key colors applied ({len(s.key_colors)} keys set)."
        except Exception as e:
            s.status = f"Error: {e}"

    def _handle_effects_tab(self, key):
        s = self.state
        eff_name = EFFECTS[s.eff_idx]
        params = EFFECT_PARAMS.get(eff_name, [])

        focus_list = ["list"] + [p for p in
            ["speed","brightness","color","direction","reactive","save"] if p in params]

        focus_idx = focus_list.index(s.eff_focus) if s.eff_focus in focus_list else 0

        if key == ord('\t'):
            s.eff_focus = focus_list[(focus_idx + 1) % len(focus_list)]
            return
        if key == curses.KEY_BTAB:
            s.eff_focus = focus_list[(focus_idx - 1) % len(focus_list)]
            return

        if s.eff_focus == "list":
            if key in (curses.KEY_DOWN, ord('j')):
                s.eff_idx = (s.eff_idx + 1) % len(EFFECTS)
                s.eff_focus = "list"
            elif key in (curses.KEY_UP, ord('k')):
                s.eff_idx = (s.eff_idx - 1) % len(EFFECTS)
                s.eff_focus = "list"
        elif s.eff_focus == "speed":
            if key in (curses.KEY_RIGHT, curses.KEY_UP):
                s.eff_speed = min(10, s.eff_speed + 1)
            elif key in (curses.KEY_LEFT, curses.KEY_DOWN):
                s.eff_speed = max(0, s.eff_speed - 1)
        elif s.eff_focus == "brightness":
            if key in (curses.KEY_RIGHT, curses.KEY_UP):
                s.eff_brightness = min(50, s.eff_brightness + 1)
            elif key in (curses.KEY_LEFT, curses.KEY_DOWN):
                s.eff_brightness = max(0, s.eff_brightness - 1)
        elif s.eff_focus == "color":
            if key in (curses.KEY_RIGHT, curses.KEY_UP):
                s.eff_color_idx = (s.eff_color_idx + 1) % len(COLOR_NAMES)
            elif key in (curses.KEY_LEFT, curses.KEY_DOWN):
                s.eff_color_idx = (s.eff_color_idx - 1) % len(COLOR_NAMES)
        elif s.eff_focus == "direction":
            if key in (curses.KEY_RIGHT, curses.KEY_UP):
                s.eff_dir_idx = (s.eff_dir_idx + 1) % len(DIRECTION_NAMES)
            elif key in (curses.KEY_LEFT, curses.KEY_DOWN):
                s.eff_dir_idx = (s.eff_dir_idx - 1) % len(DIRECTION_NAMES)
        elif s.eff_focus == "reactive":
            if key in (curses.KEY_RIGHT, curses.KEY_LEFT, curses.KEY_UP, curses.KEY_DOWN,
                       ord(' '), 10, 13):
                s.eff_reactive = not s.eff_reactive
        elif s.eff_focus == "save":
            if key in (curses.KEY_RIGHT, curses.KEY_LEFT, curses.KEY_UP, curses.KEY_DOWN,
                       ord(' '), 10, 13):
                s.eff_save = not s.eff_save

        # Apply
        if key in (10, 13, ord('\n'), curses.KEY_ENTER, ord('e'), ord('E')):
            self._apply_effect(save=s.eff_save)
        elif key in (ord('s'), ord('S')):
            self._apply_effect(save=True)
        elif key in (ord('o'), ord('O')):
            if self.hw.connected():
                self.hw.restore_palette()
                s.status = "Palette restored."

    def _apply_effect(self, save=False):
        s = self.state
        if not self.hw.connected():
            s.status = "No hardware connection."
            return
        try:
            eff_name = EFFECTS[s.eff_idx]
            self.hw.set_effect(
                eff_name,
                speed=s.eff_speed,
                brightness=s.eff_brightness,
                color_idx=s.eff_color_idx,
                direction_idx=s.eff_dir_idx,
                reactive=s.eff_reactive,
                save=save,
            )
            saved = " (saved to flash)" if save else ""
            s.status = f"Applied effect: {eff_name}{saved}"
        except Exception as e:
            s.status = f"Error: {e}"

    def _handle_lightbar_tab(self, key):
        s = self.state
        _, _, supports_color = LB_EFFECTS[s.lb_effect_idx]
        focus_order = (["effect"] +
                       (["r", "g", "b"] if supports_color else []) +
                       ["brightness", "speed"])
        if s.lb_focus not in focus_order:
            s.lb_focus = focus_order[0]
        focus_idx = focus_order.index(s.lb_focus)

        if key == ord('\t'):
            s.lb_focus = focus_order[(focus_idx + 1) % len(focus_order)]
            return
        if key == curses.KEY_BTAB:
            s.lb_focus = focus_order[(focus_idx - 1) % len(focus_order)]
            return

        delta_big = 5 if key in (curses.KEY_RIGHT, curses.KEY_UP) else -5
        delta_sm  = 1 if key in (curses.KEY_RIGHT, curses.KEY_UP) else -1

        if s.lb_focus == "effect":
            if key in (curses.KEY_DOWN, ord('j')):
                s.lb_effect_idx = (s.lb_effect_idx + 1) % len(LB_EFFECTS)
            elif key in (curses.KEY_UP, ord('k')):
                s.lb_effect_idx = (s.lb_effect_idx - 1) % len(LB_EFFECTS)
        elif s.lb_focus == "r":
            s.lb_r = max(0, min(255, s.lb_r + delta_big))
        elif s.lb_focus == "g":
            s.lb_g = max(0, min(255, s.lb_g + delta_big))
        elif s.lb_focus == "b":
            s.lb_b = max(0, min(255, s.lb_b + delta_big))
        elif s.lb_focus == "brightness":
            s.lb_brightness = max(0, min(50, s.lb_brightness + delta_sm))
        elif s.lb_focus == "speed":
            s.lb_speed = max(0, min(10, s.lb_speed + delta_sm))

        if key in (10, 13, ord('\n'), curses.KEY_ENTER):
            self._apply_lightbar()
        elif key in (ord('o'), ord('O')):
            err = lb_off()
            s.status = "Lightbar: off." if not err else f"Lightbar off error: {err}"

    def _apply_lightbar(self):
        s = self.state
        name, eff_id, supports_color = LB_EFFECTS[s.lb_effect_idx]

        if eff_id is None:
            err = lb_off()
            s.status = "Lightbar: off." if not err else f"Lightbar error: {err}"
            return

        r, g, b = s.lb_r, s.lb_g, s.lb_b

        if supports_color:
            err = lb_palette(1, r, g, b)
            if err:
                s.status = f"Lightbar palette error: {err}"
                return
            color_slot = 1
        else:
            color_slot = 8  # random / don't care

        err = lb_effect(eff_id, s.lb_speed, s.lb_brightness, color_slot)
        if err:
            s.status = f"Lightbar error: {err}"
        else:
            s.status = ("Lightbar: " + name +
                        (f"  RGB({r},{g},{b})" if supports_color else ""))


# ─── Main ─────────────────────────────────────────────────────────────────────

_CLI_DEVICE_LOC = None   # set from args before curses.wrapper

def main(stdscr):
    curses.curs_set(0)
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)

    # Enable ANSI escape passthrough for truecolor
    os.environ.setdefault("TERM", "xterm-256color")

    app = TuiApp(stdscr, device_loc=_CLI_DEVICE_LOC)
    app.run()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="HYDROC-16 RGB TUI (run as root)")
    ap.add_argument("--device", metavar="BUS/ADDR",
                    help="USB bus/address, e.g. 1/4  (find with: lsusb | grep 048d)")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("Warning: not running as root. USB access will likely fail.", file=sys.stderr)

    if args.device:
        try:
            bus, addr = args.device.split("/")
            _CLI_DEVICE_LOC = (int(bus), int(addr))
        except ValueError:
            print(f"Bad --device format '{args.device}', expected BUS/ADDR e.g. 1/4",
                  file=sys.stderr)
            sys.exit(1)

    curses.wrapper(main)
