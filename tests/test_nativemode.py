# SPDX-License-Identifier: MIT
"""The machine's own performance modes.

A preset is Custom with numbers we chose; a native mode hands control back to
the EC. The distinction is the whole point of the module, and the thing that
makes it work is clearing the custom latch -- with the latch armed the EC
treats the machine as Custom whatever 0x0751 says, which is why every preset
in this project has produced a white LED and ignored the EC's own limits.

Two properties are worth more than the rest.

Ordering. set_power_limit is silently ignored with the latch clear, so the
limits must be zeroed BEFORE the latch comes down. Zeroing afterwards leaves a
previous preset's numbers in 0x0783-0x0785 where they will be read back and
reported as what the machine is doing.

And Office must stay shut. Its encoding sets 0x0751 bit 7, the bit that
stopped the fans on this machine with the tables empty.
"""

import os
import unittest

from hydroc import fancurve as fc
from hydroc import nativemode as _nm

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "hydroc", "nativemode.py")


def load():
    return _nm


class FakeEC:
    """Records the order of everything, because order is the bug risk."""

    def __init__(self, regs=None, double_pl4=False):
        self.regs = dict(regs or {})
        self.log = []
        self._double = double_pl4
        self.latched = True

    def read(self, addr):
        return self.regs.get(addr, 0)

    def write_verify(self, addr, value):
        self.regs[addr] = value
        self.log.append(("write", addr, value))

    def set_power_limit(self, which, watts):
        self.log.append(("pl", which, watts))

    def set_custom_profile(self, enable):
        self.latched = enable
        self.log.append(("latch", enable, None))

    def custom_profile_enabled(self):
        return self.latched

    def has_double_pl4(self):
        return self._double


class DecodeTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_the_three_encodings(self):
        for raw, name in ((0x00, "balanced"), (0x10, "beast"), (0xA0, "office")):
            self.assertEqual(self.m.decode(raw), name)

    def test_boost_does_not_change_the_mode(self):
        """Fan boost ORs 0x40 into any mode; it is not a mode of its own."""
        self.assertEqual(self.m.decode(0x00 | 0x40), "balanced")
        self.assertEqual(self.m.decode(0x10 | 0x40), "beast")

    def test_an_unrecognised_encoding_is_none_not_a_guess(self):
        self.assertIsNone(self.m.decode(0x37))
        self.assertIsNone(self.m.decode(0x20))

    def test_current_reports_the_latch_because_it_overrides_the_mode(self):
        """With the latch armed the EC is in Custom whatever 0x0751 holds, so
        a mode name without the latch state is misleading."""
        ec = FakeEC({0x0751: 0x10})
        ec.latched = True
        st = self.m.current(ec)
        self.assertEqual(st["mode"], "beast")
        self.assertTrue(st["custom_latched"])


class ApplyOrderTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_limits_are_zeroed_before_the_latch_comes_down(self):
        ec = FakeEC()
        self.m.apply(ec, "balanced", curve=False)
        kinds = [e[0] for e in ec.log]
        self.assertLess(kinds.index("pl"), kinds.index("latch"),
                        "power limits must be zeroed while the latch still "
                        "accepts writes, or stale preset values persist")

    def test_all_three_limits_are_zeroed(self):
        ec = FakeEC()
        self.m.apply(ec, "beast", curve=False)
        self.assertEqual([(e[1], e[2]) for e in ec.log if e[0] == "pl"],
                         [("pl1", 0), ("pl2", 0), ("pl4", 0)])

    def test_the_latch_is_cleared_not_armed(self):
        ec = FakeEC()
        self.m.apply(ec, "balanced", curve=False)
        self.assertIn(("latch", False, None), ec.log)
        self.assertFalse(ec.latched)

    def test_the_mode_register_is_written_last(self):
        ec = FakeEC()
        self.m.apply(ec, "beast", curve=False)
        self.assertEqual(ec.log[-1], ("write", 0x0751, 0x10))

    def test_balanced_writes_zero_not_something_truthy(self):
        """0x00 is a real encoding, and a falsy one. A writer that skips
        zero would silently leave the previous mode selected."""
        ec = FakeEC({0x0751: 0x10})
        self.m.apply(ec, "balanced", curve=False)
        self.assertEqual(ec.regs[0x0751], 0x00)

    def test_boost_ors_into_the_mode(self):
        ec = FakeEC()
        self.m.apply(ec, "beast", boost=True, curve=False)
        self.assertEqual(ec.regs[0x0751], 0x10 | 0x40)


class OfficeIsShutTest(unittest.TestCase):

    def setUp(self):
        self.m = load()

    def test_office_is_refused_by_default(self):
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError) as cm:
            self.m.apply(ec, "office", curve=False)
        self.assertIn("bit 7", str(cm.exception))

    def test_refusing_office_touches_no_register(self):
        """A refusal that has already zeroed the limits is not a refusal."""
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError):
            self.m.apply(ec, "office", curve=False)
        self.assertEqual(ec.log, [])

    def test_office_is_reachable_deliberately(self):
        ec = FakeEC()
        self.m.apply(ec, "office", allow_fan_user_bit=True, curve=False)
        self.assertEqual(ec.regs[0x0751], 0xA0)

    def test_office_is_in_the_cycle_now_that_its_guard_is_checked(self):
        """It was withheld while the guard was a promise. It is checked."""
        self.assertEqual(self.m.CYCLE, ["office", "balanced", "beast"])

    def test_an_unknown_mode_is_refused(self):
        ec = FakeEC()
        with self.assertRaises(self.m.NativeModeError):
            self.m.apply(ec, "turbo", curve=False)
        self.assertEqual(ec.log, [])


class EcLimitsTest(unittest.TestCase):
    """Read-only. These are the firmware's numbers, not ours to write."""

    def setUp(self):
        self.m = load()

    def test_it_reads_the_registers_measured_on_the_hardware(self):
        ec = FakeEC({0x07A7: 205, 0x07A8: 205, 0x07A9: 200, 0x07DA: 5})
        self.assertEqual(self.m.ec_limits(ec, "beast"),
                         {"pl1": 205, "pl2": 205, "pl4": 200, "tcc": 5})

    def test_pl4_is_doubled_where_the_register_is_half_scale(self):
        ec = FakeEC({0x0730: 75, 0x0731: 75, 0x0732: 125, 0x07D8: 5},
                    double_pl4=True)
        self.assertEqual(self.m.ec_limits(ec, "balanced")["pl4"], 250)

    def test_it_never_writes(self):
        ec = FakeEC({0x0730: 75})
        self.m.ec_limits(ec, "balanced")
        self.assertEqual(ec.log, [])

    def test_every_shipped_curve_is_valid(self):
        """They are the vendor's, read off the hardware -- but one Office GPU
        point was malformed there, so they are checked rather than trusted."""
        for mode, fans in self.m.CURVES.items():
            for fan, curve in fans.items():
                fc.validate(curve, f"{mode} {fan}")

    def test_the_curves_are_the_measured_ones(self):
        """Spot values from ec-mode-*.json, so a careless edit shows up."""
        self.assertEqual(self.m.CURVES["office"]["cpu"][1], [57, 48, 30])
        self.assertEqual(self.m.CURVES["beast"]["cpu"][3], [65, 61, 40])
        self.assertEqual(self.m.CURVES["office"]["gpu"][3], [57, 56, 30],
                         "the normalised point: vendor had down=58 above "
                         "up=57")


if __name__ == "__main__":
    unittest.main()


class ReconcileTest(unittest.TestCase):
    """The daemon must not fight a native mode.

    This is the failure the integration exists to prevent: set Beast, and on
    the next reconcile pass the stored `custom_profile: True` re-arms the
    latch. The EC is back in Custom -- white LED, our numbers, its own limits
    unused -- and nothing in the UI says why the mode did not stick. It would
    look like the hardware rejecting the write.

    So a desired native mode drops the keys it owns before they are compared.
    """

    def setUp(self):
        from hydroc import hardware
        self.hw = hardware

    def test_a_native_mode_drops_the_custom_keys(self):
        for k in ("custom_profile", "cpu_pl1", "cpu_pl2", "cpu_pl4",
                  "cpu_power_limit"):
            self.assertIn(k, self.hw.NATIVE_OWNED,
                          f"{k} would be reconciled against a native mode and "
                          f"re-arm the latch")

    def test_the_fan_keys_are_owned_too(self):
        """A native mode writes its own tables, so a profile still holding a
        custom curve disagrees with the hardware forever. Missing these is
        what kept the drift banner up."""
        for k in ("fan_mode", "fan_curve_cpu", "fan_curve_gpu"):
            self.assertIn(k, self.hw.NATIVE_OWNED)

    def test_apply_strips_exactly_that_list(self):
        src = open(self.hw.__file__, encoding="utf-8").read()
        block = src.split("want_native = desired.get")[1].split("# Guard the")[0]
        self.assertIn("for k in NATIVE_OWNED", block)

    def test_drift_strips_it_too(self):
        """The two must agree: apply() refusing to act on a key while
        drift() reports it is a banner no button can clear."""
        import inspect
        src = inspect.getsource(self.hw.Hardware.drift)
        self.assertIn("NATIVE_OWNED", src)
        self.assertIn('desired.get("native_mode")', src)

    def test_the_native_step_runs_before_the_latch_step(self):
        src = open(self.hw.__file__, encoding="utf-8").read()
        self.assertLess(src.index("want_native = desired.get"),
                        src.index('if differs("custom_profile")'),
                        "the native mode must be applied before the latch "
                        "reconciliation it replaces")

    def test_state_reports_no_mode_while_the_latch_is_armed(self):
        """With the latch armed the EC is in Custom whatever 0x0751 holds.
        Reporting a mode name there would be a lie the UI repeats."""
        src = open(self.hw.__file__, encoding="utf-8").read()
        self.assertIn('None if nm["custom_latched"] else nm["mode"]', src)

    def test_the_profile_default_is_none_not_a_mode(self):
        """Defaulting to a native mode would change every existing install's
        behaviour on upgrade."""
        from hydroc import cli
        self.assertIn("native_mode", cli.DEFAULT_PROFILE)
        self.assertIsNone(cli.DEFAULT_PROFILE["native_mode"])


class ButtonCycleTest(unittest.TestCase):
    """What the physical button does.

    It now walks the machine's own modes, which is what the same button does
    under Windows -- and the LED follows, which it never could before. Every
    preset arms the custom latch, and the EC drives the LED white for the
    whole time that bit is up, so no preset cycle could ever change it.
    """

    def setUp(self):
        self.m = load()

    def test_the_cycle_is_the_vendor_order(self):
        self.assertEqual(self.m.CYCLE, ["office", "balanced", "beast"])

    def test_it_advances_and_wraps(self):
        self.assertEqual(self.m.next_in_cycle("office"), "balanced")
        self.assertEqual(self.m.next_in_cycle("balanced"), "beast")
        self.assertEqual(self.m.next_in_cycle("beast"), "office")

    def test_anything_unrecognised_starts_over(self):
        """A press after the EC reverted at power-on, or while a custom
        preset is active, has to land somewhere defined."""
        for start in (None, "custom", "performance", ""):
            self.assertEqual(self.m.next_in_cycle(start), "office")

    def test_describe_is_in_cycle_order(self):
        self.assertEqual([d["id"] for d in self.m.describe()], self.m.CYCLE)


class PresetExclusionTest(unittest.TestCase):
    """A preset and a native mode cannot both be the answer.

    Applying a preset arms the latch, which takes the machine out of native
    mode immediately -- but the SAVED profile would still carry the native
    name, and on the next boot apply() strips the custom keys and puts the
    machine back where the user just left. The stale value has to be cleared
    at the point the preset is chosen.
    """

    def test_applying_a_preset_clears_the_saved_native_mode(self):
        from hydroc import server
        src = open(server.__file__, encoding="utf-8").read()
        body = src.split("def apply_preset")[1].split("\ndef ")[0]
        self.assertIn('profile["native_mode"] = None', body)

    def test_applying_a_native_mode_saves_it(self):
        from hydroc import server
        src = open(server.__file__, encoding="utf-8").read()
        body = src.split("def apply_native")[1].split("\ndef ")[0]
        self.assertIn('profile["native_mode"] = name', body)

    def test_the_button_default_is_native(self):
        from hydroc import cli
        self.assertEqual(cli.DEFAULT_PROFILE["button_cycle"], "native")

    def test_the_old_behaviour_is_still_reachable(self):
        from hydroc import server
        src = open(server.__file__, encoding="utf-8").read()
        self.assertIn('button_cycle") == "presets"', src)
