# SPDX-License-Identifier: MIT
"""tcc_survival_probe.py follows quietly, and stops cleanly.

It logs a row only when something changes, so after the first change a 60 s
follow says nothing -- which read as a hang, and Ctrl+C then landed in the
middle of an EC read with a traceback. A stop now waits for the gap between
snapshots and still ends with a final row.
"""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import tcc_survival_probe as probe


def _row(label, applied=5):
    return {k: "" for k in probe.FIELDS} | {
        "time": "2026-11-26T10:00:00", "label": label, "applied": applied,
        "ec_0786": "0x00", "ec_enable": 0, "ec_offset": 0, "mode": "balanced",
        "mode_0751": "0x00", "custom_latch": 1, "ac": 1}


class FollowTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = probe.Log(os.path.join(self.tmp.name, "t.csv"))
        self.labels = []
        real = self.log.add
        self.log.add = lambda r: (self.labels.append(r["label"]), real(r))
        probe.STOP.clear()
        self.addCleanup(probe.STOP.clear)
        p = mock.patch.object(probe, "snapshot", lambda ec, label: _row(label))
        p.start()
        self.addCleanup(p.stop)

    def test_it_ends_on_its_own_with_a_final_row(self):
        probe.follow(None, self.log, "after set-ec 10", seconds=0.3, interval=0.05)
        self.assertEqual(self.labels[0], "after set-ec 10")
        self.assertEqual(self.labels[-1], "after set-ec 10 (end)")
        # Unchanged rows in between are not logged.
        self.assertEqual(len(self.labels), 2)

    def test_a_stop_returns_promptly_with_a_stopped_row(self):
        threading.Timer(0.15, probe.STOP.set).start()
        t0 = time.monotonic()
        probe.follow(None, self.log, "watch", seconds=60, interval=0.05)
        self.assertLess(time.monotonic() - t0, 1.0, "did not stop promptly")
        self.assertEqual(self.labels[-1], "watch (stopped)")

    def test_the_rows_are_on_disk_as_they_happen(self):
        probe.follow(None, self.log, "x", seconds=0.1, interval=0.05)
        with open(os.path.join(self.tmp.name, "t.csv")) as fh:
            self.assertEqual(len(fh.read().splitlines()), 1 + len(self.labels))


if __name__ == "__main__":
    unittest.main()
