# SPDX-License-Identifier: MIT
"""Caching the fan-curve reads, and what must stay true when you do.

read_state() reads both 16-point curves whenever manual fan control is on:
2 x 16 x 3 = 96 ECRR calls. Every EC access waits out a 6 ms inter-access
floor behind a class-level lock, so that pair costs ~0.6 s of pure pacing,
and the UI polls several routes every 10 s. The tables cannot change unless
something writes them, so almost all of that was spent re-confirming a
constant.

Caching a hardware reading is the kind of change that is right until it
quietly is not, so the risks are pinned here: a stale table surviving a
write, a caller mutating the cache, and the cache leaking into scripts and
tests that asked for the registers.
"""

import time
import unittest
from unittest import mock

from hydroc import fancurve as fc


class CountingEC:
    """Counts reads. No pacing -- the point is the call count, and sleeping
    0.006 s ninety-six times to prove it would be its own small crime."""

    def __init__(self, mem=None):
        self.mem = dict(mem or {})
        self.reads = 0
        self.writes = 0

    def read(self, addr):
        self.reads += 1
        return self.mem.get(addr, 0)

    def write_verify(self, addr, value):
        self.writes += 1
        self.mem[addr] = value

    write = write_verify

    def loaded_with(self, curve, fan="cpu"):
        up_b, down_b, duty_b = fc.BASE[fan]
        for i, (u, d, q) in enumerate(curve):
            self.mem[up_b + i] = u
            self.mem[down_b + i] = d
            self.mem[duty_b + i] = q * 2
        return self


GOOD = fc.PRESET_CURVES["balanced"]["cpu"]
PER_CURVE = fc.TABLE_LEN * 3          # 48


class DefaultsToTheHardwareTest(unittest.TestCase):
    """Off unless asked for. A probe script that prints a register has to
    have read the register."""

    def setUp(self):
        fc.invalidate_curves()

    def test_read_curve_hits_the_ec_every_time_by_default(self):
        ec = CountingEC().loaded_with(GOOD)
        fc.read_curve(ec, "cpu")
        fc.read_curve(ec, "cpu")
        self.assertEqual(ec.reads, PER_CURVE * 2)

    def test_the_signature_default_is_uncached(self):
        import inspect
        self.assertIs(inspect.signature(fc.read_curve)
                      .parameters["cached"].default, False)


class CachedReadTest(unittest.TestCase):

    def setUp(self):
        fc.invalidate_curves()
        self.ec = CountingEC().loaded_with(GOOD)

    def test_a_second_cached_read_costs_nothing(self):
        first = fc.read_curve(self.ec, "cpu", cached=True)
        self.ec.reads = 0
        second = fc.read_curve(self.ec, "cpu", cached=True)
        self.assertEqual(self.ec.reads, 0)
        self.assertEqual(first, second)

    def test_the_cached_value_matches_the_hardware(self):
        self.assertEqual(fc.read_curve(self.ec, "cpu", cached=True),
                         fc.read_curve(self.ec, "cpu"))

    def test_the_two_fans_cache_separately(self):
        self.ec.loaded_with(fc.PRESET_CURVES["balanced"]["gpu"], "gpu")
        fc.read_curve(self.ec, "cpu", cached=True)
        self.ec.reads = 0
        fc.read_curve(self.ec, "gpu", cached=True)
        self.assertEqual(self.ec.reads, PER_CURVE, "gpu served from cpu's entry")

    def test_it_expires(self):
        fc.read_curve(self.ec, "cpu", cached=True)
        self.ec.reads = 0
        with mock.patch.object(fc.time, "monotonic",
                               return_value=time.monotonic() + fc.CURVE_TTL_S + 1):
            fc.read_curve(self.ec, "cpu", cached=True)
        self.assertEqual(self.ec.reads, PER_CURVE)

    def test_the_caller_gets_a_copy(self):
        """Handing out the cached lists makes one caller's mutation every
        later caller's reading. read_state() puts this straight into a JSON
        response that the UI is free to do anything with."""
        first = fc.read_curve(self.ec, "cpu", cached=True)
        first[0][2] = 99
        self.assertNotEqual(fc.read_curve(self.ec, "cpu", cached=True)[0][2], 99)


class InvalidationTest(unittest.TestCase):
    """A stale curve surviving a write is the failure that matters: the UI
    would show the old table and the owner would believe the write failed."""

    def setUp(self):
        fc.invalidate_curves()

    def test_writing_drops_the_cache(self):
        ec = CountingEC().loaded_with(GOOD)
        fc.read_curve(ec, "cpu", cached=True)
        other = [[u, d, min(100, q + 5)] for u, d, q in GOOD]
        fc.write_curve(ec, "cpu", other)
        self.assertEqual(fc.read_curve(ec, "cpu", cached=True), other)

    def test_a_failed_write_does_not_leave_a_stale_entry(self):
        """Invalidated before and after, so a write that dies partway cannot
        leave the cache asserting either the old table or the new one."""
        class Failing(CountingEC):
            def write_verify(self, addr, value):
                if self.writes > 10:
                    raise RuntimeError("EC went away")
                return super().write_verify(addr, value)

        ec = Failing().loaded_with(GOOD)
        fc.read_curve(ec, "cpu", cached=True)
        with self.assertRaises(RuntimeError):
            fc.write_curve(ec, "cpu", [[u, d, min(100, q + 5)] for u, d, q in GOOD])
        ec.reads = 0
        fc.read_curve(ec, "cpu", cached=True)
        self.assertEqual(ec.reads, PER_CURVE, "served a stale table after a "
                                              "failed write")

    def test_writing_one_fan_does_not_drop_the_other(self):
        ec = CountingEC().loaded_with(GOOD).loaded_with(
            fc.PRESET_CURVES["balanced"]["gpu"], "gpu")
        fc.read_curve(ec, "cpu", cached=True)
        fc.read_curve(ec, "gpu", cached=True)
        fc.write_curve(ec, "cpu", GOOD)
        ec.reads = 0
        fc.read_curve(ec, "gpu", cached=True)
        self.assertEqual(ec.reads, 0)


class CallersTest(unittest.TestCase):

    def test_read_state_asks_for_the_cached_curves(self):
        import inspect
        from hydroc import hardware
        src = inspect.getsource(hardware.Hardware.read_state)
        self.assertIn("cached=True", src)

    def test_drift_accepts_a_reading_instead_of_taking_its_own(self):
        from hydroc import hardware
        hw = hardware.Hardware.__new__(hardware.Hardware)
        with mock.patch.object(hardware.Hardware, "read_state") as rs:
            hardware.Hardware.drift(hw, {"cpu_pl1": 75}, {"cpu_pl1": 75})
        rs.assert_not_called()

    def test_drift_still_reads_when_not_given_one(self):
        from hydroc import hardware
        hw = hardware.Hardware.__new__(hardware.Hardware)
        with mock.patch.object(hardware.Hardware, "read_state",
                               return_value={}) as rs:
            hardware.Hardware.drift(hw, {"cpu_pl1": 75})
        rs.assert_called_once()

    def test_the_state_route_reads_once(self):
        """It read once for its own reply and again inside drift(), so every
        poll paid twice and reported drift against a snapshot the caller
        never saw."""
        import inspect
        from hydroc import server
        src = inspect.getsource(server.Handler.do_GET)
        block = src[src.index('"/api/state"'):][:600]
        self.assertEqual(block.count("_hw.read_state()"), 1)
        self.assertIn("_hw.drift(profile, state)", block)


if __name__ == "__main__":
    unittest.main()
