# SPDX-License-Identifier: MIT
"""The optional matugen palette.

Two properties matter. It must be genuinely optional -- most testers will never
have the file, and its absence must leave the built-in palette untouched. And
the path must come from the *user's* environment, because the desktop app runs
unprivileged; the daemon must never read it, since under systemd HOME=/root and
the user's config is invisible (DESIGN.md §4.4).

Skipped where GTK/WebKit are unavailable, so the suite still runs on a headless
box or a tester's machine without the desktop dependencies.
"""

import os
import tempfile
import unittest
from unittest import mock

try:
    from hydroc import desktop
    from gi.repository import WebKit
    HAVE_GTK = True
except Exception:                                    # noqa: BLE001
    HAVE_GTK = False


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class PalettePathTest(unittest.TestCase):

    def test_respects_xdg_config_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            self.assertEqual(desktop.palette_path(),
                             "/tmp/xdg/hydroc/palette.css")

    def test_falls_back_to_dot_config(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            os.environ["HOME"] = "/home/someone"
            self.assertEqual(desktop.palette_path(),
                             "/home/someone/.config/hydroc/palette.css")


class _FakeContentManager:
    def __init__(self):
        self.sheets = []

    def add_style_sheet(self, sheet):
        self.sheets.append(sheet)

    def remove_style_sheet(self, sheet):
        self.sheets.remove(sheet)


@unittest.skipUnless(HAVE_GTK, "GTK4/WebKit not available")
class LoadPaletteTest(unittest.TestCase):
    """_load_palette only touches self.content and self._palette_sheet, so the
    real method runs against a stand-in rather than a live GTK window."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stub = type("W", (), {"content": _FakeContentManager(),
                                   "_palette_sheet": None})()
        patcher = mock.patch.dict(os.environ,
                                  {"XDG_CONFIG_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, css):
        d = os.path.join(self.tmp.name, "hydroc")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "palette.css"), "w") as fh:
            fh.write(css)

    def test_missing_file_injects_nothing(self):
        desktop.Window._load_palette(self.stub)
        self.assertEqual(self.stub.content.sheets, [])
        self.assertIsNone(self.stub._palette_sheet)

    def test_empty_file_injects_nothing(self):
        self._write("   \n  ")
        desktop.Window._load_palette(self.stub)
        self.assertEqual(self.stub.content.sheets, [])

    def test_present_file_is_injected_once(self):
        self._write(":root{--color-accent:#ff0000 !important}")
        desktop.Window._load_palette(self.stub)
        self.assertEqual(len(self.stub.content.sheets), 1)
        self.assertIsNotNone(self.stub._palette_sheet)

    def test_reload_replaces_rather_than_stacking(self):
        # matugen rewrites on every wallpaper change; without the removal the
        # manager would accumulate a sheet per change for the whole session.
        self._write(":root{--color-accent:#ff0000 !important}")
        for _ in range(5):
            desktop.Window._load_palette(self.stub)
        self.assertEqual(len(self.stub.content.sheets), 1)

    def test_deleting_the_file_restores_the_builtin_palette(self):
        self._write(":root{--color-accent:#ff0000 !important}")
        desktop.Window._load_palette(self.stub)
        os.unlink(os.path.join(self.tmp.name, "hydroc", "palette.css"))
        desktop.Window._load_palette(self.stub)
        self.assertEqual(self.stub.content.sheets, [])
        self.assertIsNone(self.stub._palette_sheet)


class TemplateTest(unittest.TestCase):
    """The template is data, so it can be checked without GTK."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "matugen", "hydrocontrol.css")) as fh:
            self.tpl = fh.read()
        with open(os.path.join(root, "hydroc", "ui", "index.html")) as fh:
            self.ui = fh.read()

    def test_status_colours_are_never_generated(self):
        # A wallpaper must not decide what "danger" looks like on a panel whose
        # controls can overheat a machine or arm a one-way latch.
        for token in ("--color-danger", "--color-warn", "--color-info"):
            self.assertNotIn(token + ":", self.tpl,
                             f"{token} must stay fixed, not follow the wallpaper")

    def test_every_generated_token_exists_in_the_app(self):
        import re
        app = set(re.findall(r"--([a-z0-9-]+)\s*:", self.ui))
        for token in set(re.findall(r"--([a-z0-9-]+)\s*:", self.tpl)):
            self.assertIn(token, app, f"template sets unknown token --{token}")

    def test_every_declaration_is_important(self):
        # USER-origin normal declarations lose to the page's author styles.
        import re
        body = self.tpl[self.tpl.index(":root {"):]
        for decl in re.findall(r"--[a-z0-9-]+\s*:[^;]+;", body):
            self.assertIn("!important", decl, f"not important: {decl.strip()}")


if __name__ == "__main__":
    unittest.main()
