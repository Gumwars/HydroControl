# SPDX-License-Identifier: MIT
"""The pre-apply hardware capture.

hydroc-apply runs at boot and hydroc-resume after every resume, and both write
stored intent into the hardware before anything reads it. Asked on 2026-09-24
whether the charging profile at 0x07A6 survives a reboot, the honest answer was
that we could not tell -- our own boot service overwrites it first. Every
observation we had was taken after our own write.

Two properties matter.

It must record what was actually there, and it must never be able to stop
settings being restored. A diagnostic that can throw during early boot, or that
fails because /var/lib is not writable yet, would trade a real feature for an
observation -- so every path here swallows and continues.

And a difference must mean something. If it reported a difference on every
boot, the record would be ignored within a week, which is the fate of every
warning that cries wolf.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from hydroc import found


class DifferencesTest(unittest.TestCase):

    def test_agreement_reports_nothing(self):
        state = {"cpu_pl1": 35, "charge_profile": "stationary"}
        self.assertEqual(found.differences(state, dict(state)), [])

    def test_a_setting_the_hardware_did_not_keep_is_reported(self):
        d = found.differences({"charge_profile": "high_capacity"},
                              {"charge_profile": "stationary"})
        self.assertEqual(d, [{"setting": "charge_profile",
                              "intent": "stationary",
                              "found": "high_capacity"}])

    def test_settings_the_hardware_cannot_report_are_skipped(self):
        """Intent carries keys read_state has no reading for. Those are not
        disagreements, they are absences."""
        self.assertEqual(found.differences({}, {"chin_color": "#FFFFFF"}), [])

    def test_readings_that_are_not_settings_are_ignored(self):
        """custom_profile is bookkeeping. Reporting it every boot would teach
        everyone to skip the whole record."""
        self.assertEqual(
            found.differences({"custom_profile": False},
                              {"custom_profile": True}), [])

    def test_curves_compare_by_value(self):
        a = [[45, 40, 25], [50, 45, 30]]
        self.assertEqual(found.differences({"fan_curve_cpu": a},
                                           {"fan_curve_cpu": list(a)}), [])
        self.assertTrue(found.differences({"fan_curve_cpu": a},
                                          {"fan_curve_cpu": [[0, 0, 0]]}))

    def test_output_is_ordered_so_two_boots_can_be_diffed(self):
        d = found.differences({"z": 1, "a": 1, "m": 1}, {"z": 2, "a": 2, "m": 2})
        self.assertEqual([x["setting"] for x in d], ["a", "m", "z"])


class RecordTest(unittest.TestCase):

    def test_record_carries_the_reason(self):
        r = found.record({"cpu_pl1": 35}, {"cpu_pl1": 35}, "boot")
        self.assertEqual(r["reason"], "boot")
        self.assertIn("when", r)
        self.assertEqual(r["differences"], [])


class PersistenceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "sub", "found.json")

    def test_round_trip(self):
        rec = found.record({"cpu_pl1": 35}, {"cpu_pl1": 45}, "boot")
        self.assertEqual(found.write(rec, self.path), self.path)
        back = found.read(self.path)
        self.assertEqual(back["differences"][0]["setting"], "cpu_pl1")

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        found.write(found.record({}, {}, "boot"), self.path)
        leftovers = [f for f in os.listdir(os.path.dirname(self.path))
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_unwritable_location_returns_none_rather_than_raising(self):
        self.assertIsNone(found.write(found.record({}, {}, "boot"),
                                      "/proc/nope/found.json"))

    def test_missing_file_reads_as_none(self):
        self.assertIsNone(found.read(os.path.join(self.tmp.name, "absent")))

    def test_corrupt_file_reads_as_none(self):
        with open(self.path.replace("/sub", ""), "w") as fh:
            fh.write("{not json")
        self.assertIsNone(found.read(self.path.replace("/sub", "")))

    def test_a_json_list_is_not_a_record(self):
        p = os.path.join(self.tmp.name, "list.json")
        with open(p, "w") as fh:
            json.dump([1, 2, 3], fh)
        self.assertIsNone(found.read(p))


class CaptureTest(unittest.TestCase):
    """capture() runs immediately before the boot-time apply. It must never be
    the reason settings fail to be restored."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "found.json")

    def test_captures_what_the_hardware_held(self):
        hw = mock.Mock()
        hw.read_state.return_value = {"charge_profile": "high_capacity"}
        rec = found.capture(hw, {"charge_profile": "stationary"}, "boot",
                            self.path)
        self.assertEqual(rec["found"]["charge_profile"], "high_capacity")
        self.assertEqual(rec["differences"][0]["setting"], "charge_profile")
        self.assertEqual(rec["saved_to"], self.path)

    def test_a_failing_read_does_not_raise(self):
        hw = mock.Mock()
        hw.read_state.side_effect = RuntimeError("EC unavailable")
        rec = found.capture(hw, {"cpu_pl1": 35}, "boot", self.path)
        self.assertEqual(rec["found"], {})
        self.assertEqual(rec["differences"], [])

    def test_an_unwritable_path_still_returns_the_finding(self):
        """The difference is worth reporting even when it cannot be saved."""
        hw = mock.Mock()
        hw.read_state.return_value = {"cpu_pl1": 20}
        rec = found.capture(hw, {"cpu_pl1": 35}, "boot", "/proc/nope/x.json")
        self.assertIsNone(rec["saved_to"])
        self.assertEqual(rec["differences"][0]["found"], 20)


if __name__ == "__main__":
    unittest.main()
