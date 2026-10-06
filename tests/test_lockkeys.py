# SPDX-License-Identifier: MIT
"""Lock keys: set, never toggle.

Control Center's Num Lock switch reads the state and presses the key only when
it differs. A keypress is a toggle, so every rule here exists to keep "set"
from degrading into "toggle": never press when the state already matches,
never press when the state cannot be read, and report a press the keyboard did
not follow as the failure it is. And lock state is not drift -- the user
pressing Num Lock must never raise a banner offering to undo it.
"""

import os
import struct
import tempfile
import unittest
from unittest import mock

from hydroc import lockkeys
from hydroc.hardware import Hardware

DEVICES = """\
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
S: Sysfs=/devices/LNXSYSTM:00/LNXPWRBN:00/input/input0
H: Handlers=kbd event0

I: Bus=0011 Vendor=0001 Product=0001 Version=ab83
N: Name="AT Translated Set 2 keyboard"
P: Phys=isa0060/serio0/input0
S: Sysfs=/devices/platform/i8042/serio0/input/input3
H: Handlers=sysrq kbd leds event3

I: Bus=0005 Vendor=3554 Product=f605 Version=0001
N: Name="ProtoArc XK01 Keyboard"
S: Sysfs=/devices/virtual/misc/uhid/0005:3554:F605.0005/input/input26
H: Handlers=sysrq kbd leds event26
"""


class FindKeyboardTest(unittest.TestCase):

    def test_resolves_the_builtin_keyboard_by_name(self):
        self.assertEqual(lockkeys.find_keyboard(devices=DEVICES),
                         ("/dev/input/event3", "input3"))

    def test_an_external_keyboard_is_not_mistaken_for_it(self):
        self.assertIsNone(lockkeys.find_keyboard(
            devices=DEVICES.replace("AT Translated Set 2 keyboard", "gone")))


class PressEventsTest(unittest.TestCase):

    def test_one_full_press_down_sync_up_sync(self):
        data = lockkeys.press_events(69)
        size = struct.calcsize(lockkeys.EVENT_FORMAT)
        evs = [struct.unpack(lockkeys.EVENT_FORMAT, data[i:i + size])[2:]
               for i in range(0, len(data), size)]
        self.assertEqual(evs, [(1, 69, 1), (0, 0, 0), (1, 69, 0), (0, 0, 0)])


class SetLockTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.leds = self.tmp.name
        os.makedirs(os.path.join(self.leds, "input3::numlock"))
        self.node = os.path.join(self.leds, "event3")
        open(self.node, "wb").close()
        self.kb = (self.node, "input3")

    def tearDown(self):
        self.tmp.cleanup()

    def led(self, value):
        with open(os.path.join(self.leds, "input3::numlock", "brightness"), "w") as fh:
            fh.write(f"{value}\n")

    def pressed(self):
        return os.path.getsize(self.node) > 0

    def set(self, on, **kw):
        # The LED reader explicitly: the default would ask a live Hyprland.
        return lockkeys.set_lock("num", on, keyboard=self.kb, settle=0,
                                 read=lambda lock: lockkeys.read_led(
                                     "input3", lock, self.leds), **kw)

    def test_already_in_state_presses_nothing(self):
        self.led(1)
        r = self.set(True)
        self.assertTrue(r["ok"])
        self.assertFalse(r["changed"])
        self.assertFalse(self.pressed())

    def test_unreadable_state_refuses_to_press_blind(self):
        # No brightness file: a press would toggle from an unknown state.
        r = self.set(True)
        self.assertFalse(r["ok"])
        self.assertIsNone(r["was"])
        self.assertFalse(self.pressed())

    def test_press_is_verified_against_the_led(self):
        self.led(0)
        real_write = os.write

        def write_and_follow(fd, data):
            self.led(1)                     # the compositor processed the press
            return real_write(fd, data)
        with mock.patch.object(lockkeys.os, "write", side_effect=write_and_follow):
            r = self.set(True)
        self.assertTrue(r["ok"])
        self.assertTrue(r["changed"])
        self.assertTrue(self.pressed())

    def test_a_press_the_led_did_not_follow_is_a_failure(self):
        self.led(0)
        r = self.set(True)
        self.assertTrue(self.pressed())
        self.assertFalse(r["ok"])
        self.assertIn("did not follow", r["error"])

    def test_hyprland_state_outranks_the_led(self):
        # Measured: LED on, Hyprland's built-in keyboard off. Trusting the LED
        # saved a default without pressing the key.
        self.led(1)
        with mock.patch.object(lockkeys, "hyprland_state",
                               return_value={"num": False, "caps": False}):
            read = lockkeys.reader("input3", self.leds)
            self.assertIs(read("num"), False)

    def test_led_is_the_fallback_without_hyprland(self):
        self.led(1)
        read = lockkeys.reader("input3", self.leds, pattern="/nonexistent/*.sock")
        self.assertIs(read("num"), True)


class DefaultsTest(unittest.TestCase):

    def test_only_booleans_are_defaults(self):
        self.assertEqual(lockkeys.defaults(
            {"numlock_default": True, "capslock_default": None}), {"num": True})
        self.assertEqual(lockkeys.defaults({"numlock_default": "on"}), {})

    def test_nothing_set_touches_nothing(self):
        with mock.patch.object(lockkeys, "find_keyboard") as fk:
            self.assertEqual(lockkeys.apply_defaults({}), [])
        fk.assert_not_called()

    def test_lock_defaults_are_never_drift(self):
        hw = mock.MagicMock()
        hw.normalize.side_effect = lambda d: dict(d)
        desired = {"numlock_default": True, "capslock_default": False}
        self.assertEqual(Hardware.drift(hw, desired, actual={"fn_lock": False}), {})


class GraphicalSessionsTest(unittest.TestCase):

    def test_display_sessions_only(self):
        with tempfile.TemporaryDirectory() as d:
            for sid, typ, cls in (("1", "unspecified", "manager"),
                                  ("3", "wayland", "user"),
                                  ("4", "tty", "user"),
                                  ("c1", "wayland", "greeter")):
                with open(os.path.join(d, sid), "w") as fh:
                    fh.write(f"UID=1000\nTYPE={typ}\nCLASS={cls}\n")
            self.assertEqual(lockkeys.graphical_sessions(d), {"3", "c1"})

    def test_missing_directory_is_empty(self):
        self.assertEqual(lockkeys.graphical_sessions("/nonexistent/hydroc"), set())


if __name__ == "__main__":
    unittest.main()
