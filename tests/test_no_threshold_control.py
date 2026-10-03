# SPDX-License-Identifier: MIT
"""There is no charge-threshold control, and that is the finding.

The EC stores `0x07B9` and never acts on it. Two operating systems, three
battery packs, and most recently a pack with zero cycles that charged eight
points past an 80% setting while still reporting Charging. A slider that
writes a register nothing reads is worse than no slider: it tells the owner
the machine can do something it cannot, and it makes a real limitation look
like a configuration they got wrong.

What the card says instead is the protection that is real -- a fixed voltage
reduction that needs no setting and cannot be turned off.

The write path in hardware.apply() is deliberately left alone. sysfs
`charge_control_end_threshold` is the kernel driver's interface and other
tools use it; removing the UI control is a statement about this app, not an
attempt to take the register away from the system.
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ui():
    with open(os.path.join(_ROOT, "hydroc", "ui", "index.html"),
              encoding="utf-8") as fh:
        return fh.read()


class NoControlTest(unittest.TestCase):

    def setUp(self):
        self.src = ui()

    def test_there_is_no_slider(self):
        self.assertNotIn('id="thr"', self.src)

    def test_nothing_posts_a_threshold(self):
        self.assertNotIn("charge_threshold:", self.src,
                         "the UI is still sending a threshold")

    def test_no_orphaned_handler(self):
        """A lookup for an element that no longer exists is dead code that
        reads like a feature."""
        self.assertNotIn("$('#thr')", self.src)

    def test_the_card_still_exists_and_explains(self):
        """Deleting it outright would leave no account of why a feature the
        vendor advertises is absent."""
        i = self.src.find("Charge threshold")
        self.assertNotEqual(i, -1, "the card is gone entirely")
        block = self.src[i:i + 1400]
        self.assertIn("never acts on it", block)
        self.assertIn("zero", block)

    def test_it_names_the_protection_that_is_real(self):
        i = self.src.find("Charge threshold")
        block = self.src[i:i + 1600]
        self.assertIn("4.175", block)
        self.assertIn("4.450", block)


class WritePathUntouchedTest(unittest.TestCase):
    """Removing the control is a UI decision. The register is the kernel
    driver's, and other tools write it."""

    def test_apply_still_honours_a_saved_threshold(self):
        import inspect
        from hydroc import hardware
        src = inspect.getsource(hardware.Hardware.apply)
        self.assertIn("charge_threshold", src)

    def test_read_state_still_reports_it(self):
        import inspect
        from hydroc import hardware
        self.assertIn("charge_threshold",
                      inspect.getsource(hardware.Hardware.read_state))


if __name__ == "__main__":
    unittest.main()
