# SPDX-License-Identifier: MIT
"""Never unload a module with no file to load back.

A rolling-distro kernel upgrade replaces /lib/modules/<version> while the old
kernel is still running. The resident modules keep working, but there is nothing
on disk to reload, and DKMS has built for the kernel you will boot NEXT. So
`modprobe -r` succeeds where `modprobe` cannot, leaving no driver and 0x0741
bit 0 clear (DESIGN.md §4.1) until a reboot -- irreversible, from a button
labelled Repair.

The guard must be conservative in one direction only: refusing a safe reload is
an inconvenience, permitting an unsafe one strands the machine.
"""

import unittest
from unittest import mock

from hydroc import kmod


class SafeToReloadTest(unittest.TestCase):

    def test_refuses_when_the_running_kernel_has_no_module_tree(self):
        with mock.patch.object(kmod, "module_tree_present", return_value=False):
            ok, why = kmod.safe_to_reload("uniwill-laptop")
        self.assertFalse(ok)
        self.assertIn("upgraded since boot", why)
        self.assertIn("reboot", why.lower())

    def test_refuses_when_the_tree_exists_but_the_module_does_not(self):
        with mock.patch.object(kmod, "module_tree_present", return_value=True), \
             mock.patch.object(kmod, "module_file", return_value=None):
            ok, why = kmod.safe_to_reload("uniwill-laptop")
        self.assertFalse(ok)
        self.assertIn("no uniwill-laptop module file", why)

    def test_permits_only_when_tree_and_module_are_both_present(self):
        with mock.patch.object(kmod, "module_tree_present", return_value=True), \
             mock.patch.object(kmod, "module_file",
                               return_value="/lib/modules/x/uniwill-laptop.ko.zst"):
            ok, why = kmod.safe_to_reload("uniwill-laptop")
        self.assertTrue(ok)
        self.assertIn("uniwill-laptop", why)

    def test_refusal_always_explains_itself(self):
        # The reason is surfaced verbatim by server.py and install.sh.
        for tree, mod in ((False, None), (True, None)):
            with mock.patch.object(kmod, "module_tree_present", return_value=tree), \
                 mock.patch.object(kmod, "module_file", return_value=mod):
                ok, why = kmod.safe_to_reload("uniwill-laptop")
            self.assertFalse(ok)
            self.assertTrue(why.strip(), "refused with no reason")


class RebootPendingTest(unittest.TestCase):

    def test_tracks_the_module_tree(self):
        with mock.patch.object(kmod, "module_tree_present", return_value=False):
            self.assertTrue(kmod.reboot_pending())
        with mock.patch.object(kmod, "module_tree_present", return_value=True):
            self.assertFalse(kmod.reboot_pending())


class ModuleFileTest(unittest.TestCase):

    def test_missing_module_resolves_to_none(self):
        self.assertIsNone(kmod.module_file("definitely-not-a-module-xyz"))

    def test_module_tree_path_follows_the_running_kernel(self):
        self.assertTrue(kmod.module_tree().endswith(kmod.running_kernel()))


class CliExitCodeTest(unittest.TestCase):
    """install.sh branches on these: 0 safe, non-zero refuse."""

    def test_unsafe_exits_nonzero(self):
        import contextlib, io
        with mock.patch.object(kmod, "module_tree_present", return_value=False), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kmod.main(["check", "uniwill-laptop"]), 1)

    def test_safe_exits_zero(self):
        import contextlib, io
        with mock.patch.object(kmod, "module_tree_present", return_value=True), \
             mock.patch.object(kmod, "module_file", return_value="/x.ko"), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(kmod.main(["check", "uniwill-laptop"]), 0)


if __name__ == "__main__":
    unittest.main()
