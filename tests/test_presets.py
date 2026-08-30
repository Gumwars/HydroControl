# SPDX-License-Identifier: MIT
"""Presets, and the invariant that keeps them applicable.

Presets are ours to define -- no shipped Windows config defines per-mode power
limits (DESIGN.md §3.7). But a preset whose PL4 is odd can never be matched once
applied, because the register stores PL4 at half scale and reads back the even
value. match() would then report 'custom' immediately after a successful apply.
"""

import unittest

from hydroc import presets


class PresetInvariantTest(unittest.TestCase):

    def test_every_preset_pl4_is_even(self):
        # The register cannot express odd watts; an odd preset is unmatchable.
        for key, preset in presets.PRESETS.items():
            pl4 = preset["settings"].get("cpu_pl4")
            if pl4:
                self.assertEqual(pl4 % 2, 0,
                                 f"preset {key!r} has odd cpu_pl4={pl4}, which "
                                 "the hardware cannot hold")

    def test_every_preset_arms_the_custom_profile_latch(self):
        # Without 0x0727 bit 6 the power-limit writes are silently ignored.
        for key, preset in presets.PRESETS.items():
            self.assertTrue(preset["settings"].get("custom_profile"),
                            f"preset {key!r} does not arm the latch")

    def test_cycle_covers_every_preset(self):
        self.assertEqual(set(presets.CYCLE), set(presets.PRESETS))


class MatchTest(unittest.TestCase):

    def test_exact_state_matches_its_preset(self):
        for key, preset in presets.PRESETS.items():
            self.assertEqual(presets.match(dict(preset["settings"])), key)

    def test_extra_state_keys_do_not_prevent_a_match(self):
        state = dict(presets.PRESETS["office"]["settings"],
                     charge_threshold=80, fn_lock=False)
        self.assertEqual(presets.match(state), "office")

    def test_one_differing_value_is_custom(self):
        state = dict(presets.PRESETS["office"]["settings"])
        state["cpu_pl1"] = state["cpu_pl1"] + 1
        self.assertEqual(presets.match(state), presets.CUSTOM)

    def test_partially_applied_preset_is_custom_not_a_lie(self):
        # A preset that half-applied must report honestly.
        state = dict(presets.PRESETS["performance"]["settings"])
        del state["cpu_pl4"]
        self.assertEqual(presets.match(state), presets.CUSTOM)

    def test_empty_state_is_custom(self):
        self.assertEqual(presets.match({}), presets.CUSTOM)


class CycleTest(unittest.TestCase):

    def test_cycle_advances_and_wraps(self):
        seen = [presets.CYCLE[0]]
        for _ in range(len(presets.CYCLE) - 1):
            seen.append(presets.next_in_cycle(seen[-1]))
        self.assertEqual(seen, list(presets.CYCLE))
        self.assertEqual(presets.next_in_cycle(seen[-1]), presets.CYCLE[0])

    def test_unrecognised_state_restarts_the_cycle(self):
        # 'custom' is what match() returns when the EC reverted at power-on.
        self.assertEqual(presets.next_in_cycle(presets.CUSTOM), presets.CYCLE[0])
        self.assertEqual(presets.next_in_cycle("nonsense"), presets.CYCLE[0])

    def test_settings_for_returns_a_copy(self):
        a = presets.settings_for("office")
        a["cpu_pl1"] = 999
        self.assertNotEqual(presets.PRESETS["office"]["settings"]["cpu_pl1"], 999)

    def test_settings_for_unknown_is_none(self):
        self.assertIsNone(presets.settings_for("nope"))


if __name__ == "__main__":
    unittest.main()
