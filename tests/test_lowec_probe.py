# SPDX-License-Identifier: MIT
"""Low EC RAM, read-only.

A fourth address space, reached a fourth way -- ec_sys over the ACPI EC ports,
not the MMIO window and not WMI. A user reports 0x35 down here is implicated in
a suppressed current_now reading, and this project has no map of 0x00-0xFF at
all.

Two properties matter.

It must be unable to write. ec_sys can be loaded with write_support=1 by
anything on the system, so the guarantee has to come from this file: the EC is
opened 'rb' and there is no write path.

And it must survey rather than stare. Watching 0x35 alone could only ever
report whether 0x35 changed. The question is whether any byte tracks charge
current, and that needs the whole space plus the correlation.
"""

import importlib.util
import os
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "lowec_probe",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "lowec_probe.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class CannotWriteTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_ec_file_is_opened_read_only(self):
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('open(EC_IO, "rb")', src)
        self.assertNotIn('"r+b"', src)
        self.assertNotIn('"wb"', src)

    def test_write_support_check_is_advisory_only(self):
        """It reports whether something else could write; it never changes it."""
        with mock.patch("builtins.open", mock.mock_open(read_data="Y")), \
             mock.patch.object(self.m.subprocess, "run") as r:
            self.assertTrue(self.m.write_support_on())
        r.assert_not_called()

    def test_modprobe_is_called_without_parameters(self):
        with mock.patch.object(self.m.os.path, "exists", return_value=False), \
             mock.patch.object(self.m.subprocess, "run") as r:
            r.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.m.ensure_ec_sys()
        r.assert_called_once_with(["modprobe", "ec_sys"],
                                  capture_output=True, text=True)


class ReadTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_a_short_read_is_rejected(self):
        """128 bytes is not the EC space; treating it as one would misalign
        every address."""
        with mock.patch("builtins.open", mock.mock_open(read_data=b"\x00" * 128)):
            self.assertIsNone(self.m.read_ec())

    def test_a_full_read_is_accepted(self):
        with mock.patch("builtins.open", mock.mock_open(read_data=bytes(256))):
            self.assertEqual(len(self.m.read_ec()), 256)

    def test_an_unreadable_file_is_none_not_an_exception(self):
        with mock.patch("builtins.open", side_effect=OSError("nope")):
            self.assertIsNone(self.m.read_ec())


class SurveyTest(unittest.TestCase):
    """The survey must find movement anywhere, not just where we expect."""

    def setUp(self):
        self.m = load()

    def run_survey(self, frames, currents):
        it = iter(frames)
        cur = iter(currents)
        with mock.patch.object(self.m, "read_ec", side_effect=lambda: next(it)), \
             mock.patch.object(self.m, "battery",
                               side_effect=lambda: {"capacity": "80",
                                                    "status": "Charging",
                                                    "current_ma": next(cur),
                                                    "charge_now": "1",
                                                    "voltage_now": "1"}), \
             mock.patch.object(self.m.time, "sleep"), \
             mock.patch.object(self.m.time, "monotonic",
                               side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 99]):
            return self.m.survey(3, 0)

    def frame(self, kv):
        """Integer addresses cannot be kwargs; take the mapping positionally."""
        b = bytearray(256)
        for a, v in kv.items():
            b[a] = v
        return bytes(b)

    def test_a_static_space_reports_nothing_moved(self):
        f = self.frame({0x35: 0x10})
        res = self.run_survey([f, f, f], [2000, 1800, 1600])
        self.assertEqual(res["moved"], {})

    def test_a_moving_byte_anywhere_is_caught(self):
        """Not just 0x35 -- a byte we have no name for matters just as much."""
        res = self.run_survey([self.frame({0x9A: 1}),
                               self.frame({0x9A: 2}),
                               self.frame({0x9A: 3})],
                              [2000, 1800, 1600])
        self.assertIn(0x9A, res["moved"])

    def test_the_named_byte_is_not_special_cased(self):
        res = self.run_survey([self.frame({0x35: 1, 0x9A: 1}),
                               self.frame({0x35: 2, 0x9A: 2}),
                               self.frame({0x35: 3, 0x9A: 3})],
                              [2000, 1800, 1600])
        self.assertEqual(sorted(res["moved"]), [0x35, 0x9A])

    def test_a_failed_read_does_not_abort_the_survey(self):
        it = iter([self.frame({0x35: 1}), None, self.frame({0x35: 2})])
        with mock.patch.object(self.m, "read_ec", side_effect=lambda: next(it)), \
             mock.patch.object(self.m, "battery",
                               return_value={"capacity": "80", "status": "c",
                                             "current_ma": 1000,
                                             "charge_now": "1", "voltage_now": "1"}), \
             mock.patch.object(self.m.time, "sleep"), \
             mock.patch.object(self.m.time, "monotonic",
                               side_effect=[0, 1, 2, 3, 99]):
            res = self.m.survey(10, 0)
        self.assertEqual(len(res["samples"]), 2)
        self.assertIn(0x35, res["moved"])


if __name__ == "__main__":
    unittest.main()


class MailboxDecodeTest(unittest.TestCase):
    """The mailbox sits at 0x8A-0x8E, not 0x00/0x01.

    The first draft of KNOWN put it at 0x00/0x01 because that is the order the
    names appear in tuxedo's header. The survey contradicted it: those two
    bytes held 0x01 and 0x00 for all 140 samples and match nothing, while
    0x8A/0x8B held 0xA6/0x07 -- the address of the charging profile register,
    the one register that run was writing -- and 0x8D held 0x20, the stationary
    bit pattern. ec_dump.py had already mapped the same block at 0x048A in the
    extended window.

    Pinned because the wrong version was plausible enough to survive review
    once, and because a misplaced mailbox is the kind of error that makes a
    later write probe poke arbitrary EC state.
    """

    def setUp(self):
        self.m = load()

    def test_the_mailbox_is_where_the_evidence_puts_it(self):
        for addr, name in ((0x8A, "LDAT"), (0x8B, "HDAT"), (0x8C, "FLAGS"),
                           (0x8D, "CMDL"), (0x8E, "CMDH")):
            self.assertIn(addr, self.m.KNOWN, f"0x{addr:02X} missing")
            self.assertIn(name, self.m.KNOWN[addr],
                          f"0x{addr:02X} should be {name}")

    def test_the_guessed_locations_are_gone(self):
        for addr in (0x00, 0x01):
            self.assertNotIn(addr, self.m.KNOWN,
                             f"0x{addr:02X} was a guess the survey refuted")

    def test_the_low_and_extended_views_agree_with_ec_dump(self):
        """0x8A here is 0x048A there -- same SFR block, two windows."""
        self.assertIn("0x048A", open(
            os.path.join(os.path.dirname(_SPEC.origin), "ec_dump.py"),
            encoding="utf-8").read())
