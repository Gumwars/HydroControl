# SPDX-License-Identifier: MIT
"""The last Linux artifact before the drive comes out.

Every Windows read has to be compared against a Linux value taken under the
same conditions, and the conditions move: 0x0502:0x0503 went 3030 to 3040
inside an evening, and the cycle count 132 to 133 in a day. A baseline from
last week against a Windows read from next week differs by conditions, not by
operating system, and nothing after the fact can separate those.

So the coverage is the test. A register missing from the capture cannot be
diffed later, and by then the drive is out of the machine.
"""

import importlib.util
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "baseline_capture",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "baseline_capture.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class CoverageTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def covered(self, addr):
        return any(s <= addr <= e for s, e, _ in self.m.RANGES)

    def test_every_watchlist_register_is_captured(self):
        for addr, what in (
                (0x0522, "chg_target low"), (0x0523, "chg_target high"),
                (0x030E, "hw_base high (big-endian)"), (0x030F, "hw_base low"),
                (0x0502, "battery temperature"), (0x0503, "temperature high"),
                (0x07C3, "the ceiling gate"),
                (0x07A6, "the profile itself"),
                (0x07B9, "threshold + REACHED"),
                (0x0742, "the ceiling footprint"),
                (0x0491, "cell-count selector"),
                (0x0490, "the derating guard"),
                (0x04A2, "temperature copy"), (0x04A6, "cycle count"),
                (0x04AB, "capacity"),
                (0x0740, "PROJECT_ID")):
            self.assertTrue(self.covered(addr),
                            f"0x{addr:04X} ({what}) is not in any range")

    def test_the_unreadable_ranges_are_captured_too(self):
        """Their emptiness is evidence. A Windows read that answers where
        Linux gets 0xFF would mean the mailbox reaches further than ECRR."""
        for addr in (0x09C9, 0x0A5C, 0x08FF):
            self.assertTrue(self.covered(addr), f"0x{addr:04X} missing")

    def test_the_battery_fields_that_move_are_recorded(self):
        for f in ("cycle_count", "capacity", "voltage_now", "status"):
            self.assertIn(f, self.m.SYSFS)

    def test_it_never_writes_an_ec_register(self):
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("ECRW", "WKBC", "WMBC", "--set", "ec_write"):
            self.assertNotIn(bad, src)


class OutputTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_a_resting_capture_warns_that_the_target_may_be_stale(self):
        """The derating only computes while charging. A baseline taken at rest
        and diffed against a Windows mid-charge read would compare a stale
        value against a live one."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("may be stale", src)
        self.assertIn("mid-charge", src)

    def test_the_endianness_trap_is_labelled_in_the_output(self):
        """0x030E:0x030F is big-endian and 0x0522:0x0523 is little-endian.
        Reading both the same way already cost this project an afternoon; the
        person reading the file on Windows will not remember."""
        joined = " ".join(why for _s, _e, why in self.m.RANGES)
        self.assertIn("BIG-endian", joined)


if __name__ == "__main__":
    unittest.main()
