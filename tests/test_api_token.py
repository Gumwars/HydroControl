# SPDX-License-Identifier: MIT
"""Only root and the user at the keyboard can change the hardware.

The Origin and Host checks stop web pages, but a local process sends no Origin
at all, and loopback is shared by every account and sandboxed app on the
machine. Every POST now needs the token the daemon hands to seat0's active
user (hydroc/apitoken.py). These drive the real Handler over a real socket.
"""

import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from hydroc import apitoken, server


class TokenRouteTest(unittest.TestCase):

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    def post(self, route, payload, token=None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers[apitoken.HEADER] = token
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{route}",
                                     data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_no_token_is_refused_before_the_route_runs(self):
        with mock.patch.object(server.gpumode, "set_mode") as m:
            code, body = self.post("/api/gpu/set",
                                   {"mode": "igpu", "confirm": True,
                                    "acknowledge_risks": True})
        self.assertEqual(code, 403)
        self.assertIs(body["token"], False)
        m.assert_not_called()

    def test_a_wrong_token_is_refused(self):
        with mock.patch.object(server.gpumode, "set_mode") as m:
            code, _ = self.post("/api/gpu/set", {"mode": "igpu"}, token="0" * 64)
        self.assertEqual(code, 403)
        m.assert_not_called()

    def test_the_right_token_reaches_the_route(self):
        with mock.patch.object(server.gpumode, "set_mode",
                               return_value={"ok": True}) as m:
            code, _ = self.post("/api/gpu/set", {"mode": "igpu"},
                                token=server._tokens.token)
        self.assertEqual(code, 200)
        m.assert_called_once()

    def test_get_needs_no_token(self):
        with mock.patch.object(server, "_telemetry") as t:
            t.get.return_value = {"cpu_temp_c": 50}
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/telemetry", timeout=5) as r:
                self.assertEqual(json.loads(r.read()), {"cpu_temp_c": 50})


class TokenKeeperTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.seat = os.path.join(self.tmp.name, "seat0")
        self.path = os.path.join(self.tmp.name, "run", "token")

    def active(self, uid):
        with open(self.seat, "w") as fh:
            fh.write(f"ACTIVE=4\nACTIVE_UID={uid}\n")

    def test_seat_uid_reads_logind(self):
        self.active(1000)
        self.assertEqual(apitoken.seat_uid(self.seat), 1000)
        self.assertIsNone(apitoken.seat_uid("/nonexistent/seat0"))

    def test_the_file_is_the_owners_alone_and_reads_back(self):
        k = apitoken.TokenKeeper(path=self.path, seat=self.seat)
        k.refresh()
        st = os.stat(self.path)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o400)
        with mock.patch.object(apitoken, "TOKEN_PATH", self.path):
            self.assertEqual(apitoken.read_token(), k.token)

    def test_a_new_user_at_the_keyboard_gets_a_new_token(self):
        # As root the uid comes from the seat; the write itself is not under
        # test here (it needs root to chown).
        k = apitoken.TokenKeeper(path=self.path, seat=self.seat)
        with mock.patch.object(apitoken.os, "geteuid", return_value=0), \
             mock.patch.object(k, "_write") as w, \
             mock.patch.object(apitoken.os.path, "exists", return_value=True):
            self.active(947)                 # the greeter
            k.refresh()
            first = k.token
            k.refresh()
            self.assertEqual(k.token, first, "same user, same token")
            self.active(1000)                # someone logs in
            k.refresh()
        self.assertNotEqual(k.token, first)
        self.assertFalse(k.check(first))
        self.assertEqual(w.call_args_list[-1].args, (1000,))

    def test_check_rejects_empty_and_wrong(self):
        k = apitoken.TokenKeeper(path=self.path, seat=self.seat)
        self.assertFalse(k.check(None))
        self.assertFalse(k.check(""))
        self.assertFalse(k.check(k.token[:-1] + "x"))
        self.assertTrue(k.check(k.token))


class SampledTest(unittest.TestCase):

    def test_clients_share_one_read_per_interval(self):
        now = [100.0]
        reads = []
        s = server.Sampled(lambda: reads.append(1) or len(reads),
                           max_age=0.9, clock=lambda: now[0])
        self.assertEqual([s.get() for _ in range(5)], [1] * 5)
        now[0] += 0.5
        self.assertEqual(s.get(), 1)
        now[0] += 0.5
        self.assertEqual(s.get(), 2)
        self.assertEqual(len(reads), 2)

    def test_concurrent_clients_wait_for_the_read_in_flight(self):
        gate = threading.Event()
        reads = []

        def slow():
            reads.append(1)
            gate.wait(2)
            return "v"

        s = server.Sampled(slow, max_age=10)
        out = []
        ts = [threading.Thread(target=lambda: out.append(s.get())) for _ in range(4)]
        for t in ts:
            t.start()
        gate.set()
        for t in ts:
            t.join(3)
        self.assertEqual(out, ["v"] * 4)
        self.assertEqual(len(reads), 1)


if __name__ == "__main__":
    unittest.main()
