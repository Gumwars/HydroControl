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
        self.assertIn("HIDKEYBOARD3_VER_HIGH = 0x20", self.src)

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
        self.assertIn("APPLIES", self.src)
        self.assertIn("turned off", self.src)

    def test_it_says_what_to_do_when_the_daemon_holds_the_device(self):
        self.assertIn("hydroc-server", self.src)


if __name__ == "__main__":
    unittest.main()
