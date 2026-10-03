# SPDX-License-Identifier: MIT
"""A log must not append under a header it did not write.

battery_watch.py wrote its header only when the output file did not exist,
so a run against a CSV from an older version of the script appended 13
columns under an 11-column header. csv.DictReader reads by position: the
columns up to current_ma survive, and everything after shifts one name to
the left, so `profile` reads the voltage.

battery_summary.py segments a run whenever profile or status changes. A
value that moves every sample turns every sample into its own segment, which
destroys the analysis without producing an error.

The damage lands on the run being collected, not the next one -- it is found
after the experiment, if at all. charge_profile_probe.py was given this
guard after the same thing happened there; this script never got it.
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WATCH = os.path.join(_ROOT, "battery_watch.py")


def header_const():
    src = open(_WATCH, encoding="utf-8").read()
    m = re.search(r'CSV_HEADER = \(([^)]*)\)', src, re.S)
    assert m, "CSV_HEADER not found"
    return "".join(re.findall(r'"([^"]*)"', m.group(1)))


class HeaderGuardTest(unittest.TestCase):

    def run_against(self, contents):
        with tempfile.NamedTemporaryFile("w", suffix=".csv",
                                         delete=False) as fh:
            fh.write(contents)
            path = fh.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        r = subprocess.run([sys.executable, _WATCH, "-o", path],
                           capture_output=True, text=True, timeout=30,
                           cwd=_ROOT)
        return r, path

    def test_a_stale_header_is_refused(self):
        old = ("time,ac,status,capacity,true_pct,charge_now_mah,"
               "charge_full_mah,current_ma,profile,threshold,mode\n")
        r, _ = self.run_against(old)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("different version", r.stdout + r.stderr)

    def test_the_refusal_names_both_headers(self):
        """So the reader can see which column moved, rather than being told
        only that something is wrong."""
        old = ("time,ac,status,capacity,true_pct,charge_now_mah,"
               "charge_full_mah,current_ma,profile,threshold,mode\n")
        r, _ = self.run_against(old)
        out = r.stdout + r.stderr
        self.assertIn("in the file:", out)
        self.assertIn("would write:", out)
        self.assertIn("current_ma", out)

    def test_it_does_not_append_when_it_refuses(self):
        old = ("time,ac,status,capacity,true_pct,charge_now_mah,"
               "charge_full_mah,current_ma,profile,threshold,mode\n")
        r, path = self.run_against(old)
        self.assertEqual(open(path).read(), old, "it wrote anyway")


class HeaderMatchesRowsTest(unittest.TestCase):
    """The guard is only worth having if the constant is what the row
    writer actually produces."""

    def setUp(self):
        self.src = open(_WATCH, encoding="utf-8").read()

    def test_the_row_writer_emits_one_field_per_column(self):
        """Counting LITERAL separators. round(s['true'],2) contains a comma
        that is not a field break, and a naive count reports fourteen
        columns where the writer emits thirteen."""
        m = re.search(r'csv\.write\(f"(.*?)\\n"\)', self.src, re.S)
        self.assertIsNotNone(m, "the row writer moved")
        body = re.sub(r'\s*"\s*f"', '', m.group(1))      # join adjacent f-strings
        depth, seps = 0, 0
        for ch in body:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "," and depth == 0:
                seps += 1
        self.assertEqual(seps + 1, len(header_const().split(",")),
                         "the row writer and CSV_HEADER disagree on width")

    def test_the_header_is_a_single_definition(self):
        """Two copies is how they drifted apart the first time."""
        self.assertEqual(self.src.count("CSV_HEADER ="), 1)
        self.assertIn("csv.write(CSV_HEADER", self.src)

    def test_voltage_columns_are_present(self):
        h = header_const()
        self.assertIn("millivolts", h)
        self.assertIn("v_per_cell", h)


if __name__ == "__main__":
    unittest.main()
