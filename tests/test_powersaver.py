# SPDX-License-Identifier: MIT
"""Power Saver engages, releases, and gives back exactly what it found.

Run against a fake sysfs and fake iw / powerprofilesctl, so the policy is
tested without root: what it changes, what it records, and the rule shared with
battery lighting -- a setting the user changed on battery is theirs, not ours
to undo.
"""

import os
import subprocess
import tempfile
import unittest

from hydroc import powersaver
from hydroc.powersaver import PowerSaver


class FakeCommands:
    """iw and powerprofilesctl, with state."""

    def __init__(self, wifi="off", ppd="balanced"):
        self.wifi = {"wlan0": wifi}
        self.ppd = ppd
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        out, rc = "", 0
        if args[:2] == ["iw", "dev"]:
            iface, verb = args[2], args[3]
            if verb == "get":
                out = f"Power save: {self.wifi[iface]}\n"
            else:
                self.wifi[iface] = args[5]
        elif args[0] == "powerprofilesctl":
            if args[1] == "get":
                out = self.ppd + "\n"
            else:
                self.ppd = args[2]
        return subprocess.CompletedProcess(args, rc, out, "")


def _put(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(value)


def _get(path):
    with open(path) as fh:
        return fh.read().strip()


class PowerSaverTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sys = os.path.join(self.tmp.name, "sys")
        self.audio = os.path.join(self.sys, "module/snd_hda_intel/parameters/power_save")
        _put(self.audio, "0")
        os.makedirs(os.path.join(self.sys, "class/net/wlan0/wireless"))
        self.pci_on = os.path.join(self.sys, "bus/pci/devices/0000:00:14.0/power/control")
        self.pci_auto = os.path.join(self.sys, "bus/pci/devices/0000:01:00.0/power/control")
        _put(self.pci_on, "on")
        _put(self.pci_auto, "auto")
        self.bl = os.path.join(self.sys, "class/backlight/panel")
        _put(os.path.join(self.bl, "max_brightness"), "800")
        _put(os.path.join(self.bl, "brightness"), "800")
        self.cmd = FakeCommands()
        self.ps = PowerSaver(sys_root=self.sys,
                             state_path=os.path.join(self.tmp.name, "run/ps.json"),
                             run=self.cmd, which=lambda name: "/usr/bin/" + name)
        self.auto = {"power_saver": "auto"}

    def brightness(self):
        return _get(os.path.join(self.bl, "brightness"))

    # -- policy --------------------------------------------------------------

    def test_wanted_follows_mode_and_power(self):
        self.assertTrue(powersaver.wanted({"power_saver": "auto"}, True))
        self.assertFalse(powersaver.wanted({"power_saver": "auto"}, False))
        self.assertIsNone(powersaver.wanted({"power_saver": "auto"}, None))
        self.assertTrue(powersaver.wanted({"power_saver": "on"}, False))
        self.assertFalse(powersaver.wanted({"power_saver": "off"}, True))
        self.assertEqual(powersaver.mode_of({"power_saver": "bogus"}), "auto")
        self.assertEqual(powersaver.mode_of({}), powersaver.DEFAULT)

    def test_lights_forced_off_only_while_wanted(self):
        p = {"power_saver": "auto", "battery_lighting": "dim"}
        self.assertEqual(powersaver.lighting_profile(p, True)["battery_lighting"], "off")
        self.assertEqual(powersaver.lighting_profile(p, False)["battery_lighting"], "dim")

    def test_unreadable_power_changes_nothing(self):
        r = self.ps.reconcile(self.auto, None)
        self.assertFalse(r["changed"])
        self.assertEqual(_get(self.audio), "0")

    # -- engage / release ----------------------------------------------------

    def test_engage_sets_every_lever(self):
        r = self.ps.reconcile(self.auto, True)
        self.assertTrue(r["active"])
        self.assertEqual(r["errors"], [])
        self.assertEqual(_get(self.audio), "1")
        self.assertEqual(self.cmd.wifi["wlan0"], "on")
        self.assertEqual(_get(self.pci_on), "auto")
        self.assertEqual(self.brightness(), "400")
        self.assertEqual(self.cmd.ppd, "power-saver")

    def test_release_gives_everything_back(self):
        self.ps.reconcile(self.auto, True)
        r = self.ps.reconcile(self.auto, False)
        self.assertFalse(r["active"])
        self.assertEqual(_get(self.audio), "0")
        self.assertEqual(self.cmd.wifi["wlan0"], "off")
        self.assertEqual(_get(self.pci_on), "on")
        self.assertEqual(self.brightness(), "800")
        self.assertEqual(self.cmd.ppd, "balanced")
        self.assertFalse(self.ps.engaged())

    def test_devices_already_auto_are_not_touched_on_release(self):
        self.ps.reconcile(self.auto, True)
        self.ps.reconcile(self.auto, False)
        self.assertEqual(_get(self.pci_auto), "auto")

    def test_engaging_twice_is_a_no_op(self):
        self.ps.reconcile(self.auto, True)
        r = self.ps.reconcile(self.auto, True)
        self.assertFalse(r["changed"])

    # -- brightness: a cap, never a raise, and the user's own change stays ---

    def test_brightness_below_cap_is_never_raised(self):
        _put(os.path.join(self.bl, "brightness"), "80")
        self.ps.reconcile(self.auto, True)
        self.assertEqual(self.brightness(), "80")
        self.ps.reconcile(self.auto, False)
        self.assertEqual(self.brightness(), "80")

    def test_brightness_changed_on_battery_is_left_alone(self):
        self.ps.reconcile(self.auto, True)
        _put(os.path.join(self.bl, "brightness"), "200")     # user dims further
        r = self.ps.reconcile(self.auto, False)
        self.assertEqual(self.brightness(), "200")
        self.assertIn("brightness left as you set it", r["actions"])

    def test_profile_changed_on_battery_is_left_alone(self):
        self.ps.reconcile(self.auto, True)
        self.cmd.ppd = "performance"                         # user's choice
        self.ps.reconcile(self.auto, False)
        self.assertEqual(self.cmd.ppd, "performance")

    def test_profile_already_power_saver_is_not_recorded(self):
        self.cmd.ppd = "power-saver"
        self.ps.reconcile(self.auto, True)
        self.ps.reconcile(self.auto, False)
        self.assertEqual(self.cmd.ppd, "power-saver")

    # -- re-assert after resume ----------------------------------------------

    def test_resume_reset_is_reasserted_and_still_restored(self):
        self.ps.reconcile(self.auto, True)
        _put(self.pci_on, "on")                              # resume reset it
        self.cmd.wifi["wlan0"] = "off"                       # so did a reconnect
        r = self.ps.reconcile(self.auto, True)
        self.assertTrue(r["changed"])
        self.assertEqual(_get(self.pci_on), "auto")
        self.assertEqual(self.cmd.wifi["wlan0"], "on")
        self.ps.reconcile(self.auto, False)
        self.assertEqual(_get(self.pci_on), "on")
        self.assertEqual(self.cmd.wifi["wlan0"], "off")

    def test_reassert_never_touches_brightness(self):
        self.ps.reconcile(self.auto, True)
        _put(os.path.join(self.bl, "brightness"), "700")     # user brightens
        self.ps.reconcile(self.auto, True)
        self.assertEqual(self.brightness(), "700")

    def test_audio_timeout_set_by_someone_else_is_left_alone(self):
        # CachyOS's 20-audio-pm.rules writes 10 on unplug. Overwriting it
        # raced the rule at unplug and fought it at every re-assert.
        _put(self.audio, "10")
        self.ps.reconcile(self.auto, True)
        self.assertEqual(_get(self.audio), "10")
        self.ps.reconcile(self.auto, True)                   # re-assert
        self.assertEqual(_get(self.audio), "10")
        self.ps.reconcile(self.auto, False)
        self.assertEqual(_get(self.audio), "10")

    def test_manual_on_engages_on_ac(self):
        r = self.ps.reconcile({"power_saver": "on"}, False)
        self.assertTrue(r["active"])

    def test_missing_tools_are_skipped_not_failed(self):
        ps = PowerSaver(sys_root=self.sys,
                        state_path=os.path.join(self.tmp.name, "run/b.json"),
                        run=self.cmd, which=lambda name: None)
        r = ps.reconcile(self.auto, True)
        self.assertEqual(r["errors"], [])
        self.assertEqual(self.cmd.calls, [])


class DgpuTest(unittest.TestCase):

    def test_rtx_state_reads_runtime_pm(self):
        with tempfile.TemporaryDirectory() as d:
            _put(os.path.join(d, "power/runtime_status"), "suspended")
            self.assertEqual(powersaver.rtx_state(d), "suspended")
        self.assertIsNone(powersaver.rtx_state(None))


if __name__ == "__main__":
    unittest.main()
