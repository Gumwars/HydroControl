# SPDX-License-Identifier: MIT
"""
hydroc.hotkeys — the profile button, delivered as an input event.

`uniwill-laptop` maps WMI event 0xB0 to KEY_F14 on the "Uniwill WMI hotkeys"
input device. That is the only trace the profile button leaves: no EC register
changes, and the LED does not move (DESIGN.md §3.7). Being the thing that
listens for it is what makes the button work at all.

Runs as a daemon thread inside `hydroc.server`. It reads evdev directly rather
than going through the desktop, so it works on a headless login and does not
care whether anything else has grabbed F14.
"""

from __future__ import annotations

import os
import struct
import threading

DEVICE_NAME = "Uniwill WMI hotkeys"

EVENT_FORMAT = "llHHi"                       # struct input_event
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)
EV_KEY = 0x01
KEY_F14 = 184
KEY_PRESS = 1


def find_device(name: str = DEVICE_NAME) -> str | None:
    """Resolve the evdev node by NAME. event numbers move between boots."""
    try:
        with open("/proc/bus/input/devices") as fh:
            blocks = fh.read().split("\n\n")
    except OSError:
        return None
    for block in blocks:
        if f'Name="{name}"' not in block:
            continue
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for tok in line.split("=", 1)[1].split():
                    if tok.startswith("event"):
                        return "/dev/input/" + tok
    return None


class ProfileButton(threading.Thread):
    """Calls `on_press()` every time the profile button is pressed.

    A missing device is not an error worth crashing over -- the button only
    emits KEY_F14 when the BIOS has it set to performance modes rather than fan
    profiles, and plenty of machines will never see one. The thread reports why
    it is idle through `status()` so the UI can say so instead of silently
    offering a control that cannot work.
    """

    def __init__(self, on_press) -> None:
        super().__init__(daemon=True, name="profile-button")
        self._on_press = on_press
        self._error: str | None = None
        self._path: str | None = None
        self._presses = 0
        self._stop = threading.Event()

    def status(self) -> dict:
        return {"listening": self._path is not None and self._error is None,
                "device": self._path, "error": self._error,
                "presses": self._presses}

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._path = find_device()
        if not self._path:
            self._error = (f'input device "{DEVICE_NAME}" not found -- is '
                           "uniwill-laptop loaded?")
            return
        try:
            fh = open(self._path, "rb")
        except OSError as e:
            self._error = f"{self._path}: {e}"
            return

        with fh:
            while not self._stop.is_set():
                try:
                    data = fh.read(EVENT_SIZE)
                except OSError as e:
                    self._error = str(e)
                    return
                if not data or len(data) < EVENT_SIZE:
                    self._error = "input device closed"
                    return
                _, _, etype, code, value = struct.unpack(EVENT_FORMAT, data)
                if etype != EV_KEY or code != KEY_F14 or value != KEY_PRESS:
                    continue
                self._presses += 1
                try:
                    self._on_press()
                except Exception as e:
                    # A failed apply must not kill the listener -- the next
                    # press should still work.
                    self._error = f"press handler failed: {e}"
