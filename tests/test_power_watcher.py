# SPDX-License-Identifier: MIT
"""The power watcher's timing: act on a change, re-assert only while engaged,
and let battery lighting see Power Saver's effect."""

import unittest
from unittest import mock

from hydroc import server


class FakeSaver:
    def __init__(self, active=False):
        self.active = active
        self.calls = 0

    def reconcile(self, profile, on_battery):
        self.calls += 1
        return {"changed": self.calls == 1, "active": self.active, "actions": []}


class SaverTimingTest(unittest.TestCase):

    def watcher(self, saver):
        p = mock.patch.object(server, "_power_saver", saver)
        p.start()
        self.addCleanup(p.stop)
        return server.PowerWatcher()

    def test_acts_once_per_change(self):
        s = FakeSaver()
        w = self.watcher(s)
        prof = {"power_saver": "auto"}
        w._saver(prof, False, 0.0)
        w._saver(prof, False, 10_000.0)
        self.assertEqual(s.calls, 1, "re-ran with nothing changed and nothing engaged")
        w._saver(prof, True, 10_001.0)                       # plugged in
        self.assertEqual(s.calls, 2)

    def test_reasserts_while_engaged(self):
        s = FakeSaver(active=True)
        w = self.watcher(s)
        prof = {"power_saver": "auto"}
        w._saver(prof, False, 0.0)
        w._saver(prof, False, w.REASSERT_S - 1)
        self.assertEqual(s.calls, 1)
        w._saver(prof, False, w.REASSERT_S)
        self.assertEqual(s.calls, 2)

    def test_mode_change_acts_immediately(self):
        s = FakeSaver()
        w = self.watcher(s)
        w._saver({"power_saver": "auto"}, True, 0.0)
        w._saver({"power_saver": "on"}, True, 1.0)
        self.assertEqual(s.calls, 2)


class LightKeyTest(unittest.TestCase):

    def test_lighting_is_off_while_power_saver_engaged(self):
        prof = {"power_saver": "auto", "battery_lighting": "keep"}
        self.assertEqual(server.PowerWatcher.light_key(prof, False), (False, "off"))

    def test_lighting_follows_its_own_setting_on_ac(self):
        prof = {"power_saver": "auto", "battery_lighting": "dim"}
        self.assertEqual(server.PowerWatcher.light_key(prof, True), (True, "dim"))

    def test_power_saver_off_leaves_lighting_alone(self):
        prof = {"power_saver": "off", "battery_lighting": "dim"}
        self.assertEqual(server.PowerWatcher.light_key(prof, False), (False, "dim"))


if __name__ == "__main__":
    unittest.main()
