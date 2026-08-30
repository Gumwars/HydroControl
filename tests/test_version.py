# SPDX-License-Identifier: MIT
"""Build identification.

A tester report is only actionable if it names the code that produced it. Two
situations must both work: a git checkout (development) and an unpacked bundle
(a tester, with no git available). Neither may raise, and an unknown build must
be reported as unknown rather than guessed at.
"""

import json
import os
import tempfile
import unittest
from unittest import mock

from hydroc import version


class StampTest(unittest.TestCase):
    """A bundle has no git, so the stamp is the only source."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stamp = os.path.join(self.tmp.name, "build.json")
        p = mock.patch.object(version, "STAMP", self.stamp)
        p.start()
        self.addCleanup(p.stop)

    def _write(self, data):
        with open(self.stamp, "w") as fh:
            json.dump(data, fh)

    def test_stamp_is_used_when_present(self):
        self._write({"commit": "abc123def456", "dirty": False,
                     "date": "2026-08-30", "source": "bundle"})
        self.assertEqual(version.build_info()["commit"], "abc123def456")
        self.assertIn("abc123def456", version.build_id())

    def test_stamp_wins_over_git(self):
        # In a bundle unpacked inside some other repo, the stamp is the truth.
        self._write({"commit": "stamped00000", "source": "bundle"})
        self.assertEqual(version.build_info()["commit"], "stamped00000")

    def test_dirty_is_surfaced(self):
        self._write({"commit": "abc123def456", "dirty": True})
        self.assertIn("-dirty", version.build_id())

    def test_clean_build_is_not_marked_dirty(self):
        self._write({"commit": "abc123def456", "dirty": False})
        self.assertNotIn("dirty", version.build_id())

    def test_malformed_stamp_falls_through(self):
        with open(self.stamp, "w") as fh:
            fh.write("{not json")
        self.assertIsNone(version._from_stamp())

    def test_stamp_without_a_commit_is_not_a_stamp(self):
        self._write({"date": "2026-08-30"})
        self.assertIsNone(version._from_stamp())


class NoSourceTest(unittest.TestCase):

    def test_unknown_is_reported_not_guessed(self):
        with mock.patch.object(version, "_from_stamp", return_value=None), \
             mock.patch.object(version, "_from_git", return_value=None):
            info = version.build_info()
            self.assertEqual(info["commit"], "unknown")
            self.assertIn("unknown build", version.build_id())

    def test_missing_git_binary_degrades_to_unknown(self):
        # The real failure mode: git is not installed. _git guards this, so
        # patch at the subprocess boundary rather than replacing the guard.
        with mock.patch.object(version, "_from_stamp", return_value=None), \
             mock.patch("subprocess.run", side_effect=OSError("no git")):
            self.assertEqual(version.build_info()["commit"], "unknown")

    def test_build_info_never_raises_even_if_a_reader_explodes(self):
        # doctor() calls this first; it must not take the health check down.
        with mock.patch.object(version, "_from_stamp",
                               side_effect=RuntimeError("boom")):
            self.assertIsInstance(version.build_info(), dict)
            self.assertEqual(version.build_info()["commit"], "unknown")


class CheckoutTest(unittest.TestCase):

    def test_this_checkout_identifies_itself(self):
        info = version.build_info()
        self.assertIn(info["source"], ("git", "bundle"))
        self.assertNotEqual(info["commit"], "unknown")

    def test_build_id_is_a_single_line(self):
        self.assertNotIn("\n", version.build_id())


if __name__ == "__main__":
    unittest.main()
