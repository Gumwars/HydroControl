# SPDX-License-Identifier: MIT
"""The keyboard is relit after a system sleep, at the level it should have.

It comes back from sleep dark and nothing in the host resent its lighting:
kbctrl's daemon used to, from a sleep hook, and HydroControl's resume unit
cannot -- hydroc-server owns the keyboard.
"""

import os
import tempfile
import unittest
from unittest import mock

from hydroc import batterylight, server


class Kbd:
    def __init__(self, reads=0, connected=True):
        self.reads = reads
        self.connected = connected
        self.sent = []

    def get(self):
        return self.reads if self.connected else None

    def set(self, n):
        self.sent.append(n)


class ResumeLevelTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.kbd = Kbd()
        self.bl = batterylight.BatteryLighting(
            self.kbd.get, self.kbd.set, lambda p: None,
            state_path=os.path.join(self.tmp.name, "bl.json"))

    def test_on_ac_resends_the_saved_level(self):
        r = self.bl.resume({"kb_brightness": 34})
        self.assertEqual(self.kbd.sent, [34])
        self.assertTrue(r["changed"])

    def test_without_a_saved_level_uses_the_fallback(self):
        self.bl.resume({})
        self.assertEqual(self.kbd.sent, [batterylight.FALLBACK_KEYBOARD])

    def test_battery_off_stays_off(self):
        self.kbd.reads = 34
        self.bl.reconcile({"battery_lighting": "off", "kb_brightness": 34}, True)
        self.kbd.sent.clear()
        self.bl.resume({"battery_lighting": "off", "kb_brightness": 34})
        self.assertEqual(self.kbd.sent, [0], "relit a keyboard battery lighting had off")

    def test_battery_dim_stays_dim(self):
        self.kbd.reads = 34
        self.bl.reconcile({"battery_lighting": "dim", "kb_brightness": 34}, True)
        self.kbd.sent.clear()
        self.bl.resume({"battery_lighting": "dim", "kb_brightness": 34})
        self.assertEqual(self.kbd.sent, [batterylight.DIM_KEYBOARD])

    def test_not_connected_raises_so_the_caller_retries(self):
        self.kbd.connected = False
        with self.assertRaises(OSError):
            self.bl.resume({"kb_brightness": 34})
        self.assertEqual(self.kbd.sent, [])


class FakeLight:
    def __init__(self, fail=0):
        self.fail = fail
        self.calls = 0

    def resume(self, profile):
        self.calls += 1
        if self.calls <= self.fail:
            raise OSError("not yet")
        return {"changed": True, "actions": ["re-sent"]}


class ResumeDetectTest(unittest.TestCase):

    def watcher(self, light):
        p = mock.patch.object(server, "_battery_light", light)
        p.start()
        self.addCleanup(p.stop)
        w = server.PowerWatcher()
        w._slept = 100.0
        return w

    def test_no_sleep_no_resume(self):
        w = self.watcher(FakeLight())
        with mock.patch.object(server.PowerWatcher, "sleep_offset", return_value=100.4):
            self.assertFalse(w._resumed(0.0))

    def test_sleep_is_detected_once(self):
        w = self.watcher(FakeLight())
        with mock.patch.object(server.PowerWatcher, "sleep_offset", return_value=700.0):
            self.assertTrue(w._resumed(0.0))
            self.assertFalse(w._resumed(2.0))

    def test_resume_forces_power_saver_reassert(self):
        w = self.watcher(FakeLight())
        w._asserted = 50.0
        with mock.patch.object(server.PowerWatcher, "sleep_offset", return_value=700.0):
            w._resumed(60.0)
        self.assertEqual(w._asserted, 0.0)

    def test_keyboard_retried_until_usb_settles(self):
        light = FakeLight(fail=2)
        w = self.watcher(light)
        with mock.patch.object(server.PowerWatcher, "sleep_offset", return_value=700.0):
            w._resumed(0.0)
        prof = {}
        for t in (0.0, 2.0, 4.0, 6.0):
            w._keyboard_after_resume(prof, True, t)
        self.assertEqual(light.calls, 3, "stopped retrying, or kept going after success")
        self.assertIsNone(w._kbd_resume_until)

    def test_keyboard_gives_up_after_the_window(self):
        light = FakeLight(fail=999)
        w = self.watcher(light)
        with mock.patch.object(server.PowerWatcher, "sleep_offset", return_value=700.0):
            w._resumed(0.0)
        w._keyboard_after_resume({}, True, 1.0)
        self.assertIsNotNone(w._kbd_resume_until)
        w._keyboard_after_resume({}, True, w.RESUME_RETRY_S + 1)
        self.assertIsNone(w._kbd_resume_until)

    def test_nothing_without_a_resume(self):
        light = FakeLight()
        w = self.watcher(light)
        w._keyboard_after_resume({}, True, 0.0)
        self.assertEqual(light.calls, 0)


if __name__ == "__main__":
    unittest.main()
