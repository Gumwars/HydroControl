# SPDX-License-Identifier: MIT
"""Root runs only code root alone can change.

Two ways this failed. kbctrl.hardware put every home's ~/.local site-packages
and pipx venv at the front of sys.path, inside a daemon running as root, so a
file any account dropped there ran as root. And the desktop window handed
pkexec the checkout's own install.sh and package, so whatever could edit the
checkout chose what the user then approved in a password dialog.

The temporary trees here are owned by the user running the tests, which is
exactly the untrusted case: nothing below may treat them as safe for root.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "kbctrl"))

import kbctrl.hardware as kb  # noqa: E402
from hydroc import trust      # noqa: E402

try:
    from hydroc import desktop
    HAVE_GTK = True
except Exception:  # noqa: BLE001 -- GTK4/WebKit absent
    HAVE_GTK = False


def _venv(base):
    site = os.path.join(base, "venvs", "ite8291r3-ctl", "lib", "python3.14",
                        "site-packages")
    os.makedirs(os.path.join(site, "ite8291r3_ctl"))
    return site


class TrustTest(unittest.TestCase):

    def test_a_tree_you_own_is_not_roots(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(trust.root_only(d))
            self.assertFalse(trust.tree_root_only(d))
            self.assertFalse(kb._root_only(d))
            self.assertFalse(kb._tree_root_only(d))

    def test_root_owned_system_directory_is(self):
        self.assertTrue(trust.root_only("/usr/bin"))
        self.assertTrue(kb._root_only("/usr/bin"))

    def test_missing_path_is_not_trusted(self):
        self.assertFalse(trust.root_only("/nonexistent/hydroc"))
        self.assertFalse(kb._root_only("/nonexistent/hydroc"))

    @unittest.skipIf(os.geteuid() == 0, "a root test run owns its temp files")
    def test_a_root_owned_path_inside_a_user_home_is_not(self):
        # The chain matters, not only the leaf: a root-owned file under a
        # directory you own can be renamed away and replaced.
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(trust.root_only(os.path.join(d, "x")))


class RootSearchPathTest(unittest.TestCase):

    def test_root_never_searches_a_home(self):
        with tempfile.TemporaryDirectory() as home:
            _venv(os.path.join(home, ".local", "share", "pipx"))
            os.makedirs(os.path.join(home, ".local", "lib", "python3.14",
                                     "site-packages"))
            with mock.patch.dict(os.environ, {"HOME": home,
                                              "SUDO_USER": "nobody"}):
                paths = kb._ite_search_paths(euid=0)
        self.assertFalse([p for p in paths if p.startswith(home)], paths)
        self.assertFalse([p for p in paths if p.startswith("/home/")], paths)

    def test_root_rejects_a_global_pipx_it_does_not_own(self):
        with tempfile.TemporaryDirectory() as base:
            _venv(base)
            with mock.patch.dict(os.environ, {"PIPX_GLOBAL_HOME": base}):
                self.assertEqual(kb._ite_search_paths(euid=0), [])

    def test_a_user_keeps_their_own_pipx_venv(self):
        with tempfile.TemporaryDirectory() as home:
            site = _venv(os.path.join(home, ".local", "share", "pipx"))
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertIn(site, kb._ite_search_paths(euid=1000))

    def test_paths_are_appended_not_put_first(self):
        with open(kb.__file__) as fh:
            src = fh.read()
        self.assertNotIn("sys.path.insert(0", src)
        self.assertNotIn('glob("/home/*")', src)


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class PrivilegedRootTest(unittest.TestCase):

    def _tree(self, d):
        open(os.path.join(d, "install.sh"), "w").close()
        for sub in ("hydroc", "kbctrl/kbctrl", "uniwill-laptop"):
            os.makedirs(os.path.join(d, sub))

    def test_a_checkout_you_can_edit_is_never_offered(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            with mock.patch.object(desktop, "INSTALL_ROOT", d):
                root, why = desktop.privileged_root(here=d)
        self.assertIsNone(root)
        self.assertIn("Refusing", why)

    def test_repair_does_not_reach_pkexec_without_a_root_copy(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            with mock.patch.object(desktop, "INSTALL_ROOT", d), \
                 mock.patch.object(desktop, "privileged_root",
                                   return_value=(None, "Refusing: test")), \
                 mock.patch.object(desktop.shutil, "which", return_value="/usr/bin/pkexec"), \
                 mock.patch.object(desktop.subprocess, "run") as run:
                ok, why = desktop.repair_now()
        self.assertFalse(ok)
        run.assert_not_called()

    def test_the_deployed_copy_is_offered_when_it_is_roots(self):
        with mock.patch.object(desktop.trust, "root_only", return_value=True), \
             mock.patch.object(desktop.trust, "tree_root_only", return_value=True):
            root, _ = desktop.privileged_root(here="/opt/hydrocontrol")
        self.assertEqual(root, "/opt/hydrocontrol")


if __name__ == "__main__":
    unittest.main()
