# SPDX-License-Identifier: MIT
"""The machine's own performance modes.

A preset is Custom with numbers we chose; a native mode hands control back to
the EC. The distinction is the whole point of the module, and the thing that
makes it work is clearing the custom latch -- with the latch armed the EC
treats the machine as Custom whatever 0x0751 says, which is why every preset
in this project has produced a white LED and ignored the EC's own limits.

Two properties are worth more than the rest.

Ordering. set_power_limit is silently ignored with the latch clear, so the
limits must be zeroed BEFORE the latch comes down. Zeroing afterwards leaves a
previous preset's numbers in 0x0783-0x0785 where they will be read back and
reported as what the machine is doing.

And Office must stay shut. Its encoding sets 0x0751 bit 7, the bit that
stopped the fans on this machine with the tables empty.
"""

import importlib.util
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "nativemode",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "hydroc", "nativemode.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class FakeEC:
    """Records the order of everything, because order is the bug risk."""

    def __init__(self, regs=None, double_pl4=False):
        self.regs = dict(regs or {})
        self.log = []
        self._double = double_pl4
        self.latched = True

    def read(self, addr):
        return self.regs.get(addr, 0)

    def write_verify(self, addr, value):
        self.regs[addr] = value
        self.log.append(("write", addr, value))

    def set_power_limit(self, which, watts):
        self.log.append(("pl", which, watts))

    def set_custom_profile(self, enable):
        self.latched = enable
        self.log.append(("latch", enable, None))

    def custom_profile_enabled(self):
        return self.latched

    def has_double_pl4(self):
        return self._double


class DecodeTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_three_encodings(self):
        for raw, name in ((0x00, "balanced"), (0x10, "beast"), (0xA0, "office")):
            self.assertEqual(self.m.decode(raw), name)

    def test_boost_does_not_change_the_mode(self):
        """Fan boost ORs 0x40 into any mode; it is not a mode of its own."""
        self.assertEqual(self.m.decode(0x00 | 0x40), "balanced")
        self.assertEqual(self.m.decode(0x10 | 0x40), "beast")

    def test_an_unrecognised_encoding_is_none_not_a_guess(self):
        self.assertIsNone(self.m.decode(0x37))
        self.assertIsNone(self.m.decode(0x20))

    def test_current_reports_the_latch_because_it_overrides_the_mode(self):
        """With the latch armed the EC is in Custom whatever 0x0751 holds, so
        a mode name without the latch state is misleading."""
        ec = FakeEC({0x0751: 0x10})
        ec.latched = True
        st = self.m.current(ec)
        self.assertEqual(st["mode"], "beast")
        self.assertTrue(st["custom_latched"])


class ApplyOrderTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_limits_are_zeroed_before_the_latch_comes_down(self):
        ec = FakeEC()
        self.m.apply(ec, "balanced")
        kinds = [e[0] for e in ec.log]
        self.assertLess(kinds.index("pl"), kinds.index("latch"),
                        "power limits must be zeroed while the latch still "
                        "accepts writes, or stale preset values persist")

    def test_all_three_limits_are_zeroed(self):
        ec = FakeEC()
        self.m.apply(ec, "beast")
        self.assertEqual([(e[1], e[2]) for e in ec.log if e[0] == "pl"],
                         [("pl1", 0), ("pl2", 0), ("pl4", 0)])

    def test_the_latch_is_cleared_not_armed(self):
        ec = FakeEC()
        self.m.apply(ec, "balanced")
        self.assertIn(("latch", False, None), ec.log)
        self.assertFalse(ec.latched)

    def test_the_mode_register_is_written_last(self):
        ec = FakeEC()
        self.m.apply(ec, "beast")
        self.assertEqual(ec.log[-1], ("write", 0x0751, 0x10))

    def test_balanced_writes_zero_not_something_truthy(self):
        """0x00 is a real encoding, and a falsy one. A writer that skips
        zero would silently leave the previous mode selected."""
        ec = FakeEC({0x0751: 0x10})
        self.m.apply(ec, "balanced")
        self.assertEqual(ec.regs[0x0751], 0x00)

    def test_boost_ors_into_the_mode(self):
        ec = FakeEC()
        self.m.apply(ec, "beast", boost=True)
        self.assertEqual(ec.regs[0x0751], 0x10 | 0x40)


class OfficeIsShutTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_office_is_refused_by_default(self):
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError) as cm:
            self.m.apply(ec, "office")
        self.assertIn("bit 7", str(cm.exception))

    def test_refusing_office_touches_no_register(self):
        """A refusal that has already zeroed the limits is not a refusal."""
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError):
            self.m.apply(ec, "office")
        self.assertEqual(ec.log, [])

    def test_office_is_reachable_deliberately(self):
        ec = FakeEC()
        self.m.apply(ec, "office", allow_fan_user_bit=True)
        self.assertEqual(ec.regs[0x0751], 0xA0)

    def test_office_is_not_in_the_button_cycle(self):
        self.assertNotIn("office", self.m.CYCLE)
        self.assertEqual(self.m.CYCLE, ["balanced", "beast"])

    def test_an_unknown_mode_is_refused(self):
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError):
            self.m.apply(ec, "turbo")
        self.assertEqual(ec.log, [])


class EcLimitsTest(unittest.TestCase):
    """Read-only. These are the firmware's numbers, not ours to write."""

    def setUp(self):
        self.m = load()

    def test_it_reads_the_registers_measured_on_the_hardware(self):
        ec = FakeEC({0x07A7: 205, 0x07A8: 205, 0x07A9: 200, 0x07DA: 5})
        self.assertEqual(self.m.ec_limits(ec, "beast"),
                         {"pl1": 205, "pl2": 205, "pl4": 200, "tcc": 5})

    def test_pl4_is_doubled_where_the_register_is_half_scale(self):
        ec = FakeEC({0x0730: 75, 0x0731: 75, 0x0732: 125, 0x07D8: 5},
                    double_pl4=True)
        self.assertEqual(self.m.ec_limits(ec, "balanced")["pl4"], 250)

    def test_it_never_writes(self):
        ec = FakeEC({0x0730: 75})
        self.m.ec_limits(ec, "balanced")
        self.assertEqual(ec.log, [])

    def test_the_module_writes_no_fan_table(self):
        """Deliberate: the vendor curves need MIN_DUTY relaxed and the
        0x0F00/0x0F10 inversion resolved first."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            body = fh.read().split('"""', 2)[2]
        self.assertNotIn("0x0F", body)


if __name__ == "__main__":
    unittest.main()
