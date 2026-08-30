# SPDX-License-Identifier: MIT
"""charge_ctrl_watch, driven by synthetic charge cycles.

The bug this guards against: the change key was (raw, prof, status) and left
capacity out, so during a steady charge -- register constant, status constant --
the watcher printed nothing at all while the battery climbed 75% to 78%.
Capacity is the variable the whole experiment exists to measure, so the
instrument was blind to precisely the thing under test, and a working run looked
like a hung script.

No hardware: EC reads and sysfs are replaced, and the sleep drives the clock.
"""

import contextlib
import importlib.util
import io
import os
import sys
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "charge_ctrl_watch",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "charge_ctrl_watch.py"))


def run_cycle(caps, thresholds=None, status="Charging", argv=()):
    """Replay a capacity series through main(); return everything printed."""
    cw = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(cw)
    thresholds = thresholds or [80] * len(caps)
    st = {"i": 0, "t": 0.0}

    def at(seq):
        return seq[min(st["i"], len(seq) - 1)]

    cw.ec_read = lambda a: (at(thresholds) & 0x7F
                            if a == cw.REG_CHARGE_CTRL else 0x20)
    cw.sysfs_int = lambda n: {"capacity": at(caps),
                              "current_now": 2_000_000,
                              "voltage_now": 16_800_000}.get(n)
    cw.sysfs = lambda n: (status if n == "status" else None)

    def sleep(_):
        st["i"] += 1
        st["t"] += 1.0
        if st["i"] >= len(caps):
            raise KeyboardInterrupt

    cw.time.sleep = sleep
    cw.time.monotonic = lambda: st["t"]

    real_euid, real_exists = os.geteuid, os.path.exists
    os.geteuid = lambda: 0
    os.path.exists = lambda p: True if p == cw.CALL else real_exists(p)
    sys.argv = ["charge_ctrl_watch", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            cw.main()
    finally:
        os.geteuid, os.path.exists = real_euid, real_exists
    return buf.getvalue()


def sample_rows(out):
    return [l for l in out.splitlines()
            if len(l) > 8 and l[0:2].isdigit() and l[2] == ":"]


class SteadyChargeTest(unittest.TestCase):
    """The regression: silence during the interesting part."""

    def test_a_climbing_capacity_prints_every_step(self):
        out = run_cycle(list(range(75, 82)))
        caps = [r.split()[5] for r in sample_rows(out)]
        for want in ("75", "76", "77", "78", "79", "80"):
            self.assertIn(want, caps, f"capacity {want}% never printed")

    def test_a_plateau_is_not_silence(self):
        # A flat stretch is data, and silence is indistinguishable from a
        # crashed script over a multi-hour run.
        out = run_cycle([78] * 20, argv=["--heartbeat", "3"])
        self.assertGreater(len(sample_rows(out)), 1,
                           "plateau produced no heartbeat rows")

    def test_heartbeat_off_still_prints_changes(self):
        out = run_cycle(list(range(75, 80)), argv=["--heartbeat", "999999"])
        self.assertGreaterEqual(len(sample_rows(out)), 4)


class VerdictTest(unittest.TestCase):
    """The question is whether capacity climbs past the threshold."""

    def test_exceeding_the_threshold_says_not_enforced(self):
        self.assertIn("NOT being enforced", run_cycle(list(range(75, 101))))

    def test_plateau_at_the_threshold_says_holding(self):
        out = run_cycle(list(range(70, 81)) + [80, 79, 80, 79, 80])
        self.assertIn("threshold IS holding", out)

    def test_stop_start_within_the_band_is_not_called_a_failure(self):
        # "stops then resumes" is the EC's hysteresis, not a broken threshold.
        out = run_cycle([79, 80, 79, 80, 79, 80], status="Not charging")
        self.assertNotIn("NOT being enforced", out)

    def test_short_cycle_refuses_to_conclude(self):
        out = run_cycle([70, 71, 72])
        self.assertIn("did not run long enough", out)

    def test_no_capacity_readings_refuses_to_conclude(self):
        cw = importlib.util.module_from_spec(_SPEC)
        _SPEC.loader.exec_module(cw)
        out = run_cycle([None, None, None])
        self.assertIn("not enough data", out)


class ClobberTest(unittest.TestCase):
    """The secondary question: does the EC rewrite the threshold field?"""

    def test_a_rewritten_threshold_is_reported(self):
        out = run_cycle(list(range(75, 85)), [80] * 5 + [85] * 5)
        self.assertIn("THRESHOLD WAS REWRITTEN", out)
        self.assertIn("THRESHOLD CHANGED", out)

    def test_a_stable_threshold_is_reported_as_held(self):
        out = run_cycle(list(range(75, 85)))
        self.assertIn("threshold held", out)
        self.assertNotIn("REWRITTEN", out)


class PacingTest(unittest.TestCase):

    def test_ec_access_is_paced(self):
        # DESIGN.md §4.2 -- reading the EC too hard has stopped the fans, and
        # this script runs for hours.
        cw = importlib.util.module_from_spec(_SPEC)
        _SPEC.loader.exec_module(cw)
        self.assertGreaterEqual(cw.EC_DELAY, 0.006)


if __name__ == "__main__":
    unittest.main()
