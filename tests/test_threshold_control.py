# SPDX-License-Identifier: MIT
"""The charge threshold control, and the three things it must say.

This file previously asserted the opposite: that there was no control,
because the threshold was believed inert. That belief came from only ever
testing the default of 80, where it genuinely does nothing -- the voltage
ceiling terminates the charge near a reported 77% and the threshold is never
reached.

Set to 60, charging stopped at 3.93 V/cell. Raised back to 80, it restarted
within 30 s. The control works, and it was removed on a conclusion drawn
from a single untested setting.

Putting a plain slider back would mislead differently, so the card has to
carry three facts, and these tests hold it to them:

  1. above ~75 it does nothing, and that is the design rather than a fault
  2. the number set will not match where charging stops
  3. the reported percentage keeps climbing after the current is zero
"""

import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ui():
    with open(os.path.join(_ROOT, "hydroc", "ui", "index.html"),
              encoding="utf-8") as fh:
        return fh.read()


def card():
    s = ui()
    i = s.index("Charge threshold")
    return s[i:i + 2600]


class ControlRestoredTest(unittest.TestCase):

    def test_the_slider_is_back(self):
        self.assertIn('id="thr"', ui())

    def test_it_posts_the_setting(self):
        self.assertIn("charge_threshold:+thr.value", ui())

    def test_the_handler_is_wired(self):
        self.assertIn("$('#thr')", ui())

    def test_it_is_hidden_when_sysfs_does_not_expose_one(self):
        """Some machines have no threshold file at all; a slider bound to
        null would write garbage."""
        self.assertIn("S.state.charge_threshold==null", card())


class HonestLabellingTest(unittest.TestCase):
    """A bare percentage slider would now be a different lie."""

    def test_it_says_high_settings_do_nothing(self):
        c = card()
        self.assertIn("nothing", c)
        self.assertIn("4.175", c, "the voltage ceiling is why")

    def test_it_warns_the_number_will_not_match(self):
        self.assertIn("not</em> match", card())

    def test_it_explains_the_climb_after_termination(self):
        """A user who sets 60, sees charging stop, and then watches the
        percentage keep rising will otherwise conclude it failed."""
        c = card()
        self.assertIn("current reaches zero", c)

    def test_it_records_the_measurement_rather_than_asserting(self):
        c = card()
        self.assertIn("3.93", c)
        self.assertIn("60", c)


class WritePathTest(unittest.TestCase):

    def test_apply_still_writes_it(self):
        import inspect
        from hydroc import hardware
        self.assertIn("charge_threshold",
                      inspect.getsource(hardware.Hardware.apply))

    def test_read_state_still_reports_it(self):
        import inspect
        from hydroc import hardware
        self.assertIn("charge_threshold",
                      inspect.getsource(hardware.Hardware.read_state))


if __name__ == "__main__":
    unittest.main()
