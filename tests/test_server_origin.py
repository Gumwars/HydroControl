# SPDX-License-Identifier: MIT
"""Cross-site POSTs to a root daemon.

Binding to loopback is not an access control. This daemon runs as root, and
its POST routes write non-volatile EFI variables (/api/gpu/set) and shell out
to the installer (/api/repair). Any page the owner visits can POST to
127.0.0.1 from their browser, and the two-gate design on /api/gpu/set is no
obstacle to a page that sets both gates.

A browser always sends Origin on a cross-origin POST and cannot suppress it.
A missing Origin is allowed on purpose: curl and this project's own scripts
do not send one, and an attacker who can already run curl here does not need
the daemon.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from hydroc import server


class OriginTest(unittest.TestCase):

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def post(self, route, payload, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{route}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def test_a_foreign_origin_is_refused(self):
        with mock.patch.object(server.gpumode, "set_mode") as m:
            status, body = self.post("/api/gpu/set",
                                     {"mode": "igpu", "confirm": True},
                                     {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("evil.example", body.get("error", ""))
        m.assert_not_called()

    def test_the_firmware_write_never_runs_on_a_refused_request(self):
        """The assertion that matters: refusal happens before routing, so no
        handler sees the payload."""
        with mock.patch.object(server.gpumode, "set_mode") as m:
            self.post("/api/gpu/set", {"mode": "igpu", "confirm": True},
                      {"Origin": "http://127.0.0.1:1"})
        m.assert_not_called()

    def test_our_own_origin_is_accepted(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               return_value={"ok": True}) as m:
            status, _ = self.post("/api/gpu/set",
                                  {"mode": "igpu", "confirm": True},
                                  {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)
        m.assert_called_once()

    def test_no_origin_is_accepted_so_curl_keeps_working(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               return_value={"ok": True}) as m:
            status, _ = self.post("/api/gpu/set",
                                  {"mode": "igpu", "confirm": True})
        self.assertEqual(status, 200)
        m.assert_called_once()

    def test_a_rebinding_host_is_refused(self):
        """A name that resolves to 127.0.0.1 still arrives with its own Host,
        which is what makes this worth checking separately from Origin."""
        with mock.patch.object(server.gpumode, "set_mode") as m:
            status, body = self.post("/api/gpu/set",
                                     {"mode": "igpu", "confirm": True},
                                     {"Host": "attacker.example"})
        self.assertEqual(status, 403)
        self.assertIn("Host", body.get("error", ""))
        m.assert_not_called()

    def test_the_check_follows_the_port_actually_bound(self):
        """Deriving it from the module's PORT constant refuses every request
        on any other port, which is every test and any alternate binding."""
        self.assertNotEqual(self.port, server.PORT)
        hosts, origins = server.Handler._allowed(
            mock.Mock(server=mock.Mock(server_address=("127.0.0.1", self.port))))
        self.assertIn(f"127.0.0.1:{self.port}", hosts)
        self.assertIn(f"http://127.0.0.1:{self.port}", origins)


class StaticFileContainmentTest(unittest.TestCase):
    """UI_DIR ends in "ui", so a startswith check also accepts a sibling
    directory named ui_anything. No such directory exists, which made this
    one mkdir from exploitable rather than safe."""

    def test_a_sibling_prefix_directory_is_not_inside_the_ui_dir(self):
        import os
        ui = server.UI_DIR
        sibling = os.path.normpath(os.path.join(ui, "..", os.path.basename(ui) + "_secret", "x.html"))
        self.assertTrue(sibling.startswith(ui), "precondition: startswith passes")
        self.assertNotEqual(os.path.commonpath([ui, sibling]), ui,
                            "commonpath must reject it")

    def test_the_handler_uses_commonpath(self):
        import inspect
        src = inspect.getsource(server.Handler._file)
        self.assertIn("commonpath", src)
        self.assertNotIn("path.startswith(UI_DIR)", src)


if __name__ == "__main__":
    unittest.main()
