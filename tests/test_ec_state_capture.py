# SPDX-License-Identifier: MIT
"""The pre-flash baseline has to cover what the daemon writes.

A capture taken to compare EC 1.17 against 1.18 was missing the per-mode
power limits, the charge threshold and the ceiling gate -- the registers this
project learned about most recently and would most want to diff. The list had
not been revisited since native modes and the charge work landed, and a
baseline is only discovered to be incomplete after the firmware it described
is gone.

So the invariant is pinned here: every address the daemon can write must be in
the capture.
"""

import importlib.util
import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(_ROOT, f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def daemon_addresses():
    """Every 0x0xxx literal in the hydroc package -- the addresses the
    running code can reach."""
    found = set()
    pkg = os.path.join(_ROOT, "hydroc")
    for fn in os.listdir(pkg):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(pkg, fn), encoding="utf-8") as fh:
            for m in re.finditer(r"\b0x0[0-9A-Fa-f]{3}\b", fh.read()):
                found.add(int(m.group(0), 16))
    return found


class CoverageTest(unittest.TestCase):

    def setUp(self):
        self.m = _load("ec_state_capture")
        self.singles = {a for a, _l, _b in self.m.SINGLES}
        self.tables = {b for b, _l in self.m.TABLES}

    def covered(self, addr):
        if addr in self.singles:
            return True
        # A table base covers its 16 entries.
        return any(b <= addr < b + 16 for b in self.tables)

    def test_every_daemon_address_is_captured(self):
        missing = sorted(a for a in daemon_addresses() if not self.covered(a))
        self.assertEqual(
            missing, [],
            "not in the baseline: "
            + ", ".join(f"0x{a:04X}" for a in missing))

    def test_the_per_mode_limits_are_there(self):
        """Added with native modes; absent from the first 1.18 baseline."""
        for a in (0x0730, 0x0731, 0x0732, 0x0734, 0x0735, 0x0736,
                  0x07A7, 0x07A8, 0x07A9, 0x07D8, 0x07D9, 0x07DA):
            self.assertIn(a, self.singles, f"0x{a:04X} missing")

    def test_the_charge_chain_is_there(self):
        for a in (0x07B9, 0x07C3, 0x07A6, 0x0742, 0x0490, 0x0522, 0x0523):
            self.assertIn(a, self.singles, f"0x{a:04X} missing")

    def test_every_entry_is_well_formed(self):
        for e in self.m.SINGLES:
            self.assertEqual(len(e), 3, f"malformed: {e}")
            self.assertIsInstance(e[0], int)
            self.assertIsInstance(e[1], str)
            self.assertIsInstance(e[2], dict)

    def test_no_address_is_listed_twice(self):
        addrs = [a for a, _l, _b in self.m.SINGLES]
        dupes = {a for a in addrs if addrs.count(a) > 1}
        self.assertEqual(dupes, set(),
                         "duplicated: " + ", ".join(f"0x{a:04X}" for a in dupes))


class HysteresisLabelTest(unittest.TestCase):
    """0x0F00 is the rise threshold, 0x0F10 the fall.

    These labels were inverted, the same way fancurve.py was. Left wrong, a
    post-flash comparison reads every curve backwards and the error looks like
    a firmware change.
    """

    def setUp(self):
        self.m = _load("ec_state_capture")
        self.labels = dict(self.m.TABLES)

    def test_0x0F00_is_labelled_rise(self):
        self.assertIn("rise", self.labels[0x0F00].lower())
        self.assertIn("rise", self.labels[0x0F30].lower())

    def test_0x0F10_is_labelled_fall(self):
        self.assertIn("fall", self.labels[0x0F10].lower())
        self.assertIn("fall", self.labels[0x0F40].lower())

    def test_0x0751_is_not_described_as_a_fan_bitfield(self):
        """0xA0 read through the old bit labels came out as "HIGH + USER".
        It is Office mode."""
        label = next(l for a, l, _b in self.m.SINGLES if a == 0x0751)
        self.assertIn("Office", label)
        self.assertNotIn("MANUAL_FAN_CTRL", label)


if __name__ == "__main__":
    unittest.main()
