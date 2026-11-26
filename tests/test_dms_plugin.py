# SPDX-License-Identifier: MIT
"""The DMS plugin stays in step with the daemon it is a client of.

It is QML, so nothing here runs it. What can drift silently is the contract:
a route renamed in server.py, a token header renamed in apitoken.py, or a
manifest pointing at a file that moved. Any of those leaves the flyout loading
cleanly and doing nothing.
"""

import json
import os
import re
import unittest

from hydroc import apitoken

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN = os.path.join(_ROOT, "dms-plugin", "hydrocontrol")


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf8") as fh:
        return fh.read()


class ManifestTest(unittest.TestCase):

    def setUp(self):
        self.m = json.loads(_read(_PLUGIN, "plugin.json"))

    def test_required_fields(self):
        for k in ("id", "name", "description", "version", "author", "type",
                  "capabilities", "component"):
            self.assertIn(k, self.m)
        self.assertEqual(self.m["type"], "widget")
        self.assertIn("dankbar-widget", self.m["capabilities"])

    def test_files_exist(self):
        for k in ("component", "settings"):
            self.assertTrue(os.path.isfile(os.path.join(_PLUGIN, self.m[k])), self.m[k])

    def test_settings_page_names_this_plugin(self):
        self.assertIn(f'pluginId: "{self.m["id"]}"', _read(_PLUGIN, "HydroSettings.qml"))

    def test_settings_need_the_write_permission(self):
        # DMS refuses to show a settings page without it.
        self.assertIn("settings_write", self.m["permissions"])


class DaemonContractTest(unittest.TestCase):

    def setUp(self):
        self.qml = _read(_PLUGIN, "HydroWidget.qml")
        server = _read(_ROOT, "hydroc", "server.py")
        self.routes = set(re.findall(r'route == "(/api/[^"]+)"', server))

    def test_every_endpoint_it_calls_exists(self):
        called = set(re.findall(r'"(/api/[a-z/-]+)"', self.qml))
        self.assertTrue(called)
        self.assertEqual(called - self.routes, set())

    def test_it_sends_the_header_the_daemon_checks(self):
        self.assertIn(f'"{apitoken.HEADER}"', self.qml)

    def test_it_reads_the_token_where_the_daemon_writes_it(self):
        self.assertIn(f'path: "{apitoken.TOKEN_PATH}"', self.qml)

    def _tcc_card(self):
        i = self.qml.index('title: "CPU temperature limit"')
        return self.qml[i:self.qml.index("// ── battery", i)]

    def test_the_temperature_limit_uses_the_daemons_keys(self):
        card = self._tcc_card()
        self.assertIn("cpu_tcc_enable: true", card)
        self.assertIn("cpu_tcc_offset: root.tjmax - t", card)

    def test_the_temperature_limit_writes_only_on_set_or_enter(self):
        # Applying on blur fought the user mid-entry in the app.
        card = self._tcc_card()
        self.assertIn("onAccepted: tccCard.set()", card)
        self.assertIn("onClicked: tccCard.set()", card)
        for auto in ("onEditingFinished", "onTextChanged", "onTextEdited: tccCard.set",
                     "onFocusStateChanged"):
            self.assertNotIn(auto, card)

    def test_a_refresh_never_overwrites_the_field_being_typed_in(self):
        self.assertIn("if (!tccField.inputFocused)", self._tcc_card())

    def test_the_flyout_never_runs_off_the_screen(self):
        # DMS sizes a popout to its content with no cap, so adding the
        # temperature limit pushed the last sections off the bottom. The body
        # scrolls inside a height the screen can hold.
        pop = self.qml[self.qml.index("popoutContent: Component"):]
        self.assertIn("DC.DFlickable", pop)
        self.assertIn("root.parentScreen.height", pop)
        self.assertIn("height: Math.min(content.implicitHeight", pop)
        # A fixed popoutHeight would bring back a blank gap on a short flyout.
        self.assertNotRegex(self.qml, r"popoutHeight:\s*[1-9]")

    def test_the_mux_is_never_switched_from_the_flyout(self):
        # A firmware write that needs a reboot does not belong one click away
        # in a panel; the flyout opens the app for it.
        self.assertNotIn("/api/gpu/set", self.qml)


if __name__ == "__main__":
    unittest.main()
