# SPDX-License-Identifier: MIT
"""A feature the hardware does not have is not a failed write.

The EC stores a charge threshold and never enforces it (DESIGN.md §3.2), so
hydroc16g1_descriptor no longer claims BATTERY_CHARGE_LIMIT and the driver stops
exposing charge_control_end_threshold. Profiles written before that change still
carry charge_threshold, and apply() must skip it rather than reporting a failed
write on every boot -- noise that would train people to ignore the change log.
"""

import os
import tempfile
import unittest
from unittest import mock

from hydroc import hardware
from hydroc.hardware import Hardware


class _EC:
    def has_double_pl4(self):
        return True

    def quantize_power_limit(self, which, watts):
        return int(watts)


class _Stub:
    """Enough of Hardware for apply() to run without touching hardware."""

    ec = _EC()

    def __init__(self, actual):
        self._actual = actual

    def read_state(self):
        return dict(self._actual)

    def driver_bound(self):
        return True

    def normalize(self, desired):
        return dict(desired)


class ThresholdAbsentTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(hardware, "BAT", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)

    def _apply(self, desired, actual):
        return Hardware.apply(_Stub(actual), desired, dry_run=True)

    def test_absent_attribute_produces_no_change_at_all(self):
        changes = self._apply({"charge_threshold": 80},
                              {"charge_threshold": None})
        self.assertEqual(
            [c for c in changes if c.setting == "charge_threshold"], [],
            "reported a change for a feature the driver does not expose")

    def test_absent_attribute_produces_no_failure(self):
        changes = self._apply({"charge_threshold": 80},
                              {"charge_threshold": None})
        self.assertTrue(all(c.ok for c in changes),
                        "absent hardware was reported as a failed write")

    def test_present_attribute_still_applies(self):
        # If a future firmware or chassis does enforce it, nothing here blocks
        # the write -- the guard keys on the attribute existing, not on a model.
        path = os.path.join(self.tmp.name, "charge_control_end_threshold")
        with open(path, "w") as fh:
            fh.write("100")
        changes = self._apply({"charge_threshold": 80},
                              {"charge_threshold": 100})
        self.assertEqual(
            [c.setting for c in changes if c.setting == "charge_threshold"],
            ["charge_threshold"])

    def test_matching_value_is_not_rewritten(self):
        path = os.path.join(self.tmp.name, "charge_control_end_threshold")
        with open(path, "w") as fh:
            fh.write("80")
        changes = self._apply({"charge_threshold": 80},
                              {"charge_threshold": 80})
        self.assertEqual(
            [c for c in changes if c.setting == "charge_threshold"], [])


class DescriptorTest(unittest.TestCase):
    """The driver must not claim a feature measured not to work."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "uniwill-laptop",
                               "uniwill-acpi.c")) as fh:
            src = fh.read()
        i = src.index("hydroc16g1_descriptor")
        self.desc = src[i:src.index("};", i)]

    def test_charge_limit_is_not_claimed(self):
        self.assertNotIn("UNIWILL_FEATURE_BATTERY_CHARGE_LIMIT", self.desc,
                         "descriptor claims a charge limit the EC never enforces")

    def test_charge_modes_is_still_claimed(self):
        # Separate feature, separately inconclusive -- not removed on this
        # evidence.
        self.assertIn("UNIWILL_FEATURE_BATTERY_CHARGE_MODES", self.desc)

    def test_the_reason_is_recorded_next_to_the_decision(self):
        self.assertIn("CHARGE_CTRL_REACHED", self.desc)


if __name__ == "__main__":
    unittest.main()
