# SPDX-License-Identifier: MIT
"""Byte 7 of an 08h/14h/1Ah HID packet is `save`, and save=1 writes flash.

Found by decompiling Control Center's own service, which is where the packet
layout came from. HANDOFF.md asserted "no code path here has ever written
flash" and that was wrong in two places:

  lb_off()          1A 00 00 00 00 00 00 01   timeout off, SAVE
  probe "commit"    1A 00 01 04 00 00 00 01   idle timeout 4, SAVE

The first ran on every chin-bar off. The second additionally persisted a
4-unit idle timeout into the device, which is the first thing to suspect if a
keyboard or bar ever goes dark by itself after sitting idle.

Neither was doing damage in any single call -- a disabled timeout, saved, is
harmless -- but flash has a finite write budget and none of these calls needed
to spend it. A deliberate "save to onboard flash" from the keyboard UI is the
one place save=1 belongs, and it is not in this repository's automatic paths.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Packets whose byte 7 is the save flag.
COMMANDS = (0x08, 0x14, 0x1A)

# A python byte-list or bytes([...]) literal of exactly eight 0x.. values.
LITERAL = re.compile(
    r"(?:bytes\(\s*)?\[\s*((?:0x[0-9A-Fa-f]{2}\s*,\s*){7}0x[0-9A-Fa-f]{2})\s*\]")


def literals():
    """Every 8-byte HID packet literal in the tree, with its source."""
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts or path.name == "test_no_flash_writes.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for m in LITERAL.finditer(line):
                vals = [int(v.strip(), 16) for v in m.group(1).split(",")]
                yield path.relative_to(ROOT), n, vals, line.strip()


class NoFlashWriteTest(unittest.TestCase):

    def test_no_automatic_path_sets_the_save_byte(self):
        offenders = [(p, n, v, l) for p, n, v, l in literals()
                     if v[0] in COMMANDS and v[7] == 1]
        self.assertEqual(
            [], offenders,
            "save=1 writes device flash:\n" + "\n".join(
                f"  {p}:{n}  {l}" for p, n, _v, l in offenders))

    def test_no_automatic_path_persists_an_idle_timeout(self):
        """1A with byte 2 set enables an idle timeout. Saved, that is how a
        bar starts going dark on its own weeks later."""
        offenders = [(p, n, l) for p, n, v, l in literals()
                     if v[0] == 0x1A and v[2] == 1 and v[7] == 1]
        self.assertEqual([], offenders,
                         "saved idle timeout:\n" + "\n".join(
                             f"  {p}:{n}  {l}" for p, n, l in offenders))

    def test_the_scan_actually_finds_packets(self):
        """A regex that matches nothing would pass both tests above."""
        found = [v for _p, _n, v, _l in literals() if v[0] in COMMANDS]
        self.assertGreater(len(found), 10,
                           "the literal scan found almost nothing -- it is "
                           "probably broken, and the guard is vacuous")

    def test_lb_off_still_turns_the_bar_off(self):
        """The fix flips one byte; it must not have dropped the packet."""
        src = (ROOT / "kbctrl" / "kbctrl" / "hardware.py").read_text()
        body = src.split("def lb_off(")[1].split("\ndef ")[0]
        self.assertIn("0x1A", body)
        self.assertIn("0x12", body)
        self.assertEqual(body.count("_lb_ctrl"), 1)


if __name__ == "__main__":
    unittest.main()
