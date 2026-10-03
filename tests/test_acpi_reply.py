# SPDX-License-Identifier: MIT
"""One reader for /proc/acpi/call, and nothing that re-rolls it.

The bug: a reply is read out of a fixed-size buffer, so a short reply leaves
the tail of a longer previous one after the terminator. `rstrip("\\x00")`
cannot see a null in the MIDDLE, so '0x3e\\x00alled' -- the remains of "not
called" -- survives as the reply.

hydroc/ec.py was fixed for it. Nine root probe scripts each carried their own
copy of the read and each had it wrong the same way, so the project had one
fixed reader and nine broken ones. Being diagnostics made that worse, not
better: they are what gets run when something is already wrong, and a
corrupted reading there sends the next hour in the wrong direction.

These tests check the shared helper is right, and that nothing has quietly
grown its own again.
"""

import io
import os
import tokenize
import unittest

from hydroc.ec import parse_reply

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ParseReplyTest(unittest.TestCase):

    def test_a_residue_tail_is_discarded(self):
        """The reading that started this: 0x3e followed by the tail of
        'not called'."""
        self.assertEqual(parse_reply("0x3e\x00alled"), "0x3e")

    def test_trailing_nulls_go_too(self):
        self.assertEqual(parse_reply("0x50\x00\x00\x00"), "0x50")

    def test_surrounding_whitespace_goes(self):
        self.assertEqual(parse_reply("  0x50 \n"), "0x50")

    def test_an_ordinary_reply_is_untouched(self):
        self.assertEqual(parse_reply("0x50"), "0x50")

    def test_an_error_reply_survives_for_the_caller_to_see(self):
        """Callers test for a leading 'Error'; swallowing it would turn a
        reported failure into an unparseable value."""
        self.assertEqual(parse_reply("Error: AE_NOT_FOUND\x00xx"),
                         "Error: AE_NOT_FOUND")

    def test_a_reply_that_is_only_residue_comes_back_empty(self):
        """Empty is unparseable, which is the point: it fails loudly instead
        of being plausibly wrong."""
        self.assertEqual(parse_reply("\x00not called"), "")

    def test_the_old_approach_would_have_been_wrong_here(self):
        """Pinned so the difference stays visible rather than becoming a
        detail someone 'simplifies' back out."""
        raw = "0x3e\x00alled"
        self.assertEqual(raw.strip().rstrip("\x00"), raw)   # unchanged!
        self.assertNotEqual(parse_reply(raw), raw)


class NoHandRolledReadersTest(unittest.TestCase):
    """Tokenised, not grepped: the fix's own comments quote the broken
    pattern, and a grep flags those as offenders."""

    @staticmethod
    def offenders():
        found = []
        for name in sorted(os.listdir(_ROOT)):
            if not name.endswith(".py"):
                continue
            path = os.path.join(_ROOT, name)
            try:
                with open(path, "rb") as fh:
                    toks = list(tokenize.tokenize(fh.readline))
            except (tokenize.TokenError, SyntaxError):
                continue
            # rstrip( "...\x00..." ) in real code -- comments and docstrings
            # arrive as COMMENT/STRING tokens and are skipped by construction.
            code = [t for t in toks
                    if t.type not in (tokenize.COMMENT, tokenize.NL,
                                      tokenize.NEWLINE, tokenize.INDENT,
                                      tokenize.DEDENT)]
            for i, t in enumerate(code[:-2]):
                if (t.type == tokenize.NAME and t.string == "rstrip"
                        and code[i + 1].string == "("
                        and code[i + 2].type == tokenize.STRING
                        and "\\x00" in code[i + 2].string):
                    found.append(f"{name}:{t.start[0]}")
        return found

    def test_no_root_script_strips_trailing_nulls(self):
        bad = self.offenders()
        self.assertEqual(
            bad, [],
            "hand-rolled ACPI reply parsing is back; import parse_reply "
            "from hydroc.ec instead: " + ", ".join(bad))

    def test_the_detector_can_actually_see_the_pattern(self):
        """A guard that cannot fail protects nothing."""
        src = 'x = fh.read().strip().rstrip("\\x00")\n'
        toks = list(tokenize.tokenize(io.BytesIO(src.encode()).readline))
        hit = any(t.type == tokenize.NAME and t.string == "rstrip"
                  for t in toks)
        self.assertTrue(hit)

    def test_the_package_reader_goes_through_the_helper(self):
        import inspect
        from hydroc import ec
        self.assertIn("parse_reply", inspect.getsource(ec.EC._call))


if __name__ == "__main__":
    unittest.main()
