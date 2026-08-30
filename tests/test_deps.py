# SPDX-License-Identifier: MIT
"""Dependency checks answer the DAEMON's question, not the caller's.

The daemons run as root under systemd, so "can this shell import X" is a
different question with a different answer. Getting that wrong told a tester
their keyboard library was missing when it was installed and working, and let
install.sh recommend a plain `pip install` that lands in ~/.local where the root
sidecar cannot see it (DESIGN.md §4.3, §4.4).

Two properties matter and are pinned here: an unprivileged caller gets UNKNOWN
rather than False, and the probe subprocess does not inherit the caller's
environment.
"""

import contextlib
import io
import os
import unittest
from unittest import mock


@contextlib.contextmanager
def quiet():
    """main() prints for a human; tests assert on exit codes, not noise."""
    with contextlib.redirect_stdout(io.StringIO()), \
         contextlib.redirect_stderr(io.StringIO()):
        yield

from hydroc import deps


class UnknownWhenUnprivilegedTest(unittest.TestCase):

    @mock.patch("os.geteuid", return_value=1000)
    def test_unprivileged_is_unknown_not_missing(self, _):
        for name in deps.DEPS:
            r = deps.check(name)
            self.assertIsNone(r["ok"], f"{name} claimed a result without root")
            self.assertTrue(r["unknown"])

    @mock.patch("os.geteuid", return_value=1000)
    def test_unknown_offers_no_remedy(self, _):
        # Advice attached to a check that never ran is confident wrong advice.
        self.assertIsNone(deps.check("bleak")["remedy"])


class ProbeEnvironmentTest(unittest.TestCase):

    def test_env_reproduces_systemd_not_the_caller(self):
        env = deps._daemon_env()
        self.assertEqual(env["HOME"], "/root")
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("SUDO_USER", env)
        self.assertNotIn("XDG_RUNTIME_DIR", env)

    @mock.patch("os.geteuid", return_value=0)
    def test_caller_pythonpath_does_not_leak_into_the_probe(self, _):
        probe = deps.Dep(
            label="leak", remedy="",
            probe="import os, sys; sys.stdout.write(repr(os.environ.get('PYTHONPATH')))")
        with mock.patch.dict(deps.DEPS, {"_leak": probe}), \
             mock.patch.dict(os.environ, {"PYTHONPATH": "/tmp/poison"}):
            self.assertEqual(deps.check("_leak")["detail"], "None")


class ResultShapeTest(unittest.TestCase):

    @mock.patch("os.geteuid", return_value=0)
    def test_missing_module_reports_missing_with_a_remedy(self, _):
        probe = deps.Dep(label="absent", probe="import definitely_not_a_module",
                         remedy="install the thing")
        with mock.patch.dict(deps.DEPS, {"_absent": probe}):
            r = deps.check("_absent")
        self.assertIs(r["ok"], False)
        self.assertFalse(r["unknown"])
        self.assertEqual(r["remedy"], "install the thing")
        self.assertIn("definitely_not_a_module", r["detail"])

    @mock.patch("os.geteuid", return_value=0)
    def test_present_module_reports_ok_and_no_remedy(self, _):
        probe = deps.Dep(label="present", remedy="unused",
                         probe="import sys; sys.stdout.write('1.2.3')")
        with mock.patch.dict(deps.DEPS, {"_present": probe}):
            r = deps.check("_present")
        self.assertIs(r["ok"], True)
        self.assertEqual(r["detail"], "1.2.3")
        self.assertIsNone(r["remedy"])

    def test_every_declared_dep_has_a_remedy(self):
        # A failing check with no remedy is a dead end for the user.
        for name, dep in deps.DEPS.items():
            self.assertTrue(dep.remedy.strip(), f"{name} has no remedy text")

    def test_remedy_is_single_sourced(self):
        # install.sh and lppd both render this; they must not diverge.
        for name, dep in deps.DEPS.items():
            self.assertEqual(deps.remedy(name), dep.remedy)


class CliExitCodeTest(unittest.TestCase):
    """0 ok, 1 missing, 2 unknown -- install.sh branches on these."""

    @mock.patch("os.geteuid", return_value=1000)
    def test_unknown_exits_two(self, _):
        with quiet():
            self.assertEqual(deps.main(["check", "bleak"]), 2)

    def test_bad_usage_exits_sixty_four(self):
        with quiet():
            self.assertEqual(deps.main(["nonsense"]), 64)
            self.assertEqual(deps.main(["check", "not-a-dep"]), 64)


if __name__ == "__main__":
    unittest.main()
