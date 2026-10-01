# SPDX-License-Identifier: MIT
"""Reactive keyboard effects, and the EC bit they need.

kbctrl has accepted a `reactive` flag since it was written and puts it in the
keyboard packet. Two things were missing and neither was visible from the UI:
`hydroc.rgb.apply_effect` did not pass the flag down at all, and nothing in
either package ever wrote EC 0x0741 bit 3 -- ITE_KBD_EFFECT_REACTIVE, the bit
that has the EC forward key presses to the keyboard controller.

So the option existed, could be ticked, and did nothing. Control Center sets
that bit for exactly four effects and clears it for the rest; all four Windows
mode dumps read 0x0741 = 0x81, bit 3 clear.

The register is shared with ENABLE_MANUAL_CTRL at bit 0, the master switch the
kernel driver owns. Every write here is read-modify-write for that reason --
clobbering bit 0 would stop the EC accepting host control at all, which is a
far worse failure than a keyboard effect not reacting.
"""

import os
import unittest
from unittest import mock

from hydroc import rgb


class FakeKeyboard:
    def __init__(self, fail=False):
        self.calls = []
        self.per_key = []
        self.fail = fail

    def set_effect(self, name, **kw):
        if self.fail:
            raise RuntimeError("device busy")
        self.calls.append((name, kw))

    def apply_per_key(self, mapped, **kw):
        if self.fail:
            raise RuntimeError("device busy")
        self.per_key.append((mapped, kw))


class ReactiveTest(unittest.TestCase):

    def run_effect(self, name, reactive, fail=False):
        kb = FakeKeyboard(fail=fail)
        bits = []
        with mock.patch.object(rgb, "_keyboard", return_value=kb), \
             mock.patch.object(rgb, "_set_reactive_bit",
                               side_effect=lambda on: bits.append(on)):
            res = rgb.apply_effect(name, reactive=reactive)
        return res, kb, bits

    def test_a_reactive_effect_sets_the_bit(self):
        res, kb, bits = self.run_effect("ripple", True)
        self.assertTrue(res["reactive"])
        self.assertEqual(bits, [True])
        self.assertTrue(kb.calls[0][1]["reactive"])

    def test_a_non_reactive_effect_clears_the_bit(self):
        """Control Center clears it too. Leaving it set forwards key presses
        to a keyboard with no use for them."""
        res, _kb, bits = self.run_effect("rainbow", False)
        self.assertFalse(res["reactive"])
        self.assertEqual(bits, [False])

    def test_asking_for_reactive_on_an_effect_without_it_is_not_silent(self):
        res, kb, bits = self.run_effect("rainbow", True)
        self.assertFalse(res["reactive"])
        self.assertEqual(bits, [False])
        self.assertIn("no reactive mode", res["note"])
        self.assertFalse(kb.calls[0][1]["reactive"])

    def test_all_four_vendor_effects_are_covered(self):
        self.assertEqual(rgb.REACTIVE_EFFECTS,
                         {"random", "ripple", "aurora", "fireworks"})

    def test_a_failed_effect_leaves_the_ec_alone(self):
        """Arming key forwarding for a pattern that is not running is worse
        than doing nothing."""
        res, _kb, bits = self.run_effect("ripple", True, fail=True)
        self.assertFalse(res["ok"])
        self.assertEqual(bits, [])

    def test_the_flag_reaches_the_keyboard_packet(self):
        """The half that was missing before the EC bit: apply_effect did not
        pass `reactive` down, so kbctrl never saw it either."""
        _res, kb, _bits = self.run_effect("aurora", True)
        self.assertIn("reactive", kb.calls[0][1])


class RegisterSafetyTest(unittest.TestCase):

    def test_it_is_a_read_modify_write_on_the_right_bit(self):
        writes = []

        class FakeEC:
            _lock = None

            def update_bits(self, addr, mask, value):
                writes.append((addr, mask, value))

        import hydroc.ec as ec_mod
        with mock.patch.object(ec_mod, "EC", FakeEC):
            rgb._set_reactive_bit(True)
            rgb._set_reactive_bit(False)
        self.assertEqual(writes, [(0x0741, 0x08, 0x08), (0x0741, 0x08, 0x00)])

    def test_it_never_writes_the_whole_register(self):
        """Bit 0 is ENABLE_MANUAL_CTRL and the kernel driver owns it."""
        import inspect
        src = inspect.getsource(rgb._set_reactive_bit)
        self.assertIn("update_bits", src)
        self.assertNotIn(".write(", src)

    def test_an_unavailable_ec_is_reported_not_raised(self):
        with mock.patch.object(rgb, "_keyboard", return_value=FakeKeyboard()), \
             mock.patch.object(rgb, "_set_reactive_bit", return_value="no acpi_call"):
            res = rgb.apply_effect("ripple", reactive=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["reactive_bit_error"], "no acpi_call")


if __name__ == "__main__":
    unittest.main()


class ColourCorrectionTest(unittest.TestCase):
    """Control Center's white balance for the keyboard panel.

    Our white goes out as FF FF FF and reads pink; Windows sends 7D FF B9.
    The owner reported the colours looking wrong before this table was found,
    which is the evidence that it applies to this panel -- the device-type
    query the vendor uses to select it is an 80h HID get-feature, and this
    keyboard is reached over pyusb, so that query is not available here
    without guessing at the control transfer.

    It is an exact-match lookup and cannot be extrapolated. White scales red
    to 0.49; orange leaves red at 1.00. The entries are tuned per colour for
    appearance rather than being a colorimetric transform, so anything not in
    the table has to pass through untouched -- which is what Windows does.
    """

    def test_white_is_the_one_that_matters(self):
        self.assertEqual(rgb.correct_rgb((0xFF, 0xFF, 0xFF)),
                         (0x7D, 0xFF, 0xB9))

    def test_orange(self):
        self.assertEqual(rgb.correct_rgb((0xFF, 0xA5, 0x00)),
                         (0xFF, 0x7D, 0x00))

    def test_red_is_unchanged_and_needs_no_entry(self):
        self.assertEqual(rgb.correct_rgb((0xFF, 0x00, 0x00)),
                         (0xFF, 0x00, 0x00))

    def test_an_arbitrary_colour_passes_through(self):
        """Near-misses are not corrected. #FEFEFE is not white to this table,
        and inventing a tolerance would be our behaviour, not the vendor's."""
        for c in ((0xFE, 0xFE, 0xFE), (0x12, 0x34, 0x56), (0, 0, 0)):
            self.assertEqual(rgb.correct_rgb(c), c)

    def test_yellow_is_absent_rather_than_guessed(self):
        """Its blue byte was not recovered from the service. A guess would
        make yellow wrong in a new way while looking authoritative."""
        self.assertNotIn((0xFF, 0xFF, 0x00), rgb.CHEAT_RGB)

    def test_the_table_is_not_a_uniform_transform(self):
        """Pinned because the obvious 'improvement' is to derive channel
        gains from the white point and apply them everywhere. The entries
        contradict that, so doing it would diverge from the hardware."""
        w_in, w_out = (0xFF, 0xFF, 0xFF), rgb.CHEAT_RGB[(0xFF, 0xFF, 0xFF)]
        o_in, o_out = (0xFF, 0xA5, 0x00), rgb.CHEAT_RGB[(0xFF, 0xA5, 0x00)]
        self.assertNotAlmostEqual(w_out[0] / w_in[0], o_out[0] / o_in[0],
                                  places=2)

    def test_the_library_default_is_off_and_the_caller_decides(self):
        """Off in the signature is not a verdict on the panel -- the daemon
        passes the user's toggle. It is off here so that a caller who has not
        thought about it sends what it was handed."""
        import inspect
        sig = inspect.signature(rgb.apply_per_key)
        self.assertFalse(sig.parameters["correct"].default)

    def test_correction_reaches_the_device_when_asked_for(self):
        """This used to call with correct=False and assert True, so the one
        path that matters -- a corrected colour arriving at the hardware --
        was never covered. The flag then shipped unwired for a day."""
        kb = FakeKeyboard()
        with mock.patch.object(rgb, "_keyboard", return_value=kb), \
             mock.patch.object(rgb, "key_id_to_matrix", return_value=(0, 0)):
            rgb.apply_per_key({"m3_7": "#FFFFFF"}, correct=True)
        self.assertEqual(kb.per_key[0][0], {(0, 0): (0x7D, 0xFF, 0xB9)})

    def test_no_correction_sends_the_colour_as_picked(self):
        kb = FakeKeyboard()
        with mock.patch.object(rgb, "_keyboard", return_value=kb), \
             mock.patch.object(rgb, "key_id_to_matrix", return_value=(0, 0)):
            rgb.apply_per_key({"m3_7": "#FFFFFF"}, correct=False)
        self.assertEqual(kb.per_key[0][0], {(0, 0): (0xFF, 0xFF, 0xFF)})

    def test_the_chin_bar_is_not_corrected(self):
        """A different device type. The doc is explicit: raw RGB."""
        import inspect
        src = inspect.getsource(rgb.chinbar)
        self.assertNotIn("correct_rgb", src)


class UIWiringTest(unittest.TestCase):
    """The flag has to leave the browser, not just exist in the server.

    `correct` was added to apply_per_key and to the /api/rgb/perkey route,
    and the UI never sent the key. `payload.get("correct", False)` then made
    every request uncorrected, so the feature read as "tried and rejected"
    when it had never run once. Both ends, or neither.
    """

    @staticmethod
    def _ui():
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "hydroc", "ui", "index.html"),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_the_perkey_request_carries_the_flag(self):
        ui = self._ui()
        i = ui.index("/api/rgb/perkey")
        body = ui[i:i + 400]
        self.assertIn("correct:S.kbCorrect", body,
                      "the per-key POST does not send the correction flag")

    def test_the_toggle_exists_and_has_a_handler(self):
        ui = self._ui()
        self.assertIn('id="kbcorrect"', ui)
        self.assertIn("S.kbCorrect=!S.kbCorrect", ui)

    def test_it_defaults_on(self):
        """Measured on the hardware: striped 7D FF B9 against FF FF FF in one
        request, and the corrected rows read visibly less pink."""
        self.assertIn("kbCorrect:true", self._ui())

    def test_the_toggle_says_what_it_does_not_cover(self):
        """Two entries. A user who corrects white and then picks #F0F0F0 gets
        pink back, and the label is the only place that is visible."""
        ui = self._ui()
        i = ui.index('id="kbcorrect"')
        self.assertIn("white and orange only", ui[i:i + 600])
