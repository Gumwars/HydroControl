# SPDX-License-Identifier: MIT
"""The UI must not cost a core to leave open.

On an NVIDIA-only machine the window turns WebKit's DMA-BUF renderer off
(hydroc/desktop.py), and WebKit then draws every frame on the CPU. Measured in
that mode: one pulsing status dot held the window at 105% of a core; without
it, 36%; with the window's `lite` flag dropping the per-second transitions
too, 7%.
"""

import os
import re
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from hydroc import desktop
    HAVE_GTK = True
except Exception:  # noqa: BLE001 -- GTK4/WebKit absent
    HAVE_GTK = False


def _page():
    with open(os.path.join(_ROOT, "hydroc", "ui", "index.html"), encoding="utf8") as fh:
        return fh.read()


class MotionTest(unittest.TestCase):

    def test_nothing_animates_forever(self):
        src = _page()
        self.assertNotIn("infinite", src)
        self.assertIsNone(re.search(r"animation-iteration-count", src))

    def test_lite_turns_off_animation_and_transitions(self):
        css = _page()
        rule = re.search(r"\.lite \*[^{]*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        self.assertIn("animation:none", rule.group(1))
        self.assertIn("transition:none", rule.group(1))

    def test_the_page_reads_lite_from_the_fragment(self):
        src = _page()
        self.assertIn("h.has('lite')", src)
        self.assertIn("classList.add('lite')", src)


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class AppUrlTest(unittest.TestCase):

    def url(self, env, token=None):
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch("hydroc.apitoken.read_token", return_value=token):
            if "WEBKIT_DISABLE_DMABUF_RENDERER" not in env:
                os.environ.pop("WEBKIT_DISABLE_DMABUF_RENDERER", None)
            return desktop.app_url()

    def test_cpu_rendering_asks_for_lite(self):
        u = self.url({"WEBKIT_DISABLE_DMABUF_RENDERER": "1"}, token="ab12")
        self.assertTrue(u.endswith("#t=ab12&lite=1"), u)

    def test_gpu_rendering_keeps_motion(self):
        u = self.url({}, token="ab12")
        self.assertTrue(u.endswith("#t=ab12"), u)

    def test_no_token_and_no_lite_is_the_bare_url(self):
        self.assertEqual(self.url({}), desktop.URL)
