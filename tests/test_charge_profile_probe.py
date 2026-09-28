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
import itertools
import types
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
                    self.m.REG_AP_OEM_6: oem6,
                    # The footprint witnesses. Values are this machine's real
                    # readings: 0x0742 with bit 2 clear, 0x0490 with both
                    # guard bits set.
                    self.m.REG_AP_OEM: 0x00,
                    self.m.REG_SUPPORT_5: 0x22,
                    self.m.REG_BATT_STATUS: 0x0F,
                    self.m.REG_CHARGE_LIMIT_MODE: 0x45}[addr]
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

    def test_records_the_arming_once_not_repeatedly(self):
        """Once confirmed, it is one event however long the bit stays set --
        the question is whether it EVER arms, not how many samples saw it.
        Timestamped at the confirming sample, not the first suspicious one."""
        for i in range(self.m.OEM6_CONFIRM + 4):
            self.note("0x14", erm=1, t=f"12:00:{i:02d}")
        self.assertEqual(len(self.log.erm_events), 1)
        self.assertEqual(self.log.erm_events[0]["t"],
                         f"12:00:{self.m.OEM6_CONFIRM - 1:02d}")

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


class Oem6ConfirmTest(unittest.TestCase):
    """0x07C6 glitches, and four glitches in one run had bit 4 set.

    Observed 2026-09-27: 864 samples read 0x04 and five read something else,
    each exactly once -- 0x37, 0xBE, 0x56, 0x35, 0x2B. One of them corrupted
    the profile field in the same read cycle. Believing the first sample with
    bit 4 set reported ERM as armed on a machine where it never armed.
    """

    def setUp(self):
        self.m = load()
        self.log = self.m.Log(None)

    def note(self, oem6, erm=0, full=0, t="12:00:00"):
        self.log.write({"t": t, "oem6": oem6, "erm_reached": erm,
                        "full_24h": full, "capacity": 50}, "stationary")

    def test_one_glitched_sample_does_not_arm_erm(self):
        self.note("0x04"); self.note("0x37", erm=1); self.note("0x04")
        self.assertEqual(self.log.erm_events, [])

    def test_four_scattered_glitches_do_not_arm_erm(self):
        """The exact shape of the real run: isolated, never consecutive."""
        for bad in ("0x37", "0xBE", "0x56", "0x35"):
            self.note("0x04"); self.note(bad, erm=1)
        self.note("0x04")
        self.assertEqual(self.log.erm_events, [])

    def test_a_sustained_bit_is_believed(self):
        for _ in range(self.m.OEM6_CONFIRM):
            self.note("0x14", erm=1)
        self.assertEqual(len(self.log.erm_events), 1)
        self.assertEqual(self.log.erm_events[0]["confirmed_over"],
                         self.m.OEM6_CONFIRM)

    def test_a_failed_read_breaks_the_run(self):
        """An unreadable sample is not evidence the bit stayed set."""
        self.note("0x14", erm=1); self.note("0x14", erm=1)
        self.log.write({"t": "t", "oem6": None}, "stationary")
        self.note("0x14", erm=1)
        self.assertEqual(self.log.erm_events, [])

    def test_every_value_is_still_counted(self):
        """Counting all of them is what exposed the glitches in the first
        place -- confirmation must not hide the distribution."""
        self.note("0x04"); self.note("0x37", erm=1); self.note("0x04")
        self.assertEqual(self.log.oem6_seen, {"0x04": 2, "0x37": 1})

    def test_full_24h_needs_the_same_confirmation(self):
        self.note("0x0C", full=1); self.note("0x04")
        self.assertEqual(self.log.full24_events, [])
        for _ in range(self.m.OEM6_CONFIRM):
            self.note("0x0C", full=1)
        self.assertEqual(len(self.log.full24_events), 1)


class WmiDoorTest(unittest.TestCase):
    """The other way into the EC.

    Both doors set the byte -- wmi_ec_probe.py confirmed agreement on seven
    registers. Whether both make the EC *act* is the open question, and the
    reason this path exists at all.
    """

    def setUp(self):
        self.m = load()
        self.m.SET_VIA = "ecrw"

    def test_default_door_is_unchanged(self):
        """Nothing changes unless --set-via asks for it."""
        self.assertEqual(self.m.SET_VIA, "ecrw")

    def test_read_encoding_matches_the_probe(self):
        calls=[]
        with mock.patch.object(self.m, "_call",
                               side_effect=lambda e: calls.append(e) or "{0x20,0,0,0}"):
            self.m._wmi(0x07A6, None)
        arg = calls[0].split("b")[-1]
        b = bytes.fromhex(arg)
        self.assertEqual(len(b), 8)
        self.assertEqual((b[0], b[1]), (0xA6, 0x07))
        self.assertEqual(b[5], 1, "function byte must be READ")

    def test_write_encoding_puts_data_in_byte_2(self):
        calls=[]
        with mock.patch.object(self.m, "_call",
                               side_effect=lambda e: calls.append(e) or "{0,0,0,0}"):
            self.m._wmi(0x07A6, 0x20)
        b = bytes.fromhex(calls[0].split("b")[-1])
        self.assertEqual((b[0], b[1]), (0xA6, 0x07))
        self.assertEqual(b[2], 0x20, "data_low goes in byte 2")
        self.assertEqual(b[3], 0x00, "data_high is always 0 here")
        self.assertEqual(b[5], 0, "function byte must be WRITE")

    def test_the_error_marker_is_not_data(self):
        with mock.patch.object(self.m, "_call", return_value="{0xFE,0xFE,0xFE,0xFE}"):
            v, err = self.m._wmi(0x07A6, None)
        self.assertIsNone(v)
        self.assertIn("FEFEFEFE", err)

    def test_wmi_write_preserves_the_shared_bits(self):
        """0x07A6 also carries touchpad-off and overboost."""
        wrote=[]
        def fake(addr, data=None):
            if data is not None:
                wrote.append(data); return 0, None
            return (0x42 if not wrote else wrote[-1]), None
        with mock.patch.object(self.m, "_wmi", side_effect=fake), \
             mock.patch.object(self.m, "ec_read", return_value=0x62), \
             mock.patch.object(self.m.time, "sleep"):
            self.m.wmi_set_profile(0x02)
        self.assertEqual(wrote[0], 0x62)
        self.assertTrue(wrote[0] & 0x40, "touchpad-off bit dropped")
        self.assertTrue(wrote[0] & 0x02, "overboost bit dropped")

    def test_doors_disagreeing_after_a_write_stops_the_run(self):
        """A value visible through one door and not the other is a shadow
        register -- a bigger finding than the one we are chasing."""
        def fake(addr, data=None):
            return (0x20, None)
        with mock.patch.object(self.m, "_wmi", side_effect=fake), \
             mock.patch.object(self.m, "ec_read", return_value=0x00), \
             mock.patch.object(self.m.time, "sleep"):
            self.assertFalse(self.m.wmi_set_profile(0x02))

    def test_a_failed_wmi_read_writes_nothing(self):
        with mock.patch.object(self.m, "_wmi", return_value=(None, "boom")) as w:
            self.assertFalse(self.m.wmi_set_profile(0x02))
        self.assertEqual(w.call_count, 1, "must not write after a failed read")

    def test_set_profile_routes_to_the_selected_door(self):
        self.m.SET_VIA = "wmi"
        with mock.patch.object(self.m, "wmi_set_profile", return_value=True) as w, \
             mock.patch.object(self.m, "ec_write") as e:
            self.assertTrue(self.m.set_profile(0x02))
        w.assert_called_once_with(0x02)
        e.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class FootprintTest(unittest.TestCase):
    """Did the EC's own ceiling code run?

    117.ELUK contains the charge-ceiling routine, byte for byte the same as the
    G2 build where the feature reportedly works, and every guard it tests passes
    on this machine while the bit it exists to set never arms. Two explanations
    survive that and look identical from outside: the code never runs, or it
    runs against a different 0x07B9 than the one we read back.

    0x0742 bit 2 separates them. The block immediately before the capacity
    comparison writes it on every pass -- set on one branch, cleared on the
    other, never left alone -- so a change proves execution.

    The asymmetry is the point and is tested for. "It moved" is a positive
    observation. "It never moved" is an absence over one capture, and this
    project has already once read a single sample as proof of a capability.
    """

    def setUp(self):
        self.m = load()

    def frames(self, *specs):
        """(support5, ran) pairs -> footprint sample dicts, guards passing."""
        return [{"support5": s, "ran": r, "guard0": 1, "guard2": 1,
                 "ap_oem": "0x04", "manual_fan_ctrl": 0} for s, r in specs]

    def test_ap_oem_is_not_presented_as_a_charging_verdict(self):
        """0x0741 bit 0 is ENABLE_MANUAL_CTRL, for fans. This file claimed it
        was the charging-profile enable, twice, and reported 0x04 as though it
        settled the investigation. It settles nothing, and the summary must
        not imply otherwise."""
        f = self.m.footprint([{"support5": "0x22", "ran": 0, "guard0": 1,
                               "guard2": 1, "ap_oem": "0x04",
                               "manual_fan_ctrl": 0}])
        r = f["ap_oem_reading"].lower()
        self.assertIn("manual fan control", r)
        for word in ("disabled", "the answer", "declining"):
            self.assertNotIn(word, r)

    def test_an_unread_ap_oem_is_not_reported_as_clear(self):
        f = self.m.footprint([{"support5": "0x22", "ran": 0, "guard0": 1,
                               "guard2": 1, "ap_oem": None,
                               "manual_fan_ctrl": None}])
        self.assertEqual(f["ap_oem_reading"], "not read")

    def test_a_flat_register_is_not_reported_as_execution(self):
        f = self.m.footprint(self.frames(*[("0x22", 0)] * 200))
        self.assertFalse(f["bit2_moved"])

    def test_a_single_change_anywhere_counts(self):
        """One toggle in a thousand samples still proves the code ran."""
        f = self.m.footprint(self.frames(*([("0x22", 0)] * 500
                                           + [("0x26", 1)]
                                           + [("0x22", 0)] * 499)))
        self.assertTrue(f["bit2_moved"])

    def test_absence_is_worded_as_a_reason_to_repeat(self):
        f = self.m.footprint(self.frames(*[("0x22", 0)] * 50))
        self.assertIn("not proof", f["reading"])

    def test_presence_is_worded_as_a_finding(self):
        f = self.m.footprint(self.frames(("0x22", 0), ("0x26", 1)))
        self.assertIn("ran", f["reading"])
        self.assertNotIn("not proof", f["reading"])

    def test_guards_are_tracked_separately_from_the_footprint(self):
        """A guard failing would be a different diagnosis entirely -- the code
        running and declining -- so it must not be conflated with idleness."""
        s = [{"support5": "0x22", "ran": 0, "guard0": 1, "guard2": 0}]
        f = self.m.footprint(s)
        self.assertFalse(f["guards_always_passed"])
        self.assertFalse(f["bit2_moved"])

    def test_guards_passing_throughout_is_reported(self):
        f = self.m.footprint(self.frames(*[("0x22", 0)] * 10))
        self.assertTrue(f["guards_always_passed"])

    def test_unreadable_registers_do_not_fake_a_flat_line(self):
        """A run where every EC read failed must not read as 'never changed'."""
        f = self.m.footprint([{"support5": None, "ran": None,
                               "guard0": None, "guard2": None}] * 20)
        self.assertEqual(f["samples"], 0)
        self.assertFalse(f["guards_always_passed"])

    def test_empty_capture_does_not_raise(self):
        self.assertEqual(self.m.footprint([])["samples"], 0)


class FootprintColumnsTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_new_columns_are_logged(self):
        for c in ("support5", "ran", "batt_status", "guard0", "guard2",
                  "charge_limit_mode"):
            self.assertIn(c, self.m.Log.COLS)

    def test_the_registers_are_the_ones_decoded_from_the_image(self):
        self.assertEqual(self.m.REG_SUPPORT_5, 0x0742)
        self.assertEqual(self.m.REG_BATT_STATUS, 0x0490)
        self.assertEqual(self.m.REG_CHARGE_LIMIT_MODE, 0x0497)

    def test_the_new_registers_are_never_written(self):
        """These are witnesses. Writing one would destroy what it measures."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        for reg in ("REG_SUPPORT_5", "REG_BATT_STATUS", "REG_CHARGE_LIMIT_MODE"):
            self.assertNotIn(f"ec_write({reg}", src)
            self.assertNotIn(f"_wmi({reg}", src)


class TriggerModeTest(unittest.TestCase):
    """One write, above the threshold, at a capacity where it shows.

    The theory: the EC evaluates the ceiling when the profile register is
    written rather than polling on its own. Every negative result in this
    project wrote once, early, while capacity was BELOW the threshold, so the
    single evaluation found nothing to do and never ran again.

    The safety property is the important one. Holding these registers by
    continuous re-writing produced bang-bang charging and then a latched state
    reporting 100% with the true charge unknown, surviving reboots
    (DESIGN.md 4.1b). This mode must write exactly once, and must not suggest
    escalating to a loop when it finds nothing.
    """

    def setUp(self):
        self.m = load()

    def args(self, **kw):
        d = dict(interval=0.0, timeout=5.0, trigger_watch=0.0)
        d.update(kw)
        return types.SimpleNamespace(**d)

    def run_trigger(self, samples, at_pct=65, watch=0.0):
        """Drive trigger_run over a scripted sequence of samples.

        The script repeats its last frame rather than running dry. A mock that
        raises StopIteration mid-loop fails the test for a reason that has
        nothing to do with the probe, which is a trap this suite has fallen
        into before with an expiring monotonic.
        """
        script = list(samples)
        log = mock.Mock()
        log.footprint_samples = []
        wrote = []
        clock = itertools.count(0.0, 1.0)

        def next_sample(_cells):
            return script.pop(0) if len(script) > 1 else script[0]

        with mock.patch.object(self.m, "sample", side_effect=next_sample), \
             mock.patch.object(self.m, "set_profile",
                               side_effect=lambda v: wrote.append(v)), \
             mock.patch.object(self.m, "time") as t:
            t.monotonic.side_effect = lambda: next(clock)
            t.sleep.return_value = None
            res = self.m.trigger_run(4, "stationary", at_pct,
                                     self.args(trigger_watch=watch), log)
        return res, wrote

    def frame(self, cap, ma, thr=60, reached=0, status="Charging", ran=0):
        return {"t": "2026-09-28T12:00:00", "capacity": cap, "current_ma": ma,
                "threshold": thr, "reached": reached, "status": status,
                "v_per_cell": 4.0, "ran": ran, "support5": "0x22"}

    def test_exactly_one_write_when_it_fires(self):
        res, wrote = self.run_trigger(
            [self.frame(62, 3700), self.frame(65, 3700)] + [self.frame(65, 0)] * 8)
        self.assertTrue(res["fired"])
        self.assertEqual(len(wrote), 1, "the whole point is a single write")

    def test_no_write_at_all_when_it_never_triggers(self):
        res, wrote = self.run_trigger([self.frame(50, 3700)] * 30)
        self.assertFalse(res["fired"])
        self.assertEqual(wrote, [])

    def test_below_the_threshold_does_not_fire(self):
        """Writing at capacity under the threshold is the experiment this
        project already ran three times, and it proves nothing."""
        res, _ = self.run_trigger([self.frame(55, 3700, thr=80)] * 30, at_pct=50)
        self.assertFalse(res["fired"])

    def test_a_current_collapse_reads_as_engagement(self):
        res, _ = self.run_trigger(
            [self.frame(64, 3700), self.frame(65, 3700)]
            + [self.frame(65, 20)] * 6, watch=3.0)
        self.assertIn("ENGAGED", res["verdict"])

    def test_a_taper_is_not_mistaken_for_engagement(self):
        """Current easing from 3700 to 3000 is charging, not a ceiling."""
        res, _ = self.run_trigger(
            [self.frame(64, 3700), self.frame(65, 3700)]
            + [self.frame(65, 3000)] * 6, watch=3.0)
        self.assertNotIn("ENGAGED", res["verdict"])

    def test_reached_arming_counts_even_without_a_current_drop(self):
        res, _ = self.run_trigger(
            [self.frame(64, 3700), self.frame(65, 3700)]
            + [self.frame(65, 3600, reached=1)] * 6, watch=3.0)
        self.assertIn("ENGAGED", res["verdict"])

    def test_a_null_result_warns_against_the_loop(self):
        """The obvious escalation from 'one write did nothing' is 'hold it',
        which is the operation that latched this EC. The verdict must say so."""
        res, _ = self.run_trigger(
            [self.frame(64, 3700), self.frame(65, 3700)]
            + [self.frame(65, 3600)] * 6, watch=3.0)
        v = res["verdict"]
        self.assertIn("NOT escalate", v)
        self.assertIn("sustained write loop", v)

    def test_the_threshold_register_is_never_written(self):
        """0x07B9 is recorded, not written. The threshold is set beforehand
        through the driver's own sysfs interface."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("ec_write(REG_CHARGE_CTRL", src)
        self.assertNotIn("_wmi(REG_CHARGE_CTRL", src)
