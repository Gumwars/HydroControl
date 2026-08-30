# SPDX-License-Identifier: MIT
"""Renamed settings keys.

A profile written before a rename keeps the old key. Nothing writes the old key
back, so drift() reports a difference no action can resolve -- permanent phantom
drift, the same failure mode as an unquantised PL4. The rule recorded in HANDOFF
is: add renames to LEGACY_KEYS, never alias them in read_state.
"""

import unittest

from hydroc.cli import LEGACY_KEYS, migrate_profile


class MigrateProfileTest(unittest.TestCase):

    def test_old_key_becomes_the_new_one(self):
        profile, changed = migrate_profile({"cpu_power_limit": 45})
        self.assertTrue(changed)
        self.assertEqual(profile["cpu_pl1"], 45)
        self.assertNotIn("cpu_power_limit", profile)

    def test_old_key_is_always_removed(self):
        # Left behind, it is re-migrated every load and never settles.
        profile, _ = migrate_profile({"cpu_power_limit": 45, "cpu_pl1": 35})
        self.assertNotIn("cpu_power_limit", profile)

    def test_new_key_wins_when_both_are_present(self):
        profile, changed = migrate_profile({"cpu_power_limit": 45, "cpu_pl1": 35})
        self.assertEqual(profile["cpu_pl1"], 35)
        self.assertTrue(changed)

    def test_untouched_profile_reports_no_change(self):
        # `changed` gates a rewrite of the file; a false positive rewrites on
        # every single load.
        profile, changed = migrate_profile({"cpu_pl1": 35})
        self.assertFalse(changed)
        self.assertEqual(profile, {"cpu_pl1": 35})

    def test_none_valued_old_key_does_not_overwrite(self):
        profile, changed = migrate_profile({"cpu_power_limit": None})
        self.assertTrue(changed)
        self.assertNotIn("cpu_power_limit", profile)
        self.assertNotIn("cpu_pl1", profile)

    def test_migration_is_idempotent(self):
        once, _ = migrate_profile({"cpu_power_limit": 45})
        twice, changed = migrate_profile(dict(once))
        self.assertEqual(once, twice)
        self.assertFalse(changed)

    def test_every_legacy_mapping_migrates(self):
        # Guards a future rename being added to the table but not handled.
        for old, new in LEGACY_KEYS.items():
            profile, changed = migrate_profile({old: 1})
            self.assertTrue(changed, f"{old} not migrated")
            self.assertNotIn(old, profile)
            self.assertEqual(profile.get(new), 1)


if __name__ == "__main__":
    unittest.main()
