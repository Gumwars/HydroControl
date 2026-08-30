#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
kbctrl.tui — Textual TUI for ITE 8291 RGB keyboard (HYDROC-16)

Run with:  sudo kbctrl-tui
       or  sudo python3 -m kbctrl.tui [--device BUS/ADDR]
"""

from __future__ import annotations

import argparse
import colorsys
import json
from pathlib import Path
from typing import ClassVar

from rich.color import Color as RichColor
from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from .config import (
    PROFILE_PATH,
    decode_key_colors,
    encode_key_colors,
    load_profile,
    save_profile,
)
from .hardware import (
    COLOR_NAMES,
    DIRECTION_NAMES,
    EFFECT_PARAMS,
    EFFECTS,
    HardwareDriver,
    lb_color,
)
from .layout import KEY_MATRIX_PATH, KEYBOARD_ROWS, load_key_matrix

# ── Colour helpers ─────────────────────────────────────────────────────────────


def hsv_to_rgb(h: int, s: int, v: int) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, v / 100.0)
    return int(r * 255), int(g * 255), int(b * 255)


def rgb_to_hsv(r: int, g: int, b: int) -> tuple[int, int, int]:
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return round(h * 360), round(s * 100), round(v * 100)


def _fg(r: int, g: int, b: int) -> tuple[int, int, int]:
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) > 128 else (255, 255, 255)


# ── Keyboard widget ────────────────────────────────────────────────────────────


class KeyboardWidget(Widget, can_focus=True):
    """Renders the physical keyboard with per-key colours.  Arrow keys navigate."""

    DEFAULT_CSS = """
    KeyboardWidget {
        height: auto;
        padding: 0 1;
        border: solid $primary;
    }
    KeyboardWidget:focus {
        border: solid $accent;
    }
    """

    selected: reactive[int] = reactive(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._rows: list = []
        self._flat: list = []

    def set_layout(self, rows: list, flat: list) -> None:
        self._rows = rows
        self._flat = flat
        self.refresh()

    # All regular keys render as KEY_W-wide squares; spacebar is SPACE_W wide.
    KEY_W   = 5   # ╭───╮  (3 inner chars)
    SPACE_W = 18  # ╭────────────────╮

    def render(self) -> Text:
        app: KbCtrlApp = self.app  # type: ignore
        key_colors = app.key_colors
        flat = self._flat
        sel  = self.selected
        text = Text(no_wrap=True)

        for row in self._rows:
            # Pre-compute per-key data for this display row
            keys = []
            for label, r, c, _ in row:
                idx = next(
                    (i for i, (_, rr, cc, _) in enumerate(flat) if rr == r and cc == c),
                    -1,
                )
                br, bg, bb = key_colors.get((r, c), (40, 40, 40))
                fr, fgv, fb = _fg(br, bg, bb)
                is_sel = (idx == sel)
                kw = self.SPACE_W if label == "Space" else self.KEY_W
                keys.append((label, br, bg, bb, fr, fgv, fb, is_sel, kw))

            # ── line 1: top borders ──────────────────────────────────────────
            for label, br, bg, bb, fr, fgv, fb, is_sel, kw in keys:
                inner = kw - 2
                style = Style(color=RichColor.from_rgb(br, bg, bb),
                              bold=is_sel)
                tl = "╔" if is_sel else "╭"
                tr = "╗" if is_sel else "╮"
                fill = "═" if is_sel else "─"
                text.append(tl + fill * inner + tr, style=style)
            text.append("\n")

            # ── line 2: label ────────────────────────────────────────────────
            for label, br, bg, bb, fr, fgv, fb, is_sel, kw in keys:
                inner  = kw - 2
                cell   = label[:inner].center(inner)
                bstyle = Style(color=RichColor.from_rgb(br, bg, bb), bold=is_sel)
                cstyle = Style(bgcolor=RichColor.from_rgb(br, bg, bb),
                               color=RichColor.from_rgb(fr, fgv, fb),
                               bold=is_sel)
                pipe = "║" if is_sel else "│"
                text.append(pipe, style=bstyle)
                text.append(cell, style=cstyle)
                text.append(pipe, style=bstyle)
            text.append("\n")

            # ── line 3: bottom borders ───────────────────────────────────────
            for label, br, bg, bb, fr, fgv, fb, is_sel, kw in keys:
                inner = kw - 2
                style = Style(color=RichColor.from_rgb(br, bg, bb), bold=is_sel)
                bl   = "╚" if is_sel else "╰"
                br_c = "╝" if is_sel else "╯"
                fill = "═" if is_sel else "─"
                text.append(bl + fill * inner + br_c, style=style)
            text.append("\n")

            # ── gap between rows ─────────────────────────────────────────────
            text.append("\n")

        return text

    def on_key(self, event) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        flat = self._flat
        if not flat:
            return

        sel = self.selected
        key = event.key

        if key == "right":
            sel = min(len(flat) - 1, sel + 1)
        elif key == "left":
            sel = max(0, sel - 1)
        elif key == "down":
            # find next key on the row below
            _, cur_row, cur_col, _ = flat[sel]
            candidates = [(i, abs(cc - cur_col)) for i, (_, rr, cc, _) in enumerate(flat)
                          if rr == cur_row - 1]
            if candidates:
                sel = min(candidates, key=lambda x: x[1])[0]
        elif key == "up":
            _, cur_row, cur_col, _ = flat[sel]
            candidates = [(i, abs(cc - cur_col)) for i, (_, rr, cc, _) in enumerate(flat)
                          if rr == cur_row + 1]
            if candidates:
                sel = min(candidates, key=lambda x: x[1])[0]
        elif key == "enter":
            app.apply_picker_to_selected()
            return
        elif key == "a":
            app.apply_color_to_all()
            return
        elif key == "p":
            app.push_to_hardware()
            return
        elif key == "o":
            app.hw.restore_palette()
            app.set_status("Palette restored")
            return
        else:
            return

        event.prevent_default()
        self.selected = sel
        # sync picker to newly selected key
        _, r, c, _ = flat[sel]
        pos = (r, c)
        if pos in app.key_colors:
            rr, gg, bb = app.key_colors[pos]
            app.picker_h, app.picker_s, app.picker_v = rgb_to_hsv(rr, gg, bb)
        app.query_one(ColorPicker).update()

    def watch_selected(self, val: int) -> None:
        self.refresh()


# ── Slider widget ──────────────────────────────────────────────────────────────


class SliderWidget(Widget, can_focus=True):
    """Keyboard-driven numeric slider.  Left/right arrows adjust the value."""

    DEFAULT_CSS = """
    SliderWidget {
        height: 1;
        padding: 0 1;
    }
    SliderWidget:focus {
        background: $accent 20%;
    }
    """

    BAR_WIDTH: ClassVar[int] = 24

    def __init__(self, label: str, attr: str, lo: int, hi: int,
                 step: int = 1, unit: str = "", **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.attr = attr
        self.lo = lo
        self.hi = hi
        self.step = step
        self.unit = unit

    def _get_val(self) -> int:
        app: KbCtrlApp = self.app  # type: ignore
        return getattr(app, self.attr, self.lo)

    def _set_val(self, v: int) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        setattr(app, self.attr, max(self.lo, min(self.hi, v)))
        app.on_slider_changed(self.attr)
        self.refresh()

    def render(self) -> Text:
        val = self._get_val()
        filled = int((val - self.lo) / max(1, self.hi - self.lo) * self.BAR_WIDTH)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        text = Text(no_wrap=True)
        text.append(f" {self.label:<3}: ")
        text.append(f"[{bar}] {val:4d}{self.unit}")
        return text

    def on_key(self, event) -> None:
        key = event.key
        if key == "right":
            self._set_val(self._get_val() + self.step)
            event.prevent_default()
        elif key == "left":
            self._set_val(self._get_val() - self.step)
            event.prevent_default()


# ── Color picker panel ─────────────────────────────────────────────────────────


class ColorPicker(Container):
    DEFAULT_CSS = """
    ColorPicker {
        height: auto;
        padding: 0 1;
        border: solid $primary;
    }
    #swatch {
        height: 1;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="swatch")
        yield SliderWidget("H", "picker_h", 0, 360, step=2, unit="°", id="sl_h")
        yield SliderWidget("S", "picker_s", 0, 100, step=1, unit="%", id="sl_s")
        yield SliderWidget("V", "picker_v", 0, 100, step=1, unit="%", id="sl_v")
        yield Static(" ── RGB ──", id="rgb_sep")
        yield SliderWidget("R", "_picker_r", 0, 255, step=5, id="sl_r")
        yield SliderWidget("G", "_picker_g", 0, 255, step=5, id="sl_g")
        yield SliderWidget("B", "_picker_b", 0, 255, step=5, id="sl_b")
        yield Static(" ── Global ──", id="global_sep")
        yield SliderWidget("Bri", "brightness", 0, 50, step=1, id="sl_bri")
        yield Label(" Enter=set key  A=all keys  P=push to HW  O=restore palette",
                    id="picker_hint")

    def refresh_swatch(self) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        r, g, b = hsv_to_rgb(app.picker_h, app.picker_s, app.picker_v)
        style = Style(bgcolor=RichColor.from_rgb(r, g, b),
                      color=RichColor.from_rgb(*_fg(r, g, b)))
        t = Text(no_wrap=True)
        t.append(f"  R:{r:3d}  G:{g:3d}  B:{b:3d}  ", style=style)
        self.query_one("#swatch", Static).update(t)
        for sl in self.query(SliderWidget):
            sl.refresh()

    def update(self) -> None:
        try:
            self.refresh_swatch()
        except Exception:
            pass


# ── Keys tab ───────────────────────────────────────────────────────────────────


class KeysTab(Container):
    DEFAULT_CSS = """
    KeysTab {
        layout: vertical;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(" Keyboard  (arrows=navigate, Enter=set, A=all, P=push, O=restore palette)",
                    id="keys_hint")
        yield KeyboardWidget(id="keyboard")
        yield ColorPicker(id="color_picker")


# ── Effects tab ────────────────────────────────────────────────────────────────


class EffectsTab(Widget, can_focus=True):
    """
    Single focusable widget for effect selection and parameter editing.

    Navigation:
      ↑/↓         select effect
      Tab         cycle through editable parameters
      ←/→         adjust focused parameter value
      Enter / E   apply effect now
      S           apply + save to flash
    """

    DEFAULT_CSS = """
    EffectsTab {
        padding: 1 2;
        border: solid $primary;
        height: auto;
    }
    EffectsTab:focus {
        border: solid $accent;
    }
    """

    # Which parameter row has editing focus (cycles with Tab)
    _PARAM_ORDER = ["speed", "brightness", "color", "direction", "reactive", "save"]
    _param_focus: reactive[int] = reactive(0)

    def render(self) -> Text:
        app: KbCtrlApp = self.app  # type: ignore
        t = Text(no_wrap=True)

        # Effect list
        t.append(" Effects\n", style=Style(bold=True))
        for i, eff in enumerate(EFFECTS):
            if i == app.eff_idx:
                t.append(f" ► {eff}\n", style=Style(bold=True, reverse=True))
            else:
                t.append(f"   {eff}\n")

        # Parameters
        t.append("\n Parameters\n", style=Style(bold=True))
        name = EFFECTS[app.eff_idx]
        params = EFFECT_PARAMS.get(name, [])

        # Build list of (param_key, label, value_str) for params that apply
        rows = []
        if "speed"      in params: rows.append(("speed",     "Speed",      f"{app.eff_speed}/10"))
        if "brightness" in params: rows.append(("brightness","Brightness", f"{app.eff_brightness}/50"))
        if "color"      in params: rows.append(("color",     "Color",      COLOR_NAMES[app.eff_color_idx]))
        if "direction"  in params: rows.append(("direction", "Direction",  DIRECTION_NAMES[app.eff_dir_idx]))
        if "reactive"   in params: rows.append(("reactive",  "Reactive",   "ON" if app.eff_reactive else "off"))
        if "save"       in params: rows.append(("save",      "Save",       "YES" if app.eff_save else "no"))

        # Map valid param indices for Tab cycling
        valid_params = [r[0] for r in rows]
        focused_key = valid_params[self._param_focus % len(valid_params)] if valid_params else ""

        for key, label, val in rows:
            focused = key == focused_key
            line = f"  {label:<14}  {val}"
            t.append(line + "\n", style=Style(reverse=focused))

        t.append("\n ↑↓=effect  Tab=param  ←/→=adjust  Enter=Apply  S=Apply+Save\n")
        return t

    def on_key(self, event) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        name = EFFECTS[app.eff_idx]
        params = EFFECT_PARAMS.get(name, [])
        valid_params = [p for p in self._PARAM_ORDER if p in params]

        key = event.key

        if key == "up":
            app.eff_idx = max(0, app.eff_idx - 1)
            self._param_focus = 0
            self.refresh()
            event.prevent_default()
        elif key == "down":
            app.eff_idx = min(len(EFFECTS) - 1, app.eff_idx + 1)
            self._param_focus = 0
            self.refresh()
            event.prevent_default()
        elif key == "tab":
            if valid_params:
                self._param_focus = (self._param_focus + 1) % len(valid_params)
            self.refresh()
            event.prevent_default()
        elif key in ("left", "right"):
            delta = 1 if key == "right" else -1
            if valid_params:
                focused = valid_params[self._param_focus % len(valid_params)]
                self._adjust_param(app, focused, delta)
            self.refresh()
            event.prevent_default()
        elif key in ("enter", "e"):
            app.apply_effect(save=False)
            event.prevent_default()
        elif key == "s":
            app.apply_effect(save=True)
            event.prevent_default()
        elif key == "o":
            app.hw.restore_palette()
            app.set_status("Palette restored")
            event.prevent_default()

    def _adjust_param(self, app: "KbCtrlApp", param: str, delta: int) -> None:
        if param == "speed":
            app.eff_speed = max(0, min(10, app.eff_speed + delta))
        elif param == "brightness":
            app.eff_brightness = max(0, min(50, app.eff_brightness + delta))
        elif param == "color":
            app.eff_color_idx = (app.eff_color_idx + delta) % len(COLOR_NAMES)
        elif param == "direction":
            app.eff_dir_idx = (app.eff_dir_idx + delta) % len(DIRECTION_NAMES)
        elif param == "reactive":
            app.eff_reactive = not app.eff_reactive
        elif param == "save":
            app.eff_save = not app.eff_save


# ── Lightbar tab ───────────────────────────────────────────────────────────────
# The ITE 8233 (048d:7001) supports a solid color + brightness (0-100).
# Protocol verified against tuxedo-drivers ite_8291_lb.c and OpenRGB MR 3166.


class LightbarTab(Container):
    DEFAULT_CSS = """
    LightbarTab { padding: 1 2; height: auto; }
    #lb_info { color: $text-muted; margin-bottom: 1; }
    """

    def compose(self) -> ComposeResult:
        yield Label(
            " Lightbar (ITE 8233) — solid color + brightness",
            id="lb_title",
        )
        yield Label(
            " Type a hex colour (e.g. FF0000) and press Enter, then use the\n"
            " slider for brightness. Brightness 0 = off.",
            id="lb_info",
        )
        yield SliderWidget("Bri", "lb_brightness", 0, 100, id="lb_bri")
        with Horizontal(id="lb_buttons"):
            yield Button("Off",  id="lb_btn_off",  variant="error")
            yield Button("Dim",  id="lb_btn_dim",  variant="default")
            yield Button("Mid",  id="lb_btn_mid",  variant="default")
            yield Button("Full", id="lb_btn_full", variant="success")
        with Horizontal(id="lb_color_row"):
            yield Label("Color: ", id="lb_color_prompt")
            yield Input("#FFFFFF", id="lb_color_input")
        yield Label(" Slider: ←/→ to adjust   Enter=Apply   (changes saved automatically)",
                    id="lb_hint")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        presets = {"lb_btn_off": 0, "lb_btn_dim": 10, "lb_btn_mid": 50, "lb_btn_full": 100}
        if event.button.id in presets:
            app.lb_brightness = presets[event.button.id]
            app.apply_lightbar()
            self.query_one("#lb_bri", SliderWidget).refresh()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        try:
            hexv = event.value.strip().lstrip("#")
            if len(hexv) != 6:
                raise ValueError
            int(hexv, 16)
        except ValueError:
            self.app.set_status("Lightbar color must be 6 hex digits (e.g. FF0000)")
            return
        app.lb_color = tuple(
            int(hexv[i:i + 2], 16) for i in (0, 2, 4)
        )
        app.apply_lightbar()


# ── Scan tab ───────────────────────────────────────────────────────────────────


class ScanTab(Container):
    DEFAULT_CSS = """
    ScanTab { padding: 1 2; height: auto; }
    #scan_status { color: $success; }
    #scan_input_row { layout: horizontal; height: 1; }
    #scan_prompt { width: 30; }
    #scan_input { width: 1fr; }
    #scan_recent { height: auto; }
    """

    def compose(self) -> ComposeResult:
        yield Label(" Matrix Scan — label each key as it lights up", id="scan_title")
        yield Label("", id="scan_status")
        yield Label("", id="scan_pos")
        with Horizontal(id="scan_input_row"):
            yield Label(" Label: ", id="scan_prompt")
            yield Static("", id="scan_input")
        yield Label("", id="scan_recent")
        yield Label(
            " Space=skip  Enter=confirm  Ctrl+D=finish early  F4=restart scan",
            id="scan_hint",
        )

    def on_mount(self) -> None:
        self.can_focus = True
        self.focus()
        self._update_display()

    def _update_display(self) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        s = app.scan_state
        if s["done"]:
            self.query_one("#scan_status", Label).update(
                f" Scan complete — saved to {KEY_MATRIX_PATH}"
            )
            self.query_one("#scan_pos", Label).update("")
            self.query_one("#scan_input", Static).update("")
        else:
            total = 6 * 21
            done = len(s["map"])
            self.query_one("#scan_pos", Label).update(
                f" Row {s['row']}, Col {s['col']}  ({done}/{total})"
            )
            self.query_one("#scan_input", Static).update(s["input"] + "█")
        recent = list(s["map"].items())[-8:]
        lines = [f" ({r},{c:2d}) = {lbl}" for (r, c), lbl in reversed(recent)]
        self.query_one("#scan_recent", Label).update("\n".join(lines))

    def on_key(self, event) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        s = app.scan_state
        if s["done"]:
            return

        key = event.key

        if key == "ctrl+d":
            self._finish()
            event.prevent_default()
            return

        if key == "space":
            if s["input"]:
                s["input"] += " "
            else:
                self._advance()
        elif key == "backspace":
            s["input"] = s["input"][:-1]
        elif key == "enter":
            if s["input"].strip():
                s["map"][(s["row"], s["col"])] = s["input"].strip()
            s["input"] = ""
            self._advance()
        elif len(key) == 1 and 33 <= ord(key) <= 126:
            s["input"] += key
        else:
            return

        event.prevent_default()
        self._update_display()

    def _advance(self) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        s = app.scan_state
        s["col"] += 1
        if s["col"] >= 21:
            s["col"] = 0
            s["row"] += 1
        if s["row"] >= 6:
            self._finish()
            return
        # light current key
        try:
            app.hw.handle.set_key_colors({(s["row"], s["col"]): (255, 0, 0)})
        except Exception:
            pass

    def _finish(self) -> None:
        app: KbCtrlApp = self.app  # type: ignore
        s = app.scan_state
        s["done"] = True
        out = {f"{r},{c}": lbl for (r, c), lbl in sorted(s["map"].items())}
        try:
            KEY_MATRIX_PATH.write_text(json.dumps(out, indent=2))
        except Exception as e:
            app.set_status(f"Save failed: {e}")
            return
        app.set_status(f"Scan saved → {KEY_MATRIX_PATH}  (restart to reload layout)")
        self._update_display()


# ── Main App ───────────────────────────────────────────────────────────────────


class KbCtrlApp(App):
    CSS = """
    Screen {
        background: $surface;
    }
    TabbedContent {
        height: 1fr;
    }
    #status_bar {
        dock: bottom;
        height: 1;
        background: $warning;
        color: $text;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("f1", "switch_tab('keys')",      "Keys",     show=True),
        Binding("f2", "switch_tab('effects')",   "Effects",  show=True),
        Binding("f3", "switch_tab('lightbar')",  "Lightbar", show=True),
        Binding("f4", "switch_tab('scan')",      "Scan",     show=True),
        Binding("q",  "quit",                    "Quit",     show=True),
    ]

    TITLE = "HYDROC-16 RGB Controller"

    # ── Reactive state ─────────────────────────────────────────────────────────
    # These drive SliderWidget.render() via self.app.<attr>

    picker_h: reactive[int] = reactive(0)
    picker_s: reactive[int] = reactive(100)
    picker_v: reactive[int] = reactive(100)

    # RGB mirrors of picker (read/write by RGB sliders)
    _picker_r: reactive[int] = reactive(255)
    _picker_g: reactive[int] = reactive(0)
    _picker_b: reactive[int] = reactive(0)

    brightness: reactive[int] = reactive(25)

    eff_idx:        reactive[int]  = reactive(0)
    eff_speed:      reactive[int]  = reactive(5)
    eff_brightness: reactive[int]  = reactive(25)
    eff_color_idx:  reactive[int]  = reactive(8)
    eff_dir_idx:    reactive[int]  = reactive(1)
    eff_reactive:   reactive[bool] = reactive(False)
    eff_save:       reactive[bool] = reactive(False)

    lb_brightness:  reactive[int]  = reactive(25)
    lb_color:       reactive[tuple] = reactive((255, 255, 255))

    def __init__(self, device_loc=None, **kwargs):
        super().__init__(**kwargs)
        self.hw = HardwareDriver(loc=device_loc)
        self.key_colors: dict[tuple[int, int], tuple[int, int, int]] = {}
        self.scan_state: dict = {
            "row": 0, "col": 0, "input": "", "map": {}, "done": False
        }

        # Load saved profile
        profile = load_profile()
        if "key_colors" in profile:
            self.key_colors = decode_key_colors(profile["key_colors"])
        if "brightness" in profile:
            self.brightness = profile["brightness"]
        if "lightbar" in profile:
            lb = profile["lightbar"]
            if "brightness" in lb:
                self.lb_brightness = lb["brightness"]
            if "color" in lb:
                self.lb_color = tuple(lb["color"])

        # Load key layout
        loaded = load_key_matrix()
        self.keyboard_rows = loaded if loaded is not None else KEYBOARD_ROWS
        self.keys_flat = [
            (lbl, r, c, w)
            for row in self.keyboard_rows
            for (lbl, r, c, w) in row
        ]

        self.brightness = self.hw.get_brightness()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane("Keys [F1]", id="keys"):
                yield KeysTab(id="keys_tab")
            with TabPane("Effects [F2]", id="effects"):
                yield EffectsTab(id="effects_tab")
            with TabPane("Lightbar [F3]", id="lightbar"):
                yield LightbarTab(id="lightbar_tab")
            with TabPane("Scan [F4]", id="scan"):
                yield ScanTab(id="scan_tab")
        hw_str = "HW: OK" if self.hw.connected() else f"NO HW: {self.hw.error}"
        yield Static(f" {hw_str}  |  Profile: {PROFILE_PATH}", id="status_bar")
        yield Footer()

    def on_mount(self) -> None:
        kb = self.query_one(KeyboardWidget)
        kb.set_layout(self.keyboard_rows, self.keys_flat)
        if not self.hw.connected():
            self.set_status(f"Hardware not connected: {self.hw.error}")

    # ── Tab switching ──────────────────────────────────────────────────────────

    def action_switch_tab(self, tab_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = tab_id
        if tab_id == "scan":
            try:
                self.query_one(ScanTab).focus()
            except Exception:
                pass

    # ── Color picker logic ─────────────────────────────────────────────────────

    def on_slider_changed(self, attr: str) -> None:
        """Called by SliderWidget after value change — sync HSV↔RGB."""
        if attr in ("picker_h", "picker_s", "picker_v"):
            r, g, b = hsv_to_rgb(self.picker_h, self.picker_s, self.picker_v)
            self._picker_r, self._picker_g, self._picker_b = r, g, b
        elif attr in ("_picker_r", "_picker_g", "_picker_b"):
            h, s, v = rgb_to_hsv(self._picker_r, self._picker_g, self._picker_b)
            self.picker_h, self.picker_s, self.picker_v = h, s, v
        try:
            self.query_one(ColorPicker).refresh()
        except Exception:
            pass

    def picker_rgb(self) -> tuple[int, int, int]:
        return hsv_to_rgb(self.picker_h, self.picker_s, self.picker_v)

    def apply_picker_to_selected(self) -> None:
        kb = self.query_one(KeyboardWidget)
        idx = kb.selected
        if idx < len(self.keys_flat):
            _, r, c, _ = self.keys_flat[idx]
            self.key_colors[(r, c)] = self.picker_rgb()
            kb.refresh()

    def apply_color_to_all(self) -> None:
        rgb = self.picker_rgb()
        for _, r, c, _ in self.keys_flat:
            self.key_colors[(r, c)] = rgb
        self.query_one(KeyboardWidget).refresh()
        self.set_status("Color applied to all keys — press P to push to hardware")

    def push_to_hardware(self, save: bool = False) -> None:
        if not self.hw.connected():
            self.set_status("Hardware not connected")
            return
        try:
            self.hw.apply_per_key(self.key_colors, brightness=self.brightness, save=save)
            self._save_profile()
            self.set_status("Pushed to keyboard" + (" (saved)" if save else ""))
        except Exception as e:
            self.set_status(f"HW error: {e}")

    # ── Effect apply ───────────────────────────────────────────────────────────

    def apply_effect(self, save: bool = False) -> None:
        if not self.hw.connected():
            self.set_status("Hardware not connected")
            return
        try:
            self.hw.set_effect(
                EFFECTS[self.eff_idx],
                speed=self.eff_speed,
                brightness=self.eff_brightness,
                color_idx=self.eff_color_idx,
                direction_idx=self.eff_dir_idx,
                reactive=self.eff_reactive,
                save=save,
            )
            self._save_profile()
            self.set_status(f"Effect '{EFFECTS[self.eff_idx]}' applied" + (" (saved)" if save else ""))
        except Exception as e:
            self.set_status(f"Effect error: {e}")

    # ── Lightbar apply ─────────────────────────────────────────────────────────

    def apply_lightbar(self) -> None:
        err = lb_color(*self.lb_color, self.lb_brightness)
        if err:
            self.set_status(f"Lightbar error: {err}")
        else:
            self._save_profile()
            if self.lb_brightness == 0:
                state = "off"
            else:
                state = f"#{''.join(f'{c:02X}' for c in self.lb_color)} at {self.lb_brightness}"
            self.set_status(f"Lightbar: {state}")

    # ── Profile persistence ────────────────────────────────────────────────────

    def _save_profile(self) -> None:
        profile = {
            "brightness": self.brightness,
            "key_colors": encode_key_colors(self.key_colors),
            "effect": {
                "name": EFFECTS[self.eff_idx],
                "speed": self.eff_speed,
                "brightness": self.eff_brightness,
                "color_idx": self.eff_color_idx,
                "dir_idx": self.eff_dir_idx,
                "reactive": self.eff_reactive,
            },
            "lightbar": {
                "brightness": self.lb_brightness,
                "color": list(self.lb_color),
            },
        }
        save_profile(profile)

    # ── Status bar ─────────────────────────────────────────────────────────────

    def set_status(self, msg: str) -> None:
        try:
            self.query_one("#status_bar", Static).update(f" {msg}")
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="kbctrl TUI")
    parser.add_argument("--device", help="Explicit USB address (BUS/ADDR)")
    args = parser.parse_args()

    loc = None
    if args.device:
        bus, addr = args.device.split("/")
        loc = (int(bus), int(addr))

    app = KbCtrlApp(device_loc=loc)
    app.run()


if __name__ == "__main__":
    main()
