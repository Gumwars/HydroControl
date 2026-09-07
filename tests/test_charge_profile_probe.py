# SPDX-License-Identifier: MIT
"""charge_profile_probe: the register arithmetic and the verdict.

Two things here are worth pinning down without hardware.

The write. 0x07A6 is a shared byte -- bits 5:4 are the charging profile, but
bit 1 is OVERBOOST_DYN_TEMP_OFF and bit 6 is TOUCHPAD_TOGGLE_OFF. This probe
runs unattended for hours on a machine nobody is sitting at, so a mask slip
would disable the touchpad and nobody would find out until they got back.

The verdict. The whole point of the run is a claim about termination voltage,
and the one outcome that must never be dressed up as a result is "nothing
resumed but the numbers disagree" -- that is a repeat, not a finding.
"""

import importlib.util
import os
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "charge_profile_probe",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "charge_profile_probe.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class Args:
    """Just the fields verdict() reads."""
    same_mv = 15


def settled(v):
    return {"v_per_cell": v}


class ProfileBitsTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_read_profile_takes_bits_5_4(self):
        with mock.patch.object(self.m, "ec_read", return_value=0x2A):
            self.assertEqual(self.m.read_profile(), 0x02)

    def test_write_preserves_touchpad_and_overboost_bits(self):
        """0x42 = touchpad-off (bit 6) + overboost bit 1, profile 0."""
        wrote = []
        with mock.patch.object(self.m, "ec_read", side_effect=[0x42, 0x62]), \
             mock.patch.object(self.m, "ec_write",
                               side_effect=lambda a, v: wrote.append((a, v))), \
             mock.patch.object(self.m.time, "sleep"):
            ok = self.m.set_profile(0x02)
        self.assertTrue(ok)
        addr, val = wrote[0]
        self.assertEqual(addr, 0x07A6)
        self.assertEqual(val, 0x62)                    # profile in, bits 6 and 1 kept
        self.assertTrue(val & 0x40, "touchpad-off bit was dropped")
        self.assertTrue(val & 0x02, "overboost bit was dropped")

    def test_write_clears_the_old_profile(self):
        """Stationary -> High Capacity must clear bits 5:4, not OR into them."""
        wrote = []
        with mock.patch.object(self.m, "ec_read", side_effect=[0x20, 0x00]), \
             mock.patch.object(self.m, "ec_write",
                               side_effect=lambda a, v: wrote.append((a, v))), \
             mock.patch.object(self.m.time, "sleep"):
            self.m.set_profile(0x00)
        self.assertEqual(wrote[0][1], 0x00)

    def test_never_writes_blind(self):
        with mock.patch.object(self.m, "ec_read", return_value=None), \
             mock.patch.object(self.m, "ec_write") as w:
            self.assertFalse(self.m.set_profile(0x02))
        w.assert_not_called()

    def test_readback_mismatch_is_a_failure(self):
        with mock.patch.object(self.m, "ec_read", side_effect=[0x00, 0x00]), \
             mock.patch.object(self.m, "ec_write"), \
             mock.patch.object(self.m.time, "sleep"):
            self.assertFalse(self.m.set_profile(0x02))


class CellCountTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_derives_4s_from_design_voltage(self):
        with mock.patch.object(self.m, "sysfs_int", return_value=15480000):
            self.assertEqual(self.m.cell_count(), 4)

    def test_falls_back_when_unreadable(self):
        with mock.patch.object(self.m, "sysfs_int", return_value=None):
            self.assertEqual(self.m.cell_count(), 4)


class VerdictTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_resume_means_the_profiles_do_something(self):
        r = [{"profile": "stationary", "settled": settled(4.10)},
             {"profile": "balanced", "resumed": True, "settled": settled(4.23)}]
        v = self.m.verdict(r, Args())
        self.assertEqual(v["conclusion"], "profiles affect termination voltage")
        self.assertEqual(v["resumed"], ["balanced"])

    def test_no_resume_and_equal_voltages_means_inert(self):
        r = [{"profile": "stationary", "settled": settled(4.2300)},
             {"profile": "balanced", "resumed": False, "settled": settled(4.2310)},
             {"profile": "high_capacity", "resumed": False, "settled": settled(4.2295)}]
        v = self.m.verdict(r, Args())
        self.assertEqual(v["conclusion"], "profiles are inert")
        self.assertLessEqual(v["spread_mv_per_cell"], 15)

    def test_no_resume_but_voltages_disagree_is_not_a_finding(self):
        """The dangerous case: it must ask for a repeat, not pick a side."""
        r = [{"profile": "stationary", "settled": settled(4.10)},
             {"profile": "balanced", "resumed": False, "settled": settled(4.23)}]
        v = self.m.verdict(r, Args())
        self.assertEqual(v["conclusion"], "unclear")
        self.assertIn("Repeat", v["detail"])

    def test_one_reading_is_inconclusive(self):
        r = [{"profile": "stationary", "settled": settled(4.23)},
             {"profile": "balanced", "set": False}]
        self.assertEqual(self.m.verdict(r, Args())["conclusion"], "inconclusive")

    def test_a_failed_settle_does_not_count_as_a_reading(self):
        r = [{"profile": "stationary", "settled": settled(4.23)},
             {"profile": "balanced", "resumed": False, "settled": None}]
        self.assertEqual(self.m.verdict(r, Args())["conclusion"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
