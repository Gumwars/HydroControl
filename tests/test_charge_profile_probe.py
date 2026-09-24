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



class Oem6SamplingTest(unittest.TestCase):
    """0x07C6 is sampled read-only, and may be the mechanism that matters."""

    def setUp(self):
        self.m = load()

    def read(self, oem6):
        """ec_read keyed by address, so the call order cannot silently change
        what the test is asserting."""
        def _r(addr):
            return {self.m.REG_CHARGE_CTRL: 0x50,
                    self.m.REG_OEM_4: 0x20,
                    self.m.REG_AP_OEM_6: oem6}[addr]
        return mock.patch.object(self.m, "ec_read", side_effect=_r)

    def sample_with(self, oem6):
        with self.read(oem6), \
             mock.patch.object(self.m, "sysfs_int", return_value=1000), \
             mock.patch.object(self.m, "sysfs_str", return_value="Full"), \
             mock.patch.object(self.m, "on_ac", return_value=True):
            return self.m.sample(4)

    def test_decodes_erm_and_24h_bits(self):
        s = self.sample_with(0x14)                 # bit 4 ERM + bit 2
        self.assertEqual(s["oem6"], "0x14")
        self.assertEqual(s["erm_reached"], 1)
        self.assertEqual(s["full_24h"], 0)

    def test_decodes_full_24h(self):
        s = self.sample_with(0x08)
        self.assertEqual(s["full_24h"], 1)
        self.assertEqual(s["erm_reached"], 0)

    def test_clear_byte_is_zero_not_none(self):
        """Unreadable and 'read as clear' must never look the same."""
        s = self.sample_with(0x00)
        self.assertEqual(s["erm_reached"], 0)
        self.assertIsNotNone(s["oem6"])

    def test_unreadable_stays_none(self):
        def _r(addr):
            return None if addr == self.m.REG_AP_OEM_6 else 0x00
        with mock.patch.object(self.m, "ec_read", side_effect=_r), \
             mock.patch.object(self.m, "sysfs_int", return_value=1), \
             mock.patch.object(self.m, "sysfs_str", return_value="Full"), \
             mock.patch.object(self.m, "on_ac", return_value=True):
            s = self.m.sample(4)
        self.assertIsNone(s["oem6"])
        self.assertIsNone(s["erm_reached"])


class Oem6TrackingTest(unittest.TestCase):

    def setUp(self):
        self.m = load()
        self.log = self.m.Log(None)               # no file; tracking must still run

    def note(self, oem6, erm=0, full=0, t="12:00:00", cap=100):
        self.log.write({"t": t, "oem6": oem6, "erm_reached": erm,
                        "full_24h": full, "capacity": cap}, "stationary")

    def test_counts_every_value_seen(self):
        self.note("0x04"); self.note("0x04"); self.note("0x14", erm=1)
        self.assertEqual(self.log.oem6_seen, {"0x04": 2, "0x14": 1})

    def test_records_only_the_first_arming(self):
        self.note("0x14", erm=1, t="12:00:01")
        self.note("0x14", erm=1, t="12:00:02")
        self.assertEqual(len(self.log.erm_events), 1)
        self.assertEqual(self.log.erm_events[0]["t"], "12:00:01")

    def test_never_arming_leaves_no_event(self):
        self.note("0x04"); self.note("0x04")
        self.assertEqual(self.log.erm_events, [])

    def test_unreadable_samples_are_not_counted(self):
        self.log.write({"t": "12:00:00", "oem6": None}, "stationary")
        self.assertEqual(self.log.oem6_seen, {})

class DriftTest(unittest.TestCase):
    """The daemon re-applies its stored charging profile; the probe must not
    credit one profile's resting voltage to another."""

    def setUp(self):
        self.m = load()

    def test_matching_profile_is_not_drift(self):
        self.assertFalse(self.m.drifted({"profile": 2}, 2))

    def test_different_profile_is_drift(self):
        self.assertTrue(self.m.drifted({"profile": 0}, 2))

    def test_unreadable_profile_is_not_treated_as_drift(self):
        """A failed EC read must not restart the quiet window forever."""
        self.assertFalse(self.m.drifted({"profile": None}, 2))
        self.assertFalse(self.m.drifted({}, 2))

    def test_high_capacity_is_zero_not_falsy(self):
        """high_capacity is 0x00 -- a truthiness check here would miss it."""
        self.assertTrue(self.m.drifted({"profile": 0}, 1))
        self.assertFalse(self.m.drifted({"profile": 0}, 0))


class ArmingWarningTest(unittest.TestCase):
    """The 2026-09-07 run set the profile into a cycle already in progress and
    reported "inert". A same-board user then armed the ceiling by selecting the
    profile while unplugged. A run that cannot arm it must not be readable as
    an answer."""

    def setUp(self):
        self.m = load()

    def test_starting_on_battery_can_arm(self):
        self.assertIsNone(self.m.arming_warning(False))

    def test_starting_on_ac_cannot_arm(self):
        w = self.m.arming_warning(True)
        self.assertIsNotNone(w)
        self.assertIn("already connected", w)

    def test_the_warning_says_what_to_do_instead(self):
        """A caveat nobody can act on just gets skipped."""
        self.assertIn("start the probe on battery", self.m.arming_warning(True))

    def test_sampling_resolves_the_reported_climb(self):
        """~2% every 5-10 s. The old 60 s idle interval applied to exactly the
        stretch where the climb happens, so it could never have resolved it."""
        self.assertLessEqual(self.m.DEFAULT_INTERVAL, 10)
        self.assertLessEqual(self.m.DEFAULT_IDLE_INTERVAL, 10,
                             "the idle interval covers the climb; it must be "
                             "as fast as the near-full interval")


class DriftTrackerTest(unittest.TestCase):
    """One bad EC read must never cause a write.

    2026-09-24: a single sample read profile=0 and oem6=0x5F while every
    neighbour read 2 and 0x04. The guard re-asserted on it, writing 0x07A6 at
    75% capacity -- which, if the EC latches its ceiling at plug-in, is exactly
    what would clear it. The guard corrupted the experiment it was protecting.
    """

    def setUp(self):
        self.m = load()

    def track(self, confirm=3):
        return self.m.DriftTracker(confirm)

    def sample(self, prof):
        return {"t": "12:00:00", "profile": prof, "capacity": 75}

    def test_a_single_bad_read_does_not_trigger_a_write(self):
        d = self.track()
        self.assertFalse(d.saw(self.sample(0), 2))
        self.assertEqual(d.events, [])

    def test_two_of_three_is_still_not_enough(self):
        d = self.track()
        d.saw(self.sample(0), 2)
        self.assertFalse(d.saw(self.sample(0), 2))

    def test_a_sustained_change_is_believed(self):
        """The daemon really does re-apply its stored profile; that persists."""
        d = self.track()
        for _ in range(2):
            d.saw(self.sample(0), 2)
        self.assertTrue(d.saw(self.sample(0), 2))
        self.assertEqual(len(d.events), 1)
        self.assertEqual(d.events[0]["read"], 0)

    def test_one_good_read_resets_the_run(self):
        """Glitches are isolated; a good sample between them means no change."""
        d = self.track()
        d.saw(self.sample(0), 2)
        d.saw(self.sample(0), 2)
        d.saw(self.sample(2), 2)            # good read
        self.assertFalse(d.saw(self.sample(0), 2))

    def test_unreadable_profile_is_not_drift(self):
        """Six reads failed outright in that run; none should provoke a write."""
        d = self.track()
        for _ in range(5):
            self.assertFalse(d.saw({"t": "t", "profile": None}, 2))

    def test_the_counter_resets_after_firing(self):
        d = self.track(confirm=2)
        d.saw(self.sample(0), 2)
        self.assertTrue(d.saw(self.sample(0), 2))
        self.assertFalse(d.saw(self.sample(0), 2))   # needs 2 again
        self.assertTrue(d.saw(self.sample(0), 2))
        self.assertEqual(len(d.events), 2)

    def test_events_record_enough_to_invalidate_a_run(self):
        d = self.track(confirm=1)
        d.saw(self.sample(0), 2)
        e = d.events[0]
        for k in ("t", "read", "expected", "capacity"):
            self.assertIn(k, e)


if __name__ == "__main__":
    unittest.main()
