# SPDX-License-Identifier: MIT
"""The volts-per-cell bar, and why the scale is what it is.

A percentage on this machine is derived from a capacity the pack never
reaches, so it cannot show the one thing worth seeing: that charging stops
275 mV per cell below what the cells are rated for. Volts per cell is a real
measurement, and against a track that runs to the rating the unused headroom
is the protection, drawn to scale.

The track is 3.00 V (a conventional Li-ion cutoff) to 4.450 V (this pack's
rating), with the ceiling at 4.175. That puts the ceiling at 81% and leaves
19% of the bar visibly unreachable, which is the entire point of drawing it
this way rather than normalising to the ceiling.
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ui():
    with open(os.path.join(_ROOT, "hydroc", "ui", "index.html"),
              encoding="utf-8") as fh:
        return fh.read()


def consts():
    m = re.search(r"const VMIN=([\d.]+), VCEIL=([\d.]+), VRATE=([\d.]+);", ui())
    assert m, "the scale constants moved"
    return tuple(float(x) for x in m.groups())


def pos(v):
    lo, _, hi = consts()
    return (v - lo) / (hi - lo) * 100


class ScaleTest(unittest.TestCase):

    def test_the_ceiling_is_the_measured_one(self):
        """4.175 V/cell, read off the hardware at the CV plateau on two
        charges, not a datasheet figure."""
        _, ceil, _ = consts()
        self.assertAlmostEqual(ceil, 4.175, places=3)

    def test_the_track_ends_at_the_pack_rating(self):
        """17800 mV / 4 cells. Ending the track at the ceiling instead would
        hide exactly what the bar exists to show."""
        _, _, rate = consts()
        self.assertAlmostEqual(rate, 4.45, places=3)

    def test_the_ceiling_leaves_visible_headroom(self):
        _, ceil, _ = consts()
        self.assertGreater(pos(ceil), 70, "the gap would be too small to read")
        self.assertLess(pos(ceil), 90, "the gap would look like a full bar")

    def test_the_measured_extremes_land_on_the_track(self):
        """3.387 V/cell at the end of the discharge, 4.1753 at the CV
        plateau -- both from discharge-newpack.csv."""
        for v in (3.387, 4.1753):
            self.assertGreater(pos(v), 0)
            self.assertLess(pos(v), 100)

    def test_the_scale_labels_match_the_constants(self):
        """A caption that disagrees with the drawing is worse than none."""
        u = ui()
        i = u.index('class="vscale"')
        block = u[i:i + 220]
        lo, ceil, rate = consts()
        self.assertIn(f"{lo:.2f}", block)
        self.assertIn(str(ceil), block)
        self.assertIn(str(rate), block)


class MarkupTest(unittest.TestCase):

    def setUp(self):
        self.src = ui()

    def test_the_fill_and_both_markers_exist(self):
        for el in ('id="vfill"', 'id="vceil"', 'id="vrate"'):
            self.assertIn(el, self.src)

    def test_the_markers_are_placed_from_the_same_function_as_the_fill(self):
        """Hard-coding 81% would drift the moment the ceiling is remeasured."""
        self.assertIn("vc.style.left=vpos(VCEIL)", self.src)

    def test_the_track_is_not_clipped(self):
        """.bar sets overflow:hidden, which would swallow markers that sit
        proud of the track."""
        i = self.src.index(".vbar{")
        self.assertNotIn("overflow:hidden", self.src[i:i + 200])

    def test_it_warns_above_the_ceiling(self):
        self.assertIn("var(--color-warn)", self.src[self.src.index("vf.style.background"):][:160])


class LoadNoteTest(unittest.TestCase):
    """Terminal voltage is not open-circuit voltage. Without saying so, the
    bar jumping when a charger is plugged in looks like a bug."""

    def setUp(self):
        self.note = ui()[ui().index("if(vn)vn.textContent"):][:900]

    def test_it_explains_a_discharge_reading_low(self):
        self.assertIn("reads low", self.note)
        self.assertIn("resistance", self.note)

    def test_it_explains_a_charge_reading_high(self):
        self.assertIn("reads high", self.note)

    def test_it_says_when_the_reading_is_trustworthy(self):
        self.assertIn("open-circuit", self.note)


if __name__ == "__main__":
    unittest.main()
