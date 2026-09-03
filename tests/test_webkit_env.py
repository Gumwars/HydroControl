# SPDX-License-Identifier: MIT
"""The NVIDIA WebKit workaround in the desktop window.

The decision is worth pinning down rather than the symptom, because the
symptom takes minutes of real use to appear and cannot be asserted on. Two
properties matter: it must fire when NVIDIA is the only render node -- the
state our own dGPU-only Graphics mode creates -- and it must not fire
anywhere else, including on the hybrid machine most testers are running.

Skipped where GTK/WebKit are unavailable, matching test_palette.
"""

import os
import unittest
from unittest import mock

try:
    from hydroc import desktop
    HAVE_GTK = True
except Exception:                                    # noqa: BLE001
    HAVE_GTK = False

NVIDIA = [("renderD128", "nvidia")]
INTEL = [("renderD128", "i915")]
HYBRID = [("renderD128", "i915"), ("renderD129", "nvidia")]


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class WebkitEnvTest(unittest.TestCase):

    def test_nvidia_only_disables_dmabuf(self):
        self.assertEqual(desktop.webkit_env(NVIDIA, {}),
                         {"WEBKIT_DISABLE_DMABUF_RENDERER": "1"})

    def test_hybrid_is_left_alone(self):
        """Dynamic mode renders on the iGPU and does not have the bug."""
        self.assertEqual(desktop.webkit_env(HYBRID, {}), {})

    def test_non_nvidia_is_left_alone(self):
        self.assertEqual(desktop.webkit_env(INTEL, {}), {})

    def test_no_render_nodes_is_left_alone(self):
        """A machine with no DRM at all: assume nothing, change nothing."""
        self.assertEqual(desktop.webkit_env([], {}), {})

    def test_user_setting_wins_even_when_we_would_set_it(self):
        env = {"WEBKIT_DISABLE_DMABUF_RENDERER": "0"}
        self.assertEqual(desktop.webkit_env(NVIDIA, env), {})

    def test_user_setting_wins_when_we_would_not(self):
        env = {"WEBKIT_DISABLE_DMABUF_RENDERER": "1"}
        self.assertEqual(desktop.webkit_env(HYBRID, env), {})


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class RenderNodesTest(unittest.TestCase):

    def test_reads_the_driver_out_of_uevent(self):
        with mock.patch.object(desktop.glob, "glob",
                               return_value=["/dev/dri/renderD128"]), \
             mock.patch("builtins.open",
                        mock.mock_open(read_data="DRIVER=nvidia\nPCI_CLASS=30000\n")):
            self.assertEqual(desktop.render_nodes(), [("renderD128", "nvidia")])

    def test_unreadable_node_is_skipped_not_raised(self):
        """This window must never fail to open because a diagnosis failed."""
        with mock.patch.object(desktop.glob, "glob",
                               return_value=["/dev/dri/renderD128"]), \
             mock.patch("builtins.open", side_effect=OSError("gone")):
            self.assertEqual(desktop.render_nodes(), [])


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class ApplyTest(unittest.TestCase):

    def test_apply_never_raises(self):
        with mock.patch.object(desktop, "render_nodes",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(desktop.apply_webkit_env(), {})

    def test_apply_sets_the_variable(self):
        clean = {k: v for k, v in os.environ.items()
                 if k != "WEBKIT_DISABLE_DMABUF_RENDERER"}
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(desktop, "render_nodes", return_value=NVIDIA):
            desktop.apply_webkit_env()
            self.assertEqual(os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"], "1")


if __name__ == "__main__":
    unittest.main()
