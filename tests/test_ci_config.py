# SPDX-License-Identifier: MIT
"""CI has to keep testing what the project claims to support.

Two numbers describe the floor -- requires-python in pyproject.toml and the
lowest entry in the workflow matrix -- and nothing connects them. Raise one
and CI either tests a version the project disowns or stops testing the oldest
one it promises. Both failures are silent and both are found by a user.

These parse the files as text rather than importing a YAML or TOML library,
because the suite is stdlib-only and adding a dependency to check that there
are no dependencies would be its own joke.
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class FloorTest(unittest.TestCase):

    def setUp(self):
        self.toml = read("pyproject.toml")
        self.yml = read(os.path.join(".github", "workflows", "ci.yml"))

    def matrix(self):
        m = re.search(r'python:\s*\[([^\]]+)\]', self.yml)
        self.assertIsNotNone(m, "no python matrix in the workflow")
        return [v.strip().strip('"\'') for v in m.group(1).split(",")]

    @staticmethod
    def key(v):
        return tuple(int(p) for p in v.split("."))

    def test_the_matrix_floor_matches_requires_python(self):
        m = re.search(r'requires-python\s*=\s*">=\s*([0-9.]+)"', self.toml)
        self.assertIsNotNone(m, "pyproject declares no requires-python")
        self.assertEqual(min(self.matrix(), key=self.key), m.group(1),
                         "the workflow does not test the oldest version "
                         "pyproject promises")

    def test_the_matrix_is_sorted_and_unique(self):
        got = self.matrix()
        self.assertEqual(got, sorted(set(got), key=self.key))

    def test_the_tests_job_needs_no_dependencies(self):
        """hydroc is stdlib-only on purpose: the daemon runs as root at boot
        and every dependency is something that can fail to import there. A
        pip install step appearing here means that stopped being true."""
        self.assertNotIn("pip install", self.yml)
        self.assertIn("dependencies = []", self.toml)

    def test_the_workflow_runs_the_command_that_works(self):
        self.assertIn("unittest discover -s tests -t .", self.yml)

    def test_the_blob_guard_covers_the_extension_that_got_through(self):
        """The near miss was a .zip whose name the gitignore pattern did not
        match. The extensions inside it are what this catches."""
        for ext in ("eluk", "rom", "bin"):
            self.assertIn(ext, self.yml.lower())


class GitignoreTest(unittest.TestCase):

    def test_no_duplicate_patterns(self):
        seen, dupes = set(), []
        for ln in read(".gitignore").splitlines():
            p = ln.strip()
            if not p or p.startswith("#"):
                continue
            if p in seen:
                dupes.append(p)
            seen.add(p)
        self.assertEqual(dupes, [], f"duplicated: {dupes}")

    def test_vendor_firmware_is_ignored(self):
        g = read(".gitignore")
        for pat in ("*.ELUK", "*_EC_[0-9]*.zip"):
            self.assertIn(pat, g)


if __name__ == "__main__":
    unittest.main()
