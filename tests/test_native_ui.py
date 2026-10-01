# SPDX-License-Identifier: MIT
"""The machine's own modes, as the UI sees them.

Native modes worked on the hardware and through the API for a day while being
invisible in the app -- reachable only by pressing the physical button or
curling the route. The same gap as the RGB correction flag: a server half and
no client half, which reads as "not implemented" from the only side the owner
looks at.

The exclusion is the part worth pinning. Exactly one of the two is in charge,
because hardware.read_state() reports native_mode as None whenever the
custom-profile latch is up. The UI must not invent its own tracking of that,
or the two can disagree.
"""

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from hydroc import server

_UI = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "hydroc", "ui", "index.html")


def ui():
    with open(_UI, encoding="utf-8") as fh:
        return fh.read()


class UIExposesNativeModesTest(unittest.TestCase):

    def setUp(self):
        self.src = ui()

    def test_the_native_row_is_rendered_from_the_api(self):
        self.assertIn("S.native.map(", self.src)
        self.assertIn('id="native"', self.src)

    def test_picking_one_posts_to_the_native_route(self):
        self.assertIn("/api/native", self.src)

    def test_the_api_fields_are_read_into_state(self):
        for field in ("pz.native", "pz.native_active", "pz.button_cycle"):
            self.assertIn(field, self.src,
                          f"{field} is served and never read")

    def test_a_preset_clears_the_native_selection(self):
        """Both rows showing a selection at once would be a lie about the
        hardware: applying a preset re-arms the latch, which is exactly what
        makes native_mode read None."""
        i = self.src.index("/api/preset'")
        self.assertIn("S.nativeActive=null", self.src[i:i + 600])

    def test_the_native_row_reflects_only_the_server_truth(self):
        """nativeActive is assigned from the response or the poll, never
        inferred from which button was clicked."""
        self.assertIn("S.nativeActive=pz.native_active", self.src)

    def test_the_stale_claim_about_the_ec_is_gone(self):
        """The card used to say the EC has no performance modes of its own.
        It has three, it holds limits and fan curves for each, and that line
        sat directly above the row that now applies them."""
        self.assertNotIn("The EC has no performance modes of its own",
                         self.src)

    def test_the_button_cycle_choice_is_offered(self):
        self.assertIn('id="bcycle"', self.src)
        self.assertIn("/api/button-cycle", self.src)


class ButtonCycleRouteTest(unittest.TestCase):

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.addCleanup(self.srv.server_close)

    def post(self, route, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{route}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def test_it_merges_rather_than_replacing_the_profile(self):
        """/api/profile overwrites the whole document. Routing this through
        it would drop every other key, because the UI does not hold them."""
        saved = {}
        with mock.patch.object(server, "load_profile",
                               return_value=({"cpu_pl1": 75,
                                              "native_mode": "beast"}, None)), \
             mock.patch.object(server, "save_profile",
                               side_effect=lambda p: saved.update(p)):
            status, body = self.post("/api/button-cycle", {"cycle": "presets"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertEqual(saved.get("button_cycle"), "presets")
        self.assertEqual(saved.get("cpu_pl1"), 75, "dropped an unrelated key")
        self.assertEqual(saved.get("native_mode"), "beast")

    def test_an_unknown_value_is_refused(self):
        with mock.patch.object(server, "save_profile") as sv:
            status, body = self.post("/api/button-cycle", {"cycle": "wat"})
        self.assertEqual(status, 400)
        self.assertIn("wat", body.get("error", ""))
        sv.assert_not_called()

    def test_a_missing_value_is_refused(self):
        with mock.patch.object(server, "save_profile") as sv:
            status, _ = self.post("/api/button-cycle", {})
        self.assertEqual(status, 400)
        sv.assert_not_called()


if __name__ == "__main__":
    unittest.main()
