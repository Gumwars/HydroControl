#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
perfmode_listen.py — watch the performance-mode button arrive as an input event.

With the BIOS set to PERFORMANCE MODE, pressing the profile button changes no
EC register at all. That is not a dead end: the button is a *notification*, not
an action. The chain is

    button -> EC raises WMI event 0xB0 (UNIWILL_OSD_PERFORMANCE_MODE_TOGGLE)
           -> uniwill-laptop maps it through sparse_keymap to KEY_F14
           -> input device "Uniwill WMI hotkeys"

and there it stops, because on Windows the OEM *service* is what decides the
next mode, writes it, and sets the LED. With no service listening, nothing
happens. That is why the fan-profile position works unattended and this one
does not: there the EC acts on its own, here it only announces.

So HydroControl does not need to find a mode register. It needs to be the
service.

This listens on the input device (resolved by NAME -- event numbers move
between boots) and reports every hotkey, sampling 0x07A6 around each press to
confirm the EC really is inert.

    sudo python3 perfmode_listen.py

Ctrl-C to stop.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEVICE_NAME = "Uniwill WMI hotkeys"

# struct input_event: 2x __kernel_ulong_t time, __u16 type, __u16 code, __s32 value
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

EV_KEY = 0x01
KEY_F14 = 184

# Keycodes uniwill_keymap can emit, so an unexpected one is named rather than
# printed as a bare integer.
KEY_NAMES = {
    184: "KEY_F14  <-- performance mode toggle",
    183: "KEY_F13",
    185: "KEY_F15",
    186: "KEY_F16",
    187: "KEY_F17",
    188: "KEY_F18",
    189: "KEY_F19",
    190: "KEY_F20",
    431: "KEY_KBDILLUMUP",
    432: "KEY_KBDILLUMDOWN",
}

REG_OEM_4 = 0x07A6


def find_device(name: str) -> str | None:
    """Resolve the evdev node by device name. event numbers are not stable."""
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


def main() -> int:
    if os.geteuid() != 0:
        print("needs root to read the input device and the EC")
        return 1

    path = find_device(DEVICE_NAME)
    if not path:
        print(f'input device "{DEVICE_NAME}" not found -- is uniwill-laptop loaded?')
        return 1

    ec = None
    try:
        from hydroc.ec import EC, ECUnavailable
        ok, _ = EC.available()
        if ok:
            ec = EC()
    except Exception:
        pass

    def oem4() -> str:
        if ec is None:
            return "n/a"
        try:
            v = ec.read(REG_OEM_4)
            return f"0x{v:02X} ({format(v, '08b')})"
        except Exception as e:
            return f"error: {e}"

    print(f"listening on {path}  ({DEVICE_NAME})")
    print(f"0x07A6 now: {oem4()}")
    print("\nPress the profile button. Every hotkey event will appear below.")
    print("Cycle through all the modes; note the LED colour each time.\n")

    presses = 0
    try:
        with open(path, "rb") as fh:
            while True:
                data = fh.read(EVENT_SIZE)
                if not data or len(data) < EVENT_SIZE:
                    break
                _, _, etype, code, value = struct.unpack(EVENT_FORMAT, data)
                if etype != EV_KEY or value != 1:      # key press only
                    continue
                presses += 1
                name = KEY_NAMES.get(code, f"keycode {code}")
                print(f"  [{presses}] {name}")
                if code == KEY_F14:
                    print(f"        0x07A6 after press: {oem4()}")
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        print(f"cannot read {path}: {e}")
        return 1

    print(f"\n=== paste this back ===")
    print(f"  hotkey presses seen: {presses}")
    print(f"  0x07A6 final: {oem4()}")
    if presses == 0:
        print("\n  No input events at all. Either the BIOS is not in performance-mode")
        print("  position, or the WMI event is not reaching uniwill-laptop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
