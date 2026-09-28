# SPDX-License-Identifier: MIT
"""Scanning an ITE EC image for register logic.

The point of this file is a comparison we cannot yet run: G1's 117.ELUK against
G2's 125.ELUK. Everything here exists so that when the second image arrives the
answer is a command rather than an afternoon of hex.

Two failure modes are worth guarding against specifically.

A false negative is the expensive one. If the scanner misses a sequence that is
present, the comparison reports a feature absent from a build that has it, and
we would conclude the EC never implements charging profiles when in fact it
does -- the same shape of error as reading 0xFF from unmapped space and calling
it a confirmed prediction. So the matchers are pinned against the real byte
sequences decoded from the G2 image at known offsets.

A false positive matters too but costs less: it would say both builds have the
feature and send us back to probing, which is where we already are.

And the file must not be able to flash anything. Cross-flashing an EC image
between projects bricks the machine with no recovery short of a clip on the
flash part.
"""

import importlib.util
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "ec_image_scan",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "ec_image_scan.py"))


def load():
    m = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(m)
    return m


def image(*chunks: bytes, size: int = 4096) -> bytes:
    """A synthetic image: code chunks laid end to end, padded with 0xFF."""
    body = b"".join(chunks)
    return body + b"\xff" * max(0, size - len(body))


# The real bytes, transcribed from 125.ELUK at the offsets named. Using the
# genuine article rather than something hand-rolled is the whole point: a
# matcher that only recognises its own test fixture recognises nothing.
G2_PROFILE_STATIONARY = bytes.fromhex("90 07 A6 E0 54 30 64 20 70 05 7B C8".replace(" ", ""))
G2_PROFILE_BALANCED = bytes.fromhex("90 07 A6 E0 54 30 64 10 70 04 7B 64".replace(" ", ""))
G2_PROFILE_CLEARED = bytes.fromhex("90 07 A6 E0 54 CF F0 12 DB 2D".replace(" ", ""))
G2_CAPABILITY_GATE = bytes.fromhex("90 07 8E E0 44 08 12 D5 56 70 07".replace(" ", ""))
G2_CAPACITY_SUBTRACT = bytes.fromhex("90 04 AB E0 C3 9B 90 07 B9".replace(" ", ""))
G2_REACHED_SET = bytes.fromhex("90 07 B9 E0 40 09 44 80 F0".replace(" ", ""))
G2_REACHED_CLEARED = bytes.fromhex("90 07 B9 E0 54 7F F0".replace(" ", ""))
G2_PROJECT_INIT = bytes.fromhex("90 07 40 74 1A F0".replace(" ", ""))


class CannotFlashTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_no_write_or_flash_path_exists(self):
        for name in dir(self.m):
            low = name.lower()
            self.assertNotIn("flash", low, f"{name} in a read-only scanner")
            self.assertNotIn("write", low, f"{name} in a read-only scanner")

    def test_images_are_only_ever_opened_for_reading(self):
        with open(_SPEC.origin, encoding="utf-8") as fh:
            src = fh.read()
        for mode in ('"wb"', "'wb'", '"r+b"', "'r+b'", '"ab"', "'ab'"):
            self.assertNotIn(mode, src, f"{mode} appears in a read-only tool")


class DptrLoadTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_finds_the_three_byte_load(self):
        self.assertEqual(self.m.find_loads(image(b"\x00" + G2_PROFILE_CLEARED),
                                           0x07A6), [1])

    def test_finds_every_occurrence(self):
        img = image(G2_PROFILE_STATIONARY, G2_PROFILE_BALANCED)
        self.assertEqual(len(self.m.find_loads(img, 0x07A6)), 2)

    def test_does_not_confuse_the_address_halves(self):
        """0x07A6 is 90 07 A6. A6 07 is a different instruction entirely."""
        self.assertEqual(self.m.find_loads(image(b"\x90\xa6\x07"), 0x07A6), [])

    def test_a_coincidental_data_byte_run_still_counts(self):
        """The scanner cannot tell code from a table that happens to match, and
        must not pretend otherwise -- which is why counts inform a comparison
        rather than standing alone as a verdict."""
        self.assertEqual(len(self.m.find_loads(image(b"\x90\x07\xa6"), 0x07A6)), 1)


class ProjectIdTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_reads_the_id_the_build_writes(self):
        self.assertEqual(self.m.project_id(image(G2_PROJECT_INIT)), 0x1A)

    def test_g1_would_report_its_own(self):
        g1 = bytes.fromhex("900740" + "7419" + "f0")
        self.assertEqual(self.m.project_id(image(g1)), 0x19)

    def test_a_plain_read_of_the_register_is_not_an_init(self):
        """90 07 40 / E0 is reading PROJECT_ID, not declaring it."""
        self.assertIsNone(self.m.project_id(image(b"\x90\x07\x40\xe0\x22")))

    def test_absent_is_none_not_zero(self):
        self.assertIsNone(self.m.project_id(image(b"\x00" * 16)))


class SequenceTest(unittest.TestCase):
    """Pinned against the real bytes. A matcher that drifts off these silently
    turns a present feature into an absent one."""

    def setUp(self):
        self.m = load()

    def names(self, img, addr):
        return sorted(n for n, _o, _w in self.m.find_sequences(img, addr))

    def test_stationary_branch(self):
        self.assertIn("profile compared against STATIONARY",
                      self.names(image(G2_PROFILE_STATIONARY), 0x07A6))

    def test_balanced_branch(self):
        self.assertIn("profile compared against BALANCED",
                      self.names(image(G2_PROFILE_BALANCED), 0x07A6))

    def test_the_two_branches_are_told_apart(self):
        """XRL #20h and XRL #10h differ in one byte; conflating them would
        report a stationary ceiling in a build that only has balanced."""
        self.assertNotIn("profile compared against BALANCED",
                         self.names(image(G2_PROFILE_STATIONARY), 0x07A6))

    def test_profile_clear(self):
        self.assertIn("profile bits cleared",
                      self.names(image(G2_PROFILE_CLEARED), 0x07A6))

    def test_capability_gate(self):
        self.assertIn("capability bit 3 tested",
                      self.names(image(G2_CAPABILITY_GATE), 0x078E))

    def test_capacity_subtraction(self):
        self.assertIn("capacity read then subtracted",
                      self.names(image(G2_CAPACITY_SUBTRACT), 0x04AB))

    def test_reached_set_and_cleared_are_distinct(self):
        self.assertEqual(self.names(image(G2_REACHED_SET), 0x07B9),
                         ["CHARGE_CTRL_REACHED set"])
        self.assertEqual(self.names(image(G2_REACHED_CLEARED), 0x07B9),
                         ["CHARGE_CTRL_REACHED cleared"])

    def test_a_bare_reference_is_not_a_decision(self):
        """Touching 0x07A6 to flip the touchpad bit is not caring which profile
        is selected. Counting references alone would not know the difference."""
        touchpad = bytes.fromhex("9007a6" + "e0" + "5440")
        self.assertEqual(self.names(image(touchpad), 0x07A6), [])

    def test_the_match_must_start_at_the_load(self):
        """A sequence sixteen bytes later belongs to different code."""
        img = image(b"\x90\x07\xa6" + b"\x00" * 20 + b"\xe0\x54\x30\x64\x20")
        self.assertEqual(self.names(img, 0x07A6), [])


class EntropyTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_plain_code_is_low(self):
        self.assertLess(self.m.entropy(b"\x90\x07\xa6\xe0" * 256), 4.0)

    def test_empty_does_not_divide_by_zero(self):
        self.assertEqual(self.m.entropy(b""), 0.0)

    def test_uniform_bytes_approach_eight(self):
        self.assertGreater(self.m.entropy(bytes(range(256)) * 8), 7.9)


class CompareTest(unittest.TestCase):
    """The comparison is the deliverable."""

    def setUp(self):
        self.m = load()

    def scan(self, *chunks):
        return self.m.scan(image(*chunks))

    def test_a_feature_in_one_build_only_is_visible(self):
        g2 = self.scan(G2_PROJECT_INIT, G2_PROFILE_STATIONARY, G2_CAPACITY_SUBTRACT)
        g1 = self.scan(bytes.fromhex("9007407419f0"))
        self.assertTrue(any(h[0] == "profile compared against STATIONARY"
                            for h in g2["sequences"][0x07A6]))
        self.assertEqual(g1["sequences"][0x07A6], [])
        self.assertEqual((g1["project_id"], g2["project_id"]), (0x19, 0x1A))

    def test_identical_builds_show_no_difference(self):
        a = self.scan(G2_PROFILE_STATIONARY)
        b = self.scan(G2_PROFILE_STATIONARY)
        self.assertEqual([h[0] for h in a["sequences"][0x07A6]],
                         [h[0] for h in b["sequences"][0x07A6]])

    def test_scan_reports_every_known_register(self):
        r = self.scan(G2_PROFILE_STATIONARY)
        self.assertEqual(set(r["loads"]), set(self.m.REGISTERS))


if __name__ == "__main__":
    unittest.main()
