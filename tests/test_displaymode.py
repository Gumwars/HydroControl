# SPDX-License-Identifier: MIT
"""Display colour modes as a colour matrix, and the Wayland client that holds it.

The rules worth pinning: Standard is exactly identity (anything else is a tint
the user never chose, on top of a factory calibration); no matrix entry is
ever negative (hyprland_ctm_control_v1 answers that with a protocol error that
kills the connection); a temperature shift never pushes a channel above 1, so
it cannot clip; and the Windows presets keep their meaning.
"""

import struct
import unittest

from hydroc import displayd, displaymode as dm


class MatrixTest(unittest.TestCase):

    def test_standard_is_exactly_identity(self):
        self.assertEqual(dm.matrix(dm.MODES["standard"]["params"]), dm.IDENTITY)

    def test_6500k_is_identity(self):
        self.assertEqual(dm.temperature_gains(6500), (1.0, 1.0, 1.0))

    def test_no_entry_is_ever_negative(self):
        for k in range(2500, 10001, 500):
            for v in (0, 64, 128, 255):
                m = dm.matrix({"temperature": k, "red": v, "green": v, "blue": v,
                               "brightness": 50})
                self.assertTrue(all(x >= 0 for x in m), (k, v, m))

    def test_temperature_never_brightens(self):
        for k in range(2500, 10001, 250):
            g = dm.temperature_gains(k)
            self.assertLessEqual(max(g), 1.0 + 1e-9)
            self.assertAlmostEqual(max(g), 1.0)

    def test_warm_cuts_blue_cool_cuts_red(self):
        warm, cool = dm.temperature_gains(3400), dm.temperature_gains(9000)
        self.assertLess(warm[2], warm[1] + 1e-9)
        self.assertLess(cool[0], cool[2])

    def test_read_is_the_half_scale_blue_cut(self):
        m = dm.matrix(dm.MODES["read"]["params"])
        self.assertEqual((m[0], m[4]), (1.0, 1.0))
        self.assertAlmostEqual(m[8], 0.5)

    def test_out_of_range_is_clamped_not_rejected(self):
        p = dm.clamp_params({"brightness": 400, "blue": -5, "temperature": "x"})
        self.assertEqual((p["brightness"], p["blue"], p["temperature"]),
                         (110, 0, 6500))

    def test_saved_state_with_unknown_mode_heals(self):
        st = dm.normalise_state({"mode": "vivid", "modes": {"vivid": {}, "read": {"blue": 30}}})
        self.assertEqual(st["mode"], "standard")
        self.assertEqual(set(st["modes"]), set(dm.MODES))
        self.assertEqual(st["modes"]["read"]["blue"], 30)


class WireTest(unittest.TestCase):

    def test_fixed_is_24_8(self):
        self.assertEqual(displayd.fixed(1.0), 256)
        self.assertEqual(displayd.fixed(0.5), 128)

    def test_string_is_length_prefixed_nul_terminated_padded(self):
        b = displayd.wl_string("wl_output")
        self.assertEqual(struct.unpack_from("<I", b)[0], 10)
        self.assertEqual(len(b) % 4, 0)
        self.assertEqual(displayd.read_string(b), ("wl_output", len(b)))

    def test_messages_split_and_keep_the_partial_tail(self):
        a = displayd.message(5, 1, b"\x01\x00\x00\x00")
        b = displayd.message(6, 0)
        msgs, rest = displayd.parse_messages(a + b + b[:5])
        self.assertEqual([(o, op) for o, op, _ in msgs], [(5, 1), (6, 0)])
        self.assertEqual(rest, b[:5])


class FakeClient:
    blocked = False

    def __init__(self):
        self.sent = []
        self.on_outputs_changed = None

    def set_ctm(self, targets):
        self.sent.append(targets)
        return list(targets)


class ServiceTest(unittest.TestCase):

    def make(self):
        saved = []
        svc = displayd.Service(FakeClient(), dm.new_state(), save=saved.append)
        svc.panel = lambda: "eDP-1"
        return svc, saved

    def test_set_applies_to_the_panel_and_saves(self):
        svc, saved = self.make()
        r = svc.handle({"op": "set", "mode": "read"})
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(svc.client.sent[-1]["eDP-1"][8], 0.5)
        self.assertEqual(saved[-1]["mode"], "read")

    def test_editing_a_mode_marks_it_and_reset_restores(self):
        svc, _ = self.make()
        svc.handle({"op": "set", "mode": "custom", "params": {"temperature": 4000}})
        custom = next(m for m in svc.status()["modes"] if m["id"] == "custom")
        self.assertTrue(custom["edited"])
        svc.handle({"op": "set", "mode": "custom", "reset": True})
        custom = next(m for m in svc.status()["modes"] if m["id"] == "custom")
        self.assertFalse(custom["edited"])

    def test_unknown_mode_is_refused_without_applying(self):
        svc, saved = self.make()
        r = svc.handle({"op": "set", "mode": "vivid"})
        self.assertFalse(r["ok"])
        self.assertEqual((svc.client.sent, saved), ([], []))

    def test_blocked_is_reported_as_failure(self):
        svc, _ = self.make()
        svc.client.blocked = True
        r = svc.handle({"op": "set", "mode": "read"})
        self.assertFalse(r["ok"])
        self.assertIn("another program", r["error"])


if __name__ == "__main__":
    unittest.main()
