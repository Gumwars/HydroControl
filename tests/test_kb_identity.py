# SPDX-License-Identifier: MIT
"""Which keyboard panel this is, and whether the vendor's table applies.

hydroc.rgb.CHEAT_RGB was read off Control Center for HIDKeyboard3 panels
(type 21). The vendor picks that table by firmware version and uses a
different one, or none, for other panels -- so applying it blind is as likely
to make colours worse as better, which is what "white looks purplish after
the correction" would mean.

The probe must stay read-only. It runs as root against a keyboard the daemon
may also hold, and a diagnostic that changes colours while answering a
question about colours is worse than no diagnostic.
"""

import os
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "kb_identity.py")


class ReadOnlyTest(unittest.TestCase):

    def setUp(self):
        with open(_SRC, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_it_only_asks_for_the_version(self):
        for bad in ("set_key_colors", "set_brightness", "set_effect",
                    "turn_off", "apply_per_key", "restore_default_palette"):
            self.assertNotIn(bad, self.src,
                             f"{bad} would change the keyboard while asking "
                             f"a question about it")

    def test_it_writes_no_ec_register_and_no_flash(self):
        for bad in ("ECRW", "write_verify", "update_bits", "0x1A"):
            self.assertNotIn(bad, self.src)

    def test_it_knows_which_byte_decides(self):
        self.assertIn("VER_HIGH_TYPES", self.src)
        self.assertIn("REG_KBID = 0x073C", self.src)

    def test_it_explains_the_stock_cli_failure(self):
        """`ite8291r3-ctl query --fw-version` says "no suitable device found"
        because the library matches 0x6004/0x6006/0xCE00 and this keyboard is
        048d:600b. Without that recorded, the next person repeats the dead
        end."""
        self.assertIn("600b", self.src)
        self.assertIn("PRODUCT_IDS", self.src)

    def test_it_tells_the_reader_what_each_answer_means(self):
        """A probe that prints a number and leaves the interpretation to
        memory is how 0x0741 bit 0 got read as a charging enable."""
        self.assertIn("is the table for this", self.src)
        self.assertIn("wrong table for this panel", self.src)

    def test_it_says_what_to_do_when_the_daemon_holds_the_device(self):
        self.assertIn("hydroc-server", self.src)


if __name__ == "__main__":
    unittest.main()


class TypeMappingTest(unittest.TestCase):
    """Ver_High -> panel type, and the hex/decimal trap inside it.

    The original notes read "Ver_High == 0x20 gives type 21" and this panel
    reports 0x22, which looks adjacent and is not: 0x20 is 32 decimal, 0x22 is
    34, and they are separate branches of ILM_RGBKB_Init. Acting on the
    resemblance put cheatRGB_HIDKeyboard3 -- a types 21/22 table -- in front
    of a type 7 panel, and white went purple.
    """

    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("kb_identity", _SRC)
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        with open(_SRC, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_this_panel_resolves_to_type_7(self):
        """Ver_High 0x22 with KBID 0x18, both measured on this machine."""
        family, by_kbid = self.m.VER_HIGH_TYPES[0x22]
        self.assertEqual(family, (7, 8, 9, 10))
        self.assertEqual(by_kbid[0x18], 7)

    def test_0x20_and_0x22_are_different_branches(self):
        """The whole mistake in one assertion."""
        self.assertNotEqual(self.m.VER_HIGH_TYPES[0x20][0],
                            self.m.VER_HIGH_TYPES[0x22][0])

    def test_hidkeyboard3_is_types_21_and_22_only(self):
        self.assertEqual(self.m.HIDKEYBOARD3_TYPES, (21, 22))
        self.assertNotIn(7, self.m.HIDKEYBOARD3_TYPES)

    def test_the_kbid_read_is_read_only(self):
        import inspect
        src = inspect.getsource(self.m._read_kbid)
        self.assertIn(".read(", src)
        self.assertNotIn(".write", src)

    def test_an_unreadable_kbid_degrades_to_the_family(self):
        """Without EC access the probe still names the candidates rather
        than guessing one."""
        self.assertIn("KBID unread", self.src)
