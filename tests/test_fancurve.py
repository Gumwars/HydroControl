# SPDX-License-Identifier: MIT
"""Fan curve safety rules.

The hazard is specific and one register write away: the tables ship empty and
the enable bit ships clear, so setting the bit first hands the fans a curve that
reads zero at every temperature. The rule "populate, then enable" has to live in
code, because the dangerous order is the intuitive one.

No hardware: a recording stand-in stands in for the EC.
"""

import unittest

from hydroc import fancurve as fc


class FakeEC:
    """Records the order of operations, which is the thing under test."""

    def __init__(self, mem=None):
        self.mem = dict(mem or {})
        self.log = []

    def read(self, addr):
        return self.mem.get(addr, 0)

    def write_verify(self, addr, value):
        self.log.append(("w", addr, value))
        self.mem[addr] = value

    write = write_verify

    def loaded_with(self, curve, fan="cpu"):
        # BASE is (up_t, down_t, duty). This helper unpacked it the other way
        # round, which mirrored the bug in fancurve itself -- so a seeded
        # table read back swapped and the round trip still looked right.
        up_b, down_b, duty_b = fc.BASE[fan]
        for i, (u, d, q) in enumerate(curve):
            self.mem[up_b + i] = u
            self.mem[down_b + i] = d
            self.mem[duty_b + i] = q * 2
        return self


GOOD = fc.PRESET_CURVES["balanced"]["cpu"]


class ValidateTest(unittest.TestCase):

    def test_every_preset_curve_is_valid(self):
        for name, pair in fc.PRESET_CURVES.items():
            for fan, curve in pair.items():
                fc.validate(curve, f"{name}.{fan}")

    def test_a_zero_duty_point_at_temperature_is_rejected(self):
        """Index 0 is the off band and may be zero; anywhere else it is a
        fan-stop waiting for the wrong temperature."""
        bad = [list(p) for p in GOOD]
        bad[4][2] = 0
        with self.assertRaises(fc.CurveError) as cm:
            fc.validate(bad)
        self.assertIn("fan-stop", str(cm.exception))

    def test_zero_duty_in_the_off_band_is_allowed(self):
        """Point 0 is 'below the first threshold'. The vendor's own curves
        stop the fans there, and DESIGN.md measured that as correct stock
        behaviour -- 0 rpm until ~55 C, not a stall."""
        ok = [list(p) for p in GOOD]
        ok[0] = [ok[0][0], 0, 0]
        fc.validate(ok)

    def test_duty_below_the_floor_is_rejected(self):
        bad = [list(p) for p in GOOD]
        bad[0][2] = fc.MIN_DUTY - 1
        with self.assertRaises(fc.CurveError):
            fc.validate(bad)

    def test_duty_must_not_decrease_with_temperature(self):
        bad = [list(p) for p in GOOD]
        bad[5][2] = bad[4][2] - 5
        with self.assertRaises(fc.CurveError):
            fc.validate(bad)

    def test_temperatures_must_not_decrease(self):
        bad = [list(p) for p in GOOD]
        bad[5][0] = bad[4][0] - 1
        with self.assertRaises(fc.CurveError):
            fc.validate(bad)

    def test_a_point_without_hysteresis_is_rejected(self):
        bad = [list(p) for p in GOOD]
        bad[3][1] = bad[3][0]
        with self.assertRaises(fc.CurveError):
            fc.validate(bad)

    def test_wrong_length_is_rejected(self):
        with self.assertRaises(fc.CurveError):
            fc.validate(GOOD[:8])

    def test_an_all_zero_table_is_rejected(self):
        # Exactly what a cold-booted machine has in its tables.
        with self.assertRaises(fc.CurveError):
            fc.validate([[0, 0, 0]] * fc.TABLE_LEN)


class EnableRefusalTest(unittest.TestCase):
    """The safeguard: never hand the fans an empty table."""

    def test_enable_refuses_against_empty_tables(self):
        ec = FakeEC()                       # all reads return 0, as on boot
        with self.assertRaises(fc.CurveError) as cm:
            fc.enable(ec)
        self.assertIn("Write a curve first", str(cm.exception))

    def test_enable_writes_nothing_when_it_refuses(self):
        ec = FakeEC()
        with self.assertRaises(fc.CurveError):
            fc.enable(ec)
        self.assertEqual(ec.log, [], "touched the hardware while refusing")

    def test_enable_proceeds_when_both_tables_are_valid(self):
        ec = FakeEC().loaded_with(GOOD, "cpu")
        ec.loaded_with(fc.PRESET_CURVES["balanced"]["gpu"], "gpu")
        fc.enable(ec)
        self.assertTrue(fc.is_enabled(ec))
        self.assertTrue(fc.is_split(ec))

    def test_enable_refuses_if_only_the_cpu_table_is_populated(self):
        # Split control means the GPU table drives fan 2; an empty one stops it.
        ec = FakeEC().loaded_with(GOOD, "cpu")
        with self.assertRaises(fc.CurveError):
            fc.enable(ec, split=True)


class OrderingTest(unittest.TestCase):
    """Populate, then enable -- never the reverse."""

    def test_apply_writes_every_table_before_touching_the_enable_bit(self):
        ec = FakeEC()
        pair = fc.PRESET_CURVES["balanced"]
        fc.apply_curves(ec, pair["cpu"], pair["gpu"])

        enable_idx = [i for i, (_, a, v) in enumerate(ec.log)
                      if a == fc.REG_AP_OEM_6 and v & fc.ENABLE_UNIVERSAL]
        self.assertEqual(len(enable_idx), 1, "enable bit not set exactly once")
        table_idx = [i for i, (_, a, _) in enumerate(ec.log) if 0x0F00 <= a <= 0x0F5F]
        self.assertTrue(table_idx, "no table writes at all")
        self.assertLess(max(table_idx), enable_idx[0],
                        "enable bit was set before the tables were written")

    def test_apply_writes_all_96_table_bytes(self):
        ec = FakeEC()
        pair = fc.PRESET_CURVES["office"]
        fc.apply_curves(ec, pair["cpu"], pair["gpu"])
        touched = {a for _, a, _ in ec.log if 0x0F00 <= a <= 0x0F5F}
        self.assertEqual(len(touched), 96)

    def test_apply_refuses_an_invalid_curve_before_writing_anything(self):
        ec = FakeEC()
        bad = [list(p) for p in GOOD]
        bad[4][2] = 0          # a zero-duty band at temperature, not the off band
        with self.assertRaises(fc.CurveError):
            fc.apply_curves(ec, bad, fc.PRESET_CURVES["balanced"]["gpu"])
        self.assertEqual(ec.log, [])


class DisableTest(unittest.TestCase):

    def test_disable_clears_both_bits(self):
        ec = FakeEC({fc.REG_AP_OEM_6: fc.ENABLE_UNIVERSAL,
                     fc.REG_UNIVERSAL_FAN_CTRL: fc.SPLIT_TABLES})
        fc.disable(ec)
        self.assertFalse(fc.is_enabled(ec))
        self.assertFalse(fc.is_split(ec))

    def test_disable_survives_an_unavailable_ec(self):
        # It runs on daemon shutdown; raising there masks the real failure.
        class Broken(FakeEC):
            def read(self, addr):
                from hydroc.ec import ECUnavailable
                raise ECUnavailable("gone")
        fc.disable(Broken())        # must not raise


class RoundTripTest(unittest.TestCase):

    def test_a_written_curve_reads_back_identically(self):
        ec = FakeEC()
        fc.write_curve(ec, "cpu", GOOD)
        self.assertEqual(fc.read_curve(ec, "cpu"), [list(p) for p in GOOD])

    def test_duty_is_stored_at_double_scale(self):
        ec = FakeEC()
        fc.write_curve(ec, "cpu", GOOD)
        _, _, duty_b = fc.BASE["cpu"]
        self.assertEqual(ec.mem[duty_b], GOOD[0][2] * 2)


if __name__ == "__main__":
    unittest.main()


class RegisterDirectionTest(unittest.TestCase):
    """Which register holds the rise threshold, and which the fall.

    fancurve unpacked BASE as (down_t, up_t, duty) and so wrote the rise
    threshold into the fall register. Every curve this project produced had
    inverted bands, and nothing caught it because validate() checks the
    curve's own semantics -- down_t < up_t -- which stayed true while the two
    landed in the wrong places.

    The hardware settles it. Control Center's tables, read back off this
    machine in each mode:

        office CPU [1]   0x0F00 = 57   0x0F10 = 48
        beast  CPU [3]   0x0F00 = 65   0x0F10 = 61

    0x0F00 always holds the larger value, and 0x0F00[i] == 0x0F10[i+1] across
    the table, so 0x0F10 opens a band and 0x0F00 closes it.
    """

    @staticmethod
    def _mod():
        return fc

    class EC:
        def __init__(self, regs=None):
            self.regs = dict(regs or {})

        def read(self, a):
            return self.regs.get(a, 0)

        def write_verify(self, a, v):
            self.regs[a] = v

        def write(self, a, v):
            # write_curve writes in bulk and verifies in one pass, so the
            # double has to offer the same primitive the real EC does.
            self.regs[a] = v

    def flat(self, up0=40, step=3, duty0=30):
        """A valid 16-point curve: rising temps, rising duty, fall below rise."""
        out = []
        for i in range(16):
            up = min(100, up0 + i * step)
            out.append([up, up - 5, min(100, duty0 + i * 4)])
        return out

    def test_the_rise_threshold_lands_in_0x0F00(self):
        m = self._mod()
        ec = self.EC()
        m.write_curve(ec, "cpu", self.flat())
        self.assertGreater(ec.regs[0x0F00], ec.regs[0x0F10],
                           "0x0F00 must hold the rise threshold, which is the "
                           "larger of the pair -- the vendor writes 57/48")

    def test_the_gpu_table_goes_the_same_way(self):
        m = self._mod()
        ec = self.EC()
        m.write_curve(ec, "gpu", self.flat())
        self.assertGreater(ec.regs[0x0F30], ec.regs[0x0F40])

    def test_a_vendor_table_reads_back_with_rise_above_fall(self):
        """Point 1 of Control Center's Office curve, verbatim."""
        m = self._mod()
        ec = self.EC({0x0F00 + 1: 57, 0x0F10 + 1: 48, 0x0F20 + 1: 60})
        up_t, down_t, duty = m.read_curve(ec, "cpu")[1]
        self.assertEqual((up_t, down_t, duty), (57, 48, 30))

    def test_write_then_read_round_trips(self):
        m = self._mod()
        ec = self.EC()
        curve = self.flat()
        m.write_curve(ec, "cpu", curve)
        self.assertEqual(m.read_curve(ec, "cpu"), curve)

    def test_validate_still_rejects_an_inverted_point(self):
        """The curve's own semantics are unchanged: fall must be below rise."""
        m = self._mod()
        bad = self.flat()
        bad[3] = [50, 60, 40]
        with self.assertRaises(m.CurveError):
            m.validate(bad)
