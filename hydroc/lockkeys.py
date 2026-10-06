# SPDX-License-Identifier: MIT
"""
hydroc.lockkeys — Num Lock and Caps Lock, read and set on the built-in keyboard.

Control Center's "Num Lock" toggle is not an EC feature. Decompiled
(`GCUService.exe`, `MySettingManager.UserSetNumpad`), it reads
`GetKeyState(VK_NUMLOCK)` and, if that disagrees with the request, synthesises
one Num Lock keypress. Nothing is stored in firmware and nothing is restored at
boot -- Windows does that itself (`InitialKeyboardIndicators`). Caps Lock has
no setting at all over there, only an OSD.

The Linux equivalent has one trap. The lock state does not live in the kernel
once a compositor is running: it lives in the compositor's xkb state, and
Hyprland keeps **one per keyboard** -- an external keyboard can have Num Lock on
while the built-in one has it off. A virtual uinput keyboard (what `ydotool`
and friends create) toggles only its *own* state, which changes nothing anyone
types on. So the keypress is written into the built-in keyboard's own evdev
node, where the input core delivers it to every reader as if it had come from
that keyboard. It works the same on a VT, a greeter, and any compositor.

State is read back from the keyboard's LED. The compositor drives the LEDs from
its xkb state for that device, so the LED is the one place outside the
compositor that reports the state that matters. Every set is verified against
it, and a press that did not move the LED is reported as a failure.

Defaults (`numlock_default`, `capslock_default`: True / False / None) are
applied at boot and resume by `cli apply`, and -- because a compositor starting
up resets every keyboard to its own default -- again when a graphical session
starts, by `SessionWatcher` inside the daemon. None means leave it alone.

Lock state is deliberately **not** part of `Hardware.read_state()`: the user
pressing Num Lock is not drift, and a banner offering to undo it would be wrong.
"""

from __future__ import annotations

import glob
import json
import os
import socket
import struct
import threading
import time

KEYBOARD_NAME = "AT Translated Set 2 keyboard"

EVENT_FORMAT = "llHHi"                       # struct input_event
EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0

# lock -> (key code, LED name, profile key)
LOCKS = {
    "num": (69, "numlock", "numlock_default"),     # KEY_NUMLOCK
    "caps": (58, "capslock", "capslock_default"),  # KEY_CAPSLOCK
}

LEDS = "/sys/class/leds"
HYPR_SOCKETS = "/run/user/*/hypr/*/.socket.sock"
SESSIONS = "/run/systemd/sessions"


class LockKeyError(Exception):
    pass


def find_keyboard(name: str = KEYBOARD_NAME,
                  devices: str | None = None) -> tuple[str, str] | None:
    """(evdev node, input name) for the keyboard called `name`, or None.

    By name, never by number: event and input numbers move between boots.
    `devices` is the text of /proc/bus/input/devices, for tests.
    """
    if devices is None:
        try:
            with open("/proc/bus/input/devices") as fh:
                devices = fh.read()
        except OSError:
            return None
    for block in devices.split("\n\n"):
        if f'N: Name="{name}"' not in block:
            continue
        event = sysfs = None
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                event = next((t for t in line.split("=", 1)[1].split()
                              if t.startswith("event")), None)
            elif line.startswith("S: Sysfs="):
                sysfs = os.path.basename(line.split("=", 1)[1].strip())
        if event and sysfs:
            return "/dev/input/" + event, sysfs
    return None


def led_path(input_name: str, lock: str, leds: str = LEDS) -> str:
    return os.path.join(leds, f"{input_name}::{LOCKS[lock][1]}", "brightness")


def read_led(input_name: str, lock: str, leds: str = LEDS) -> bool | None:
    """True/False from the LED, None when it cannot be read -- never False."""
    try:
        with open(led_path(input_name, lock, leds)) as fh:
            return int(fh.read().strip()) > 0
    except (OSError, ValueError):
        return None


def hyprland_state(name: str = KEYBOARD_NAME,
                   pattern: str = HYPR_SOCKETS) -> dict | None:
    """{"num", "caps"} for keyboard `name` as Hyprland holds it, or None.

    The LED is NOT a per-keyboard reading under Hyprland: it drives every
    keyboard's LEDs from whichever keyboard last changed state, so the
    built-in's Num Lock light can be showing an external keyboard's state.
    Measured: LED on, Hyprland reporting the built-in's Num Lock off. Where
    Hyprland runs, its own per-keyboard state is the truth.
    """
    slug = name.lower().replace(" ", "-")
    paths = sorted(glob.glob(pattern), key=lambda p: os.path.getmtime(p)
                   if os.path.exists(p) else 0, reverse=True)
    for path in paths:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(path)
                s.sendall(b"j/devices")
                chunks = []
                while True:
                    c = s.recv(65536)
                    if not c:
                        break
                    chunks.append(c)
            kbs = json.loads(b"".join(chunks)).get("keyboards", [])
        except (OSError, ValueError):
            continue
        for k in kbs:
            if k.get("name") == slug:
                return {"num": bool(k.get("numLock")), "caps": bool(k.get("capsLock"))}
    return None


def reader(input_name: str, leds: str = LEDS, pattern: str = HYPR_SOCKETS):
    """lock -> True/False/None: Hyprland's state when it runs, else the LED.

    The LED fallback is right on a VT and for compositors that keep the LEDs
    per keyboard; it is only Hyprland that is known to sync them.
    """
    def read(lock: str) -> bool | None:
        h = hyprland_state(pattern=pattern)
        if h is not None:
            return h[lock]
        return read_led(input_name, lock, leds)
    return read


def press_events(code: int) -> bytes:
    """One full keypress: down, sync, up, sync."""
    def ev(etype, c, value):
        return struct.pack(EVENT_FORMAT, 0, 0, etype, c, value)
    return (ev(EV_KEY, code, 1) + ev(EV_SYN, SYN_REPORT, 0)
            + ev(EV_KEY, code, 0) + ev(EV_SYN, SYN_REPORT, 0))


def state(keyboard: tuple[str, str] | None = None) -> dict:
    """{"available", "device", "num", "caps"} -- lock values are None if unknown."""
    kb = keyboard or find_keyboard()
    if not kb:
        return {"available": False, "device": None, "num": None, "caps": None,
                "error": f'keyboard "{KEYBOARD_NAME}" not found'}
    out = {"available": True, "device": kb[0],
           "source": "hyprland" if hyprland_state() is not None else "led"}
    read = reader(kb[1])
    for lock in LOCKS:
        out[lock] = read(lock)
    return out


def set_lock(lock: str, on: bool, keyboard: tuple[str, str] | None = None,
             settle: float = 0.15, leds: str = LEDS, read=None) -> dict:
    """Make `lock` read `on`. Presses the key only if it currently differs.

    Returns {"lock", "was", "now", "changed", "ok", "error"}.
    """
    if lock not in LOCKS:
        raise LockKeyError(f"unknown lock {lock!r}")
    code = LOCKS[lock][0]
    res = {"lock": lock, "was": None, "now": None, "changed": False,
           "ok": False, "error": ""}
    kb = keyboard or find_keyboard()
    if not kb:
        res["error"] = f'keyboard "{KEYBOARD_NAME}" not found'
        return res
    node, input_name = kb
    read = read or reader(input_name, leds)

    was = read(lock)
    res["was"] = was
    if was is None:
        # Pressing blind would toggle -- the opposite of setting.
        res["error"] = "lock state unreadable -- not pressing blind"
        return res
    if was == on:
        res.update(now=was, ok=True)
        return res

    try:
        fd = os.open(node, os.O_WRONLY)
        try:
            os.write(fd, press_events(code))
        finally:
            os.close(fd)
    except OSError as e:
        res["error"] = f"{node}: {e}"
        return res

    # The compositor updates its state after it processes the press.
    deadline = time.monotonic() + max(settle, 0) * 4
    now = read(lock)
    while now != on and time.monotonic() < deadline:
        time.sleep(settle / 3 if settle else 0)
        now = read(lock)
    res["now"] = now
    res["changed"] = now != was
    res["ok"] = now == on
    if not res["ok"]:
        res["error"] = "key sent but the lock state did not follow"
    return res


def defaults(profile: dict) -> dict:
    """{lock: True/False} for every lock the profile sets a default for."""
    out = {}
    for lock, (_, _, key) in LOCKS.items():
        v = profile.get(key)
        if isinstance(v, bool):
            out[lock] = v
    return out


def apply_defaults(profile: dict, keyboard=None, **kw) -> list[dict]:
    """Apply the profile's lock defaults. Empty list when none are set."""
    want = defaults(profile)
    if not want:
        return []
    kb = keyboard or find_keyboard()
    return [set_lock(lock, on, keyboard=kb, **kw) for lock, on in want.items()]


def graphical_sessions(root: str = SESSIONS) -> set[str]:
    """IDs of logind sessions that run a display server (user or greeter)."""
    out = set()
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for sid in names:
        try:
            with open(os.path.join(root, sid)) as fh:
                kv = dict(line.rstrip("\n").split("=", 1)
                          for line in fh if "=" in line)
        except OSError:
            continue
        if kv.get("TYPE") in ("wayland", "x11") \
                and kv.get("CLASS") in ("user", "greeter"):
            out.add(sid)
    return out


class SessionWatcher(threading.Thread):
    """Re-applies lock defaults when a graphical session starts.

    A compositor starting up resets each keyboard to its own default
    (Hyprland: `input.numlock_by_default`), which silently undoes what boot
    applied. logind writes one file per session under /run/systemd/sessions,
    so polling that directory is cheap and needs no D-Bus.

    Applies twice per new session -- the compositor may not have taken the
    keyboard yet at the first attempt -- and never again after that, so a
    user pressing Num Lock is never fought. Sessions already present when the
    daemon starts are left alone for the same reason.
    """

    POLL = 2.0
    ATTEMPTS = (3.0, 8.0)        # seconds after the session appears

    def __init__(self, get_profile, root: str = SESSIONS) -> None:
        super().__init__(daemon=True, name="lockkeys-sessions")
        self._get_profile = get_profile
        self._root = root
        self._stop = threading.Event()
        self.last: list[dict] = []
        self.error: str | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        seen = graphical_sessions(self._root)
        pending: dict[str, list[float]] = {}
        while not self._stop.wait(self.POLL):
            now = time.monotonic()
            for sid in graphical_sessions(self._root) - seen:
                seen.add(sid)
                pending[sid] = [now + d for d in self.ATTEMPTS]
            for sid, times in list(pending.items()):
                if times and now >= times[0]:
                    times.pop(0)
                    try:
                        profile = self._get_profile()
                        self.last = apply_defaults(profile)
                        self.error = None
                    except Exception as e:                 # noqa: BLE001
                        self.error = str(e)
                if not times:
                    del pending[sid]
