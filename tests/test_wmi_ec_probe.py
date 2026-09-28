# SPDX-License-Identifier: MIT
"""The WMI read door.

Every EC access this project has made goes through ECRR/ECRW, which the DSDT
implements as MMIO. tuxedo-drivers uses a WMI method instead. Both set the
byte; whether both make the EC *act* is a different question, and it is the
best remaining explanation for why the charging profiles have never engaged
here while a user on the same model sees them work.

Two properties matter, and one of them is the whole point of the file.

It must be unable to write. The function byte is the only thing separating a
read from a write in this protocol -- byte 5, where 1 is READ and 0 is WRITE.
A file that can emit a 0 there is a file that can poke the EC by accident, so
the tests pin that byte and the absence of any write path.

And it must not mistake a firmware error for data. 0xFEFEFEFE is the read
failure marker; returning 0xFE as though it were a register value would be the
same class of mistake as reading 0xFF from unmapped space and calling it a
confirmed prediction.
"""

import importlib.util
import os
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "wmi_ec_probe",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "wmi_ec_probe.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


class CannotWriteTest(unittest.TestCase):
    """The safety property. This file reads."""

    def setUp(self):
        self.m = load()

    def test_the_function_byte_is_read(self):
        """Byte 5 is the read/write selector. 1 is READ."""
        self.assertEqual(self.m.read_arg(0x07A6)[5], 1)
        self.assertEqual(self.m.FUNCTION_READ, 1)

    def test_no_write_helper_exists_at_all(self):
        """Not disabled -- absent. A disabled write is one edit from enabled."""
        for name in dir(self.m):
            self.assertNotIn("write", name.lower(),
                             f"{name} looks like a write path in a read-only probe")

    def test_the_module_never_mentions_the_write_function_code(self):
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("FUNCTION_WRITE", src)

    def test_only_ecrr_and_wmbc_are_ever_called(self):
        """ECRW must not appear as a callable target anywhere."""
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("INOU.ECRW", src)


class ArgEncodingTest(unittest.TestCase):
    """Matching tuxedo's packing exactly, including its buffer length."""

    def setUp(self):
        self.m = load()

    def test_address_is_split_low_then_high(self):
        a = self.m.read_arg(0x07A6)
        self.assertEqual(a[0], 0xA6)
        self.assertEqual(a[1], 0x07)

    def test_length_matches_what_the_working_driver_actually_sends(self):
        """tuxedo memsets 40 bytes then passes sizeof(u32*) = 8. Whatever the
        intent, 8 is what reaches firmware in the implementation that works."""
        self.assertEqual(len(self.m.read_arg(0x07A6)), 8)
        self.assertEqual(self.m.ARG_LEN, 8)

    def test_every_other_byte_is_zero(self):
        a = self.m.read_arg(0x0783)
        for i in (2, 3, 4, 6, 7):
            self.assertEqual(a[i], 0, f"byte {i} should be zero")

    def test_a_high_address_still_encodes(self):
        a = self.m.read_arg(0x0F5F)
        self.assertEqual((a[0], a[1]), (0x5F, 0x0F))


class ResponseTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def read(self, raw):
        with mock.patch.object(self.m, "_call", return_value=raw):
            return self.m.wmi_read(0x07A6)

    def test_buffer_response_takes_the_low_byte(self):
        self.assertEqual(self.read("{0xFC, 0x00, 0x00, 0x00}"), (0xFC, None))

    def test_integer_response_takes_the_low_byte(self):
        self.assertEqual(self.read("0x000000fc"), (0xFC, None))

    def test_the_firmware_error_marker_is_not_data(self):
        """0xFEFEFEFE means the read failed. Reporting 0xFE as a register value
        is how 0x0984 fooled us once already."""
        v, err = self.read("{0xFE, 0xFE, 0xFE, 0xFE}")
        self.assertIsNone(v)
        self.assertIn("FEFEFEFE", err)

    def test_an_acpi_error_is_reported_not_parsed(self):
        v, err = self.read("Error: AE_NOT_FOUND")
        self.assertIsNone(v)
        self.assertIn("Error", err)

    def test_garbage_is_an_error_not_a_zero(self):
        v, err = self.read("wat")
        self.assertIsNone(v)
        self.assertIsNotNone(err)

    def test_empty_response_is_an_error(self):
        v, err = self.read("{}")
        self.assertIsNone(v)
        self.assertIsNotNone(err)

    def test_a_real_0xfe_byte_is_still_data(self):
        """Only all four bytes being 0xFE is the marker."""
        self.assertEqual(self.read("{0xFE, 0x00, 0x00, 0x00}"), (0xFE, None))


class CompareTest(unittest.TestCase):
    """A door is trustworthy when it is boringly repeatable, not when one
    sample happens to match."""

    def setUp(self):
        self.m = load()

    def run_compare(self, wmi_vals, ecrr_vals):
        with mock.patch.object(self.m, "wmi_read",
                               side_effect=[(v, None) for v in wmi_vals]), \
             mock.patch.object(self.m, "ecrr_read",
                               side_effect=[(v, None) for v in ecrr_vals]):
            return self.m.compare([(0x07A6, "")], len(wmi_vals))[0]

    def test_stable_agreement(self):
        r = self.run_compare([0x20] * 3, [0x20] * 3)
        self.assertTrue(r["agree"])

    def test_a_flapping_wmi_read_is_not_agreement(self):
        r = self.run_compare([0x20, 0x37, 0x20], [0x20] * 3)
        self.assertFalse(r["agree"])
        self.assertFalse(r["wmi_stable"])

    def test_a_flapping_ecrr_read_is_not_agreement(self):
        """0x07C6 glitches through ECRR; that must not be blamed on WMI."""
        r = self.run_compare([0x04] * 3, [0x04, 0x5F, 0x04])
        self.assertFalse(r["agree"])
        self.assertFalse(r["ecrr_stable"])

    def test_stable_but_different_is_a_real_disagreement(self):
        r = self.run_compare([0x20] * 3, [0x00] * 3)
        self.assertFalse(r["agree"])
        self.assertTrue(r["wmi_stable"])
        self.assertTrue(r["ecrr_stable"])


if __name__ == "__main__":
    unittest.main()
