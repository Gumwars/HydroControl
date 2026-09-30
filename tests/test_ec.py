# SPDX-License-Identifier: MIT
"""The ACPI call path itself, below every register this project reads."""

import io
import unittest
from unittest import mock

from hydroc import ec as ec_mod


class ReplyTruncationTest(unittest.TestCase):
    """/proc/acpi/call hands back a fixed-size buffer.

    A short reply leaves the tail of a longer previous one after the null
    terminator, so the read comes back as '0x3e\\x00alled' -- 0x3e, then what
    is left of "not called". rstrip("\\x00") cannot help: the null is in the
    middle, not at the end.

    This surfaced reading a fan table and had been present the whole time.
    The class comment attributed "garbled replies" to EC flakiness and the
    6 ms pacing; some of them were this.

    It fails loudly rather than silently -- a residue leaves the string
    unparseable rather than plausibly wrong, so no recorded measurement is
    suspect -- but it fails reads that were fine.
    """

    def reply(self, text):
        """Drive the real EC._call with `text` as what the device returns.

        open() is patched rather than a temp file used, because _call opens
        the path for writing first -- on /proc/acpi/call that issues the
        call, but on an ordinary file it truncates the reply before the read.
        """
        ec = ec_mod.EC("/dev/null")
        ec_mod.EC._last_access = 0.0

        def fake_open(path, mode="r", *a, **k):
            return io.StringIO() if "w" in mode else io.StringIO(text)

        with mock.patch("builtins.open", fake_open):
            return ec._call("ignored")

    def test_a_residue_after_the_null_is_dropped(self):
        self.assertEqual(self.reply("0x3e\x00alled"), "0x3e")

    def test_a_clean_reply_is_unchanged(self):
        self.assertEqual(self.reply("0x50\x00"), "0x50")

    def test_whitespace_still_goes(self):
        self.assertEqual(self.reply("  0x50  \x00junk"), "0x50")

    def test_an_error_reply_survives_truncation(self):
        """read() dispatches on startswith('Error'); truncation must not eat
        the prefix or the error turns into an unparseable-reply report."""
        self.assertTrue(self.reply("Error: AE_NOT_FOUND\x00").startswith("Error"))

    def test_the_residue_case_now_parses_as_a_register(self):
        """The exact reply that broke a fan-table read on the hardware."""
        ec = ec_mod.EC("/dev/null")
        ec_mod.EC._last_access = 0.0

        def fake_open(path, mode="r", *a, **k):
            return io.StringIO() if "w" in mode else io.StringIO("0x3e\x00alled")

        with mock.patch("builtins.open", fake_open):
            self.assertEqual(ec.read(0x0F0D), 0x3E)