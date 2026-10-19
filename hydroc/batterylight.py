# SPDX-License-Identifier: MIT
"""
hydroc.batterylight — dim or switch off the keyboard and chin bar on battery.

    battery_lighting: "keep" | "dim" | "off"      (profile key, default "keep")

Unplugging applies it; plugging back in restores what was there. Runs inside
hydroc-server, because the keyboard is a libusb device with one owner per
process and the transition needs something that is always listening. `cli
apply` honours it for the chin bar only, at boot and resume: the bar is
fire-and-forget hidraw, and without that a boot on battery would light it
from the saved profile before the server got a say.

Keyboard: only its brightness is touched (ITE command 0x09, `09 02 <level>`),
never the effect or colours, and 0x09 carries no save byte -- the flash write
lives in byte 7 of the 08h/14h/1Ah packets -- so unplugging all day writes no
flash (tests/test_no_flash_writes.py). The level before dimming is kept in
/run, and is restored on AC only if the keyboard still reads what we set: a
brightness the user picked on battery is new intent, not ours to undo.

Chin bar: volatile and write-only, so there is nothing to read back. Off sends
off; dim re-sends the saved profile at reduced brightness; AC re-sends the
saved profile as is.

Measured on the development machine, idle on battery (2026-10-06), but the
figures were not recorded with it. power_saver_probe.py cannot price the
lights either -- hydroc-server owns the keyboard -- so the saving is unquantified
in this repository (CHANGELOG 0.9.6).
"""

from __future__ import annotations

import json
import os

MODES = ("keep", "dim", "off")
KEY = "battery_lighting"
DIM_KEYBOARD = 10          # of 50
DIM_CHIN = 20              # percent
KEYBOARD_MAX = 50
FALLBACK_KEYBOARD = 25
SUPPLIES = "/sys/class/power_supply"
STATE_PATH = "/run/hydroc/battery-lighting.json"


def on_external_power(root: str = SUPPLIES) -> bool | None:
    """True if a mains or USB-C supply is online, False if none is, None if
    this cannot be read. USB-C counts: AC0 reads offline while charging over
    USB-C, and treating that as battery would dim a plugged-in machine."""
    try:
        names = os.listdir(root)
    except OSError:
        return None
    seen = False
    for n in names:
        try:
            with open(os.path.join(root, n, "type")) as fh:
                kind = fh.read().strip()
            if kind not in ("Mains", "USB"):
                continue
            with open(os.path.join(root, n, "online")) as fh:
                seen = True
                if fh.read().strip() == "1":
                    return True
        except OSError:
            continue
    return False if seen else None


def mode_of(profile: dict) -> str:
    m = profile.get(KEY, "keep")
    return m if m in MODES else "keep"


def chin_override(profile: dict, on_battery: bool) -> dict | None:
    """What the chin bar should run instead of the saved profile, or None."""
    m = mode_of(profile)
    if not on_battery or m == "keep":
        return None
    if m == "off":
        return {"chin_mode": "off"}
    return {"chin_brightness": min(int(profile.get("chin_brightness", 100)), DIM_CHIN)}


class BatteryLighting:
    """Applies and undoes the battery setting. Hardware access is injected:

    kbd_get()    -> int | None   current keyboard brightness, None if absent
    kbd_set(n)   -> None
    chin(profile)-> dict          re-send chin bar intent from a profile dict

    The keyboard's brightness readback is not trusted outside 0..50. On the
    development machine (048d:600b, in no released ite8291r3-ctl) it read 100
    against a settable range of 0..50 -- another scale or another field, not
    established. A reading out of range is treated as unknown, and the level
    restored is the one last applied through HydroControl
    (`profile["kb_brightness"]`), else FALLBACK_KEYBOARD.
    """

    def __init__(self, kbd_get, kbd_set, chin, state_path: str = STATE_PATH):
        self.kbd_get = kbd_get
        self.kbd_set = kbd_set
        self.chin = chin
        self.state_path = state_path

    @staticmethod
    def _valid(v) -> bool:
        return isinstance(v, int) and 0 <= v <= KEYBOARD_MAX

    @staticmethod
    def _fallback(profile: dict) -> int:
        v = profile.get("kb_brightness")
        return v if isinstance(v, int) and 0 <= v <= KEYBOARD_MAX else FALLBACK_KEYBOARD

    def _load(self) -> dict:
        try:
            with open(self.state_path) as fh:
                st = json.load(fh)
            return st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, st: dict | None) -> None:
        if st is None:
            try:
                os.unlink(self.state_path)
            except OSError:
                pass
            return
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as fh:
            json.dump(st, fh)

    def status(self) -> dict:
        st = self._load()
        return {"applied": st.get("mode"), "keyboard_saved": st.get("kbd_saved")}

    def reconcile(self, profile: dict, on_battery: bool | None) -> dict:
        """Bring the lights in line with (profile, power). Idempotent."""
        if on_battery is None:
            return {"changed": False, "reason": "power state unreadable"}
        mode = mode_of(profile)
        st = self._load()
        active = st.get("mode")
        want = mode if (on_battery and mode != "keep") else None
        if want == active:
            return {"changed": False, "mode": active}

        actions = []
        # Leaving a battery mode: give back what was there.
        if active and not want:
            raw = self.kbd_get()
            cur = raw if self._valid(raw) else None
            saved = st.get("kbd_saved")
            if not self._valid(saved):
                saved = self._fallback(profile)
            if raw is not None and (cur is None or cur == st.get("kbd_set")):
                self.kbd_set(saved)
                actions.append(f"keyboard {cur} -> {saved}")
            elif cur is not None:
                actions.append(f"keyboard left at {cur} (changed on battery)")
            self.chin(profile)
            actions.append("chin bar: saved profile")
            self._save(None)
            return {"changed": True, "mode": None, "actions": actions}

        # Entering (or switching between) battery modes.
        if active:
            saved = st.get("kbd_saved")
        else:
            raw = self.kbd_get()
            saved = raw if self._valid(raw) else (None if raw is None else self._fallback(profile))
        target = None
        if saved is not None:
            target = 0 if want == "off" else min(saved, DIM_KEYBOARD)
            self.kbd_set(target)
            actions.append(f"keyboard {saved} -> {target}")
        self.chin({**profile, **chin_override(profile, True)})
        actions.append(f"chin bar: {want}")
        self._save({"mode": want, "kbd_saved": saved, "kbd_set": target})
        return {"changed": True, "mode": want, "actions": actions}
