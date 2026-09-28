# SPDX-License-Identifier: MIT
"""Asking the EC a question instead of watching it.

Every charge-ceiling test here has been passive. Two full cycles later
CHARGE_CTRL_REACHED has never armed and 0x0742 has never moved, and neither
result separates "the code does not run" from "the code runs and has nothing
to do". That is the flaw in the 0x0742 footprint: a witness that fires only on
a state change is silent when the state does not change, and this suite should
have caught that before the capture rather than after.

0x087F mirrors the threshold and the EC writes it only when the two disagree
(117.ELUK at 0x1C8F5). So make them disagree and watch. A negative here is a
positive result -- the EC was handed something to do.

Two properties are pinned. It must change the threshold through the driver's
sysfs, never by writing the EC, and never hold anything: sustained re-writes
to these registers are the one operation known to have damaged this machine.
And it must always put the original threshold back, including when interrupted,
because leaving someone's charge limit moved is a real consequence.
"""

import importlib.util
import os
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "shadow_probe",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "shadow_probe.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class SafetyTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_there_is_no_ec_write_path(self):
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("ECRW", src)
        self.assertNotIn("WKBC", src)
        self.assertNotIn("WMBC", src)

    def test_the_only_write_target_is_the_driver_sysfs(self):
        self.assertIn("charge_control_end_threshold", self.m.SYSFS)

    def test_the_shadow_is_never_written(self):
        """0x087F is the witness. Writing it would destroy the measurement."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("write_ec", src)
        self.assertEqual(src.count("REG_SHADOW"), src.count("ec_read(REG_SHADOW)") + 1)

    def test_it_does_not_loop_the_write(self):
        """One write, not a hold. DESIGN.md 4.1b."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            body = fh.read().split('"""', 2)[2]
        self.assertEqual(body.count("write_threshold("), 3)  # def + set + restore


class TargetTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_target_always_differs_from_the_current_value(self):
        for cur in range(1, 101):
            self.assertNotEqual(self.m.pick_target(cur, None), cur,
                                f"writing {cur} over {cur} produces no change "
                                f"for the EC to notice, which is the exact "
                                f"silence this probe exists to break")

    def test_an_explicit_target_is_honoured(self):
        self.assertEqual(self.m.pick_target(80, 65), 65)


class VerdictTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_following_means_the_code_ran(self):
        v = self.m.verdict(80, 70, 70)
        self.assertTrue(v["followed"])
        self.assertIn("RAN", v["reading"])

    def test_not_following_is_reported_as_a_positive_result(self):
        """The distinction from a flat 0x0742 is the whole point: there, the
        EC had nothing to do; here, it was given something."""
        v = self.m.verdict(80, 80, 70)
        self.assertFalse(v["followed"])
        self.assertIn("positive result", v["reading"])

    def test_a_partial_move_is_not_following(self):
        self.assertFalse(self.m.verdict(80, 75, 70)["followed"])

    def test_unreadable_is_not_a_negative(self):
        v = self.m.verdict(80, None, 70)
        self.assertFalse(v["followed"])
        self.assertEqual(v["reading"], "unreadable")


class UnmappedTest(unittest.TestCase):
    """0xFF is not a value on this EC.

    0x087F reads 0xFF on the HYDROC-16 G1 -- unmapped space, recorded in
    DESIGN.md 3.2 on 2026-08-27, before this probe existed. The first run of
    this file read 255 and reported "the routine did not run", which is the
    0x0984 mistake again: unmapped space has every bit set and says nothing.

    Two things are pinned. It must refuse, and it must refuse BEFORE touching
    the threshold -- a test whose witness is invisible has no business changing
    the machine's charge limit.
    """

    def setUp(self):
        self.m = load()

    def run_main(self, shadow):
        wrote = []
        with mock.patch.object(self.m.os, "geteuid", return_value=0), \
             mock.patch.object(self.m.os.path, "exists", return_value=True), \
             mock.patch.object(self.m, "read_threshold", return_value=80), \
             mock.patch.object(self.m, "ec_read", side_effect=[0x50, shadow]), \
             mock.patch.object(self.m, "write_threshold",
                               side_effect=lambda v: wrote.append(v)), \
             mock.patch.object(self.m.sys, "argv", ["shadow_probe.py"]):
            with self.assertRaises(SystemExit) as cm:
                self.m.main()
        return str(cm.exception), wrote

    def test_it_refuses_on_an_unmapped_read(self):
        msg, _ = self.run_main(0xFF)
        self.assertIn("unmapped", msg)

    def test_it_does_not_touch_the_threshold_when_it_refuses(self):
        _, wrote = self.run_main(0xFF)
        self.assertEqual(wrote, [], "refused, so nothing should have changed")

    def test_a_real_value_is_not_refused(self):
        """0xFE is a plausible byte. Only all-ones is the unmapped marker."""
        with mock.patch.object(self.m.os, "geteuid", return_value=0), \
             mock.patch.object(self.m.os.path, "exists", return_value=True), \
             mock.patch.object(self.m, "read_threshold", return_value=80), \
             mock.patch.object(self.m, "ec_read", return_value=0xFE), \
             mock.patch.object(self.m, "write_threshold", return_value=None), \
             mock.patch.object(self.m.time, "sleep", side_effect=KeyboardInterrupt), \
             mock.patch.object(self.m.sys, "argv", ["shadow_probe.py"]):
            with self.assertRaises(KeyboardInterrupt):
                self.m.main()


class RestoreTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_original_threshold_is_restored_after_an_interruption(self):
        wrote = []
        with mock.patch.object(self.m.os, "geteuid", return_value=0), \
             mock.patch.object(self.m.os.path, "exists", return_value=True), \
             mock.patch.object(self.m, "read_threshold", return_value=80), \
             mock.patch.object(self.m, "ec_read", return_value=80), \
             mock.patch.object(self.m, "write_threshold",
                               side_effect=lambda v: wrote.append(v)), \
             mock.patch.object(self.m.time, "sleep",
                               side_effect=KeyboardInterrupt), \
             mock.patch.object(self.m.sys, "argv", ["shadow_probe.py"]):
            with self.assertRaises(KeyboardInterrupt):
                self.m.main()
        self.assertEqual(wrote[-1], 80, "must put the user's threshold back")


if __name__ == "__main__":
    unittest.main()
