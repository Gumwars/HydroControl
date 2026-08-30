# SPDX-License-Identifier: MIT
"""LPP dock frames.

The pump codes are NOT in speed order -- 0 is High, 1 Max, 2 Low, 3 Medium --
and the dock accepts any byte without complaint, so treating the code as an
ordered scale silently puts it in the wrong mode. Nothing should pass a raw
integer; PUMP_MODES maps names to codes. These tests pin that mapping so a
future tidy-up into 0,1,2,3 fails loudly instead of quietly mis-driving a pump.

Pure protocol construction -- no BLE, no transport.
"""

import unittest

from hydroc import lpp


class FrameShapeTest(unittest.TestCase):

    def test_frames_are_eight_bytes_delimited_by_FE_EF(self):
        for frame in (lpp.fan_frame(50), lpp.pump_frame("low"),
                      lpp.status_frame(), *lpp.INIT_SEQUENCE):
            self.assertEqual(len(frame), 8, lpp.describe(frame))
            self.assertEqual(frame[0], 0xFE)
            self.assertEqual(frame[-1], 0xEF)

    def test_payload_is_zero_padded(self):
        self.assertEqual(lpp.status_frame(), bytes([0xFE, 0x33, 0, 0, 0, 0, 0, 0xEF]))


class PumpModeTest(unittest.TestCase):
    """The trap: these codes are not an ordered scale."""

    def test_pump_codes_are_not_in_speed_order(self):
        self.assertEqual(lpp.PUMP_MODES,
                         {"low": 2, "medium": 3, "high": 0, "max": 1})

    def test_speed_order_is_not_code_order(self):
        # If someone "fixes" the table into 0,1,2,3 this fails -- which is the
        # entire point of the test.
        codes = [lpp.PUMP_MODES[name] for name in lpp.PUMP_ORDER]
        self.assertNotEqual(codes, sorted(codes),
                            "pump codes are ordered -- the documented hardware "
                            "quirk has been silently 'corrected'")

    def test_frame_carries_the_device_code_not_the_ordinal(self):
        # 'low' is ordinal 0 in PUMP_ORDER but device code 2.
        self.assertEqual(lpp.pump_frame("low")[4], 2)
        self.assertEqual(lpp.pump_frame("high")[4], 0)

    def test_names_and_codes_agree(self):
        for name, code in lpp.PUMP_MODES.items():
            self.assertEqual(lpp.pump_frame(name), lpp.pump_frame(code))
            self.assertEqual(lpp.PUMP_MODES_REV[code], name)

    def test_unknown_mode_and_code_are_rejected(self):
        with self.assertRaises(ValueError):
            lpp.pump_frame("turbo")
        with self.assertRaises(ValueError):
            lpp.pump_frame(9)

    def test_pump_order_covers_every_mode(self):
        self.assertEqual(set(lpp.PUMP_ORDER), set(lpp.PUMP_MODES))


class FanFrameTest(unittest.TestCase):

    def test_duty_is_carried_verbatim(self):
        for duty in (0, 1, 50, 99, 100):
            self.assertEqual(lpp.fan_frame(duty)[3], duty)

    def test_out_of_range_is_rejected(self):
        # The dock accepts nonsense silently, so validation has to be ours.
        for bad in (-1, 101, 255):
            with self.assertRaises(ValueError, msg=f"{bad} accepted"):
                lpp.fan_frame(bad)


class InitSequenceTest(unittest.TestCase):

    def test_trailer_is_the_literal_bytes_sw(self):
        # Not a framed command; sent verbatim, and exactly once.
        self.assertEqual(lpp.INIT_TRAILER, b"sw")

    def test_init_sequence_is_four_frames(self):
        self.assertEqual(len(lpp.INIT_SEQUENCE), 4)

    def test_init_starts_the_pump_at_medium(self):
        self.assertEqual(lpp.INIT_SEQUENCE[0][4], lpp.PUMP_MODES["medium"])


if __name__ == "__main__":
    unittest.main()
