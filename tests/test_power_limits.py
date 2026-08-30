# SPDX-License-Identifier: MIT
"""The half-scale PL4 quantiser.

This one has already cost a real bug. PL4 is stored at half scale when 0x0727
bit 7 is set, so the register cannot express odd watts: asking for 125 stores 62
and reads back 124. Intent the hardware cannot hold is permanent phantom drift --
read_state reports the register, drift compares it against the request, and no
amount of re-applying makes them agree. normalize() must therefore snap intent
BEFORE writing, comparing and persisting.

Nothing here touches hardware: the quantiser's only dependency is
has_double_pl4(), so the real method is called against a stand-in.
"""

import unittest

from hydroc.ec import EC
from hydroc.hardware import Hardware


class _EC:
    """Duck-typed stand-in; the real EC would open /proc/acpi/call."""

    def __init__(self, double_pl4=True):
        self._double = double_pl4

    def has_double_pl4(self):
        return self._double

    def quantize_power_limit(self, which, watts):
        return EC.quantize_power_limit(self, which, watts)


def quantize(watts, which="pl4", double=True):
    return EC.quantize_power_limit(_EC(double), which, watts)


class QuantiserTest(unittest.TestCase):

    def test_odd_pl4_rounds_down_to_even(self):
        # 125 would store as 62 and read back 124 -- drift that never clears.
        self.assertEqual(quantize(125), 124)
        self.assertEqual(quantize(1), 0)
        self.assertEqual(quantize(249), 248)

    def test_even_pl4_is_unchanged(self):
        for w in (0, 2, 90, 124, 150, 250):
            self.assertEqual(quantize(w), w, f"{w} W should be expressible")

    def test_quantised_value_is_a_fixed_point(self):
        # Persisting the quantised value must not drift further on re-apply.
        for w in range(0, 256):
            once = quantize(w)
            self.assertEqual(quantize(once), once, f"{w} W not stable")

    def test_pl1_and_pl2_are_not_quantised(self):
        # Only PL4 is stored at half scale.
        for which in ("pl1", "pl2"):
            self.assertEqual(quantize(125, which=which), 125)

    def test_no_quantisation_without_the_double_pl4_bit(self):
        self.assertEqual(quantize(125, double=False), 125)

    def test_zero_is_left_alone(self):
        # 0 means "firmware default", not 0 watts, and must survive verbatim.
        self.assertEqual(quantize(0), 0)
        self.assertEqual(quantize(0, double=False), 0)


class NormalizeTest(unittest.TestCase):
    """normalize() is what stands between intent and phantom drift."""

    def _norm(self, desired, double=True):
        stub = type("H", (), {"ec": _EC(double)})()
        return Hardware.normalize(stub, desired)

    def test_snaps_pl4_before_it_is_stored(self):
        self.assertEqual(self._norm({"cpu_pl4": 125})["cpu_pl4"], 124)

    def test_leaves_other_keys_untouched(self):
        out = self._norm({"cpu_pl1": 35, "cpu_pl2": 45, "cpu_pl4": 91,
                          "charge_threshold": 80})
        self.assertEqual(out["cpu_pl1"], 35)
        self.assertEqual(out["cpu_pl2"], 45)
        self.assertEqual(out["charge_threshold"], 80)
        self.assertEqual(out["cpu_pl4"], 90)

    def test_does_not_mutate_the_caller_dict(self):
        original = {"cpu_pl4": 125}
        self._norm(original)
        self.assertEqual(original["cpu_pl4"], 125, "normalize mutated its input")

    def test_absent_and_none_pl4_are_preserved(self):
        self.assertNotIn("cpu_pl4", self._norm({"cpu_pl1": 35}))
        self.assertIsNone(self._norm({"cpu_pl4": None})["cpu_pl4"])


if __name__ == "__main__":
    unittest.main()
