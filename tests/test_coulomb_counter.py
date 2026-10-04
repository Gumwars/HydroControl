# SPDX-License-Identifier: MIT
"""Showing the owner what the battery is actually doing.

Everything sysfs reports about charge comes from one number. charge_now is
capacity x (charge_full / 100); charge_full is a figure the pack never
reaches; and after a charge terminates the percentage keeps climbing with no
current flowing -- 640 mAh of it in ninety seconds, measured twice in one
evening.

current_now is the only independent measurement on the machine. Integrating
it live, and showing it beside what the OS claims, is the difference between
a finding buried in a CSV and an instrument the owner can read.

The integrator must not invent anything, which is most of what these tests
are about: no ratio from one sample, no charge counted across a suspend, and
a clean reset when the current changes direction.
"""

import unittest

from hydroc.hardware import Hardware


def hw():
    h = Hardware.__new__(Hardware)
    h._coulomb = None
    return h


def step(h, ma, status, reported, advance=None):
    """One telemetry sample, optionally `advance` seconds after the last."""
    if advance is not None and h._coulomb:
        h._coulomb["t"] -= advance
    return h._coulomb_step({"current_ma": ma, "status": status,
                            "charge_now_mah": reported})


def run_leg(h, ma, status, start_reported, minutes, reported_per_min):
    """Charge or discharge in 60 s steps.

    One big jump does not work and should not: anything over
    COULOMB_MAX_GAP_S is treated as a stall and deliberately not counted.
    """
    step(h, ma, status, start_reported)
    r = None
    for i in range(minutes):
        r = step(h, ma, status,
                 start_reported + (i + 1) * reported_per_min, advance=60)
    return r


class IntegrationTest(unittest.TestCase):

    def test_an_hour_at_one_amp_is_one_amp_hour(self):
        h = hw()
        step(h, 1000, "Charging", 1000)
        for i in range(60):
            r = step(h, 1000, "Charging", 1000 + (i + 1) * 16, advance=60)
        self.assertAlmostEqual(r["coulomb_measured_mah"], 1000.0, delta=1.0)

    def test_it_reports_no_ratio_from_a_single_sample(self):
        """A ratio computed off one reading is noise wearing a number's
        clothes."""
        r = step(hw(), 1000, "Charging", 1000)
        self.assertIsNone(r["coulomb_ratio"])

    def test_a_direction_change_starts_a_new_leg(self):
        h = hw()
        run_leg(h, 1000, "Charging", 1000, 10, 16)
        r = step(h, 1500, "Discharging", 2000)
        self.assertEqual(r["coulomb_leg"], "discharge")
        self.assertEqual(r["coulomb_measured_mah"], 0.0)
        self.assertIsNone(r["coulomb_ratio"])

    def test_a_long_gap_is_not_counted(self):
        """Suspend, or a stalled poll. Assuming the last current flowed the
        whole time would invent charge that never moved."""
        h = hw()
        step(h, 3000, "Charging", 1000)
        r = step(h, 3000, "Charging", 1100, advance=4000)
        self.assertEqual(r["coulomb_measured_mah"], 0.0)

    def test_a_gap_inside_the_limit_is_counted(self):
        h = hw()
        step(h, 3600, "Charging", 1000)
        r = step(h, 3600, "Charging", 1100, advance=100)
        self.assertAlmostEqual(r["coulomb_measured_mah"], 100.0, delta=1.0)


class SeamTest(unittest.TestCase):
    """The specific thing this exists to show."""

    def test_zero_current_with_a_climbing_number_is_flagged(self):
        h = hw()
        run_leg(h, 1000, "Charging", 1000, 10, 16)
        r = step(h, 0, "Charging", 2640, advance=30)
        self.assertTrue(r["fabricating"])

    def test_an_honest_charge_is_not_flagged(self):
        h = hw()
        r = run_leg(h, 1000, "Charging", 1000, 10, 16)
        self.assertFalse(r["fabricating"])

    def test_idle_is_not_flagged(self):
        """Not charging and not moving is the normal resting state, not a
        lie."""
        h = hw()
        step(h, 0, "Not charging", 6400)
        r = step(h, 0, "Not charging", 6400, advance=60)
        self.assertFalse(r["fabricating"])

    def test_the_ratio_falls_when_the_number_runs_ahead(self):
        h = hw()
        honest = run_leg(h, 1000, "Charging", 1000, 60, 16)   # 1000 mAh, claims 960
        after = step(h, 0, "Charging", 2600, advance=30)      # +640 with nothing flowing
        self.assertGreater(honest["coulomb_ratio"], 0.95)
        self.assertLess(after["coulomb_ratio"], honest["coulomb_ratio"])
        self.assertTrue(after["fabricating"])


class UIWiringTest(unittest.TestCase):

    @staticmethod
    def ui():
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "hydroc", "ui", "index.html"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_card_shows_both_numbers(self):
        u = self.ui()
        self.assertIn('data-t="coulomb_measured_mah"', u)
        self.assertIn('data-t="coulomb_claimed_mah"', u)

    def test_the_ratio_is_coloured_against_the_measured_threshold(self):
        u = self.ui()
        self.assertIn("0.85", u)
        self.assertIn("coulomb_ratio", u)

    def test_the_banner_exists_and_is_hidden_by_default(self):
        u = self.ui()
        i = u.index('id="fabricating"')
        self.assertIn("display:none", u[i:i + 120])

    def test_the_banner_says_what_is_happening_in_plain_words(self):
        u = self.ui()
        self.assertIn("The battery is not charging.", u)

    def test_the_charging_mode_blurb_no_longer_claims_nothing_limits(self):
        """It said no mode was observed to limit charging. The threshold
        does, at settings below the voltage ceiling."""
        self.assertNotIn("no mode was observed to limit", self.ui())


if __name__ == "__main__":
    unittest.main()
