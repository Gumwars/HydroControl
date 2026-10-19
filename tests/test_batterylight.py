# SPDX-License-Identifier: MIT
"""Battery lighting: dim on unplug, give back exactly what was there on plug-in.

Rules pinned here: "keep" touches nothing; the keyboard level before dimming
is what comes back, even through a dim -> off switch on battery; a level the
user chose on battery is not undone; USB-C counts as external power; and the
boot-time chin bar apply does not relight a bar battery lighting switched off.
"""

import os
import tempfile
import unittest

from hydroc import batterylight as bl


class FakeHW:
    def __init__(self, level=34):
        self.level = level
        self.chin_calls = []

    def get(self):
        return self.level

    def set(self, n):
        self.level = n

    def chin(self, profile):
        self.chin_calls.append({k: profile.get(k) for k in ("chin_mode", "chin_brightness")})
        return {"ok": True}


class ReconcileTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hw = FakeHW()
        self.b = bl.BatteryLighting(self.hw.get, self.hw.set, self.hw.chin,
                                    os.path.join(self.tmp.name, "st.json"))
        self.profile = {"chin_mode": "static", "chin_brightness": 100}

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, mode, on_battery):
        return self.b.reconcile({**self.profile, bl.KEY: mode}, on_battery)

    def test_keep_touches_nothing(self):
        self.assertFalse(self.run_("keep", True)["changed"])
        self.assertEqual((self.hw.level, self.hw.chin_calls), (34, []))

    def test_off_on_battery_then_restore_on_ac(self):
        self.run_("off", True)
        self.assertEqual(self.hw.level, 0)
        self.assertEqual(self.hw.chin_calls[-1]["chin_mode"], "off")
        self.run_("off", False)
        self.assertEqual(self.hw.level, 34)
        self.assertEqual(self.hw.chin_calls[-1], {"chin_mode": "static", "chin_brightness": 100})

    def test_dim_caps_and_never_brightens(self):
        self.run_("dim", True)
        self.assertEqual(self.hw.level, bl.DIM_KEYBOARD)
        self.assertEqual(self.hw.chin_calls[-1]["chin_brightness"], bl.DIM_CHIN)
        hw = FakeHW(level=4)
        b = bl.BatteryLighting(hw.get, hw.set, hw.chin, os.path.join(self.tmp.name, "b.json"))
        b.reconcile({bl.KEY: "dim"}, True)
        self.assertEqual(hw.level, 4)

    def test_repeat_is_idempotent(self):
        self.run_("off", True)
        calls = len(self.hw.chin_calls)
        self.assertFalse(self.run_("off", True)["changed"])
        self.assertEqual(len(self.hw.chin_calls), calls)

    def test_dim_then_off_on_battery_keeps_the_original_level(self):
        self.run_("dim", True)
        self.run_("off", True)
        self.run_("off", False)
        self.assertEqual(self.hw.level, 34)

    def test_a_level_the_user_chose_on_battery_is_not_undone(self):
        self.run_("off", True)
        self.hw.level = 20                      # user turned it up while unplugged
        r = self.run_("off", False)
        self.assertEqual(self.hw.level, 20)
        self.assertIn("left at 20", " ".join(r["actions"]))

    def test_unreadable_power_does_nothing(self):
        self.assertFalse(self.run_("off", None)["changed"])
        self.assertEqual(self.hw.level, 34)

    def test_missing_keyboard_still_handles_the_chin_bar(self):
        hw = FakeHW(level=None)
        b = bl.BatteryLighting(hw.get, hw.set, hw.chin, os.path.join(self.tmp.name, "n.json"))
        b.reconcile({bl.KEY: "off"}, True)
        self.assertIsNone(hw.level)
        self.assertEqual(hw.chin_calls[-1]["chin_mode"], "off")

    def test_out_of_range_readback_is_not_trusted(self):
        # Measured: this keyboard reads 100 against a settable 0..50.
        hw = FakeHW(level=100)
        b = bl.BatteryLighting(hw.get, hw.set, hw.chin, os.path.join(self.tmp.name, "r.json"))
        prof = {bl.KEY: "off", "kb_brightness": 30}
        b.reconcile(prof, True)
        self.assertEqual(hw.level, 0)
        b.reconcile(prof, False)
        self.assertEqual(hw.level, 30)

    def test_out_of_range_with_no_remembered_level_uses_the_fallback(self):
        hw = FakeHW(level=100)
        b = bl.BatteryLighting(hw.get, hw.set, hw.chin, os.path.join(self.tmp.name, "f.json"))
        b.reconcile({bl.KEY: "dim"}, True)
        b.reconcile({bl.KEY: "dim"}, False)
        self.assertEqual(hw.level, bl.FALLBACK_KEYBOARD)


class PowerSupplyTest(unittest.TestCase):

    def supplies(self, d, items):
        for name, kind, online in items:
            os.makedirs(os.path.join(d, name))
            with open(os.path.join(d, name, "type"), "w") as fh:
                fh.write(kind + "\n")
            if online is not None:
                with open(os.path.join(d, name, "online"), "w") as fh:
                    fh.write(online + "\n")

    def test_usb_c_charging_counts_as_external_power(self):
        with tempfile.TemporaryDirectory() as d:
            self.supplies(d, [("AC0", "Mains", "0"), ("BAT0", "Battery", None),
                              ("ucsi-source-psy-USBC000:001", "USB", "1")])
            self.assertTrue(bl.on_external_power(d))

    def test_all_offline_is_battery(self):
        with tempfile.TemporaryDirectory() as d:
            self.supplies(d, [("AC0", "Mains", "0"), ("BAT0", "Battery", None)])
            self.assertIs(bl.on_external_power(d), False)

    def test_no_supply_information_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            self.supplies(d, [("BAT0", "Battery", None)])
            self.assertIsNone(bl.on_external_power(d))


class ChinOverrideTest(unittest.TestCase):

    def test_boot_apply_keeps_the_bar_dark_on_battery(self):
        self.assertEqual(bl.chin_override({bl.KEY: "off"}, True), {"chin_mode": "off"})

    def test_no_override_on_ac_or_keep(self):
        self.assertIsNone(bl.chin_override({bl.KEY: "off"}, False))
        self.assertIsNone(bl.chin_override({bl.KEY: "keep"}, True))


if __name__ == "__main__":
    unittest.main()
