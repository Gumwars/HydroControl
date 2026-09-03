# SPDX-License-Identifier: MIT
"""POST routes must answer.

This exists because /api/gpu/set once read the request body twice. The second
read blocks forever: the client has already sent exactly Content-Length bytes
and is waiting on a reply, so rfile.read() never returns. The daemon stayed
healthy and every GET kept working -- only that one route wedged, and the app
sat on "writing firmware..." with no error and no timeout. Nothing was written,
which is the only reason it was harmless.

No unit test on set_mode() could have caught it, because the handler never got
as far as calling set_mode. So these drive the real Handler over a real socket
and assert only that a reply arrives.
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from hydroc import server

TIMEOUT = 5          # generous; the bug hung forever, it does not hang briefly


class PostRoutesAnswerTest(unittest.TestCase):

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def post(self, route, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{route}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        def decode(raw):
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                return {}                              # 404 is served as HTML
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, decode(r.read())
        except urllib.error.HTTPError as e:            # a reply is still a reply
            return e.code, decode(e.read())

    def test_gpu_set_replies(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               return_value={"ok": True, "message": "written"}) as m:
            status, body = self.post("/api/gpu/set",
                                     {"mode": "dynamic", "confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        m.assert_called_once_with("dynamic", confirm=True)

    def test_gpu_set_passes_the_body_through(self):
        """The payload must survive -- a hang was not the only way to lose it."""
        with mock.patch.object(server.gpumode, "set_mode",
                               return_value={"ok": True}) as m:
            self.post("/api/gpu/set", {"mode": "igpu", "confirm": True})
        m.assert_called_once_with("igpu", confirm=True)

    def test_gpu_set_without_confirm_is_refused_not_hung(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               side_effect=server.gpumode.GpuModeError("needs confirm")):
            status, body = self.post("/api/gpu/set", {"mode": "igpu"})
        self.assertEqual(status, 400)
        self.assertIn("confirm", body.get("error", ""))

    def test_firmware_error_is_reported_not_hung(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               side_effect=OSError("EIO")):
            status, body = self.post("/api/gpu/set", {"mode": "igpu", "confirm": True})
        self.assertEqual(status, 500)
        self.assertIn("firmware write failed", body.get("error", ""))

    def test_unknown_post_route_replies(self):
        status, _ = self.post("/api/nonesuch", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
