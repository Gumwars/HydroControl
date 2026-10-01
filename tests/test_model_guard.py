# SPDX-License-Identifier: MIT
"""Which DMI fields identify this machine.

The guard read board_name and nothing else, and board_name is the single
least reliable field on this laptop. Prema Mod rewrites it to "HYDROC-16
powered by premamod.com"; a BIOS flash clears the DMI strings until they are
stamped back, and the vendor package ships AMIDEWIN tools for exactly that.

So a legitimate vendor firmware update could have left the daemon refusing to
run on the machine it was written for, with a message saying it was the wrong
model. Any of the three identifiers now satisfies it.

What must NOT change: sys_vendor still has to be ELUKTRONICS. The guard exists
because EC register layouts differ between chassis, and writing HYDROC-16
addresses elsewhere could set anything at all.
"""

import os
import unittest
from unittest import mock

from hydroc import cli

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def dmi(**fields):
    """Stand in for /sys/class/dmi/id/<field>."""
    def read(path):
        return fields.get(os.path.basename(path), "")
    return mock.patch.object(cli, "_first_line", side_effect=read)


def supported(**fields):
    """diagnose() runs every check; only the model row is under test here,
    and a MagicMock satisfies the hardware the others ask about."""
    with dmi(**fields):
        rows = cli.diagnose(mock.MagicMock())
    row = next(r for r in rows if r["name"] == "supported model")
    return row["ok"]


class GuardTest(unittest.TestCase):

    def test_the_prema_board_name_is_accepted(self):
        """What this machine reads today."""
        self.assertTrue(supported(
            sys_vendor="ELUKTRONICS",
            board_name="HYDROC-16 powered by premamod.com",
            product_name="HYDROC-16", product_sku="HYDROC-16 G1"))

    def test_a_blank_board_name_after_a_flash_is_accepted(self):
        """The case this was written for: DMI cleared, product fields intact
        or restored first."""
        self.assertTrue(supported(
            sys_vendor="ELUKTRONICS", board_name="",
            product_name="HYDROC-16", product_sku="HYDROC-16 G1"))

    def test_a_tongfang_board_code_with_eluktronics_product_is_accepted(self):
        """If the stock BIOS stamps the ODM's own board code."""
        self.assertTrue(supported(
            sys_vendor="ELUKTRONICS", board_name="GM6IX9B",
            product_name="HYDROC-16", product_sku="HYDROC-16 G1"))

    def test_only_the_sku_surviving_is_enough(self):
        self.assertTrue(supported(
            sys_vendor="ELUKTRONICS", board_name="", product_name="",
            product_sku="HYDROC-16 G1"))

    def test_another_vendor_is_still_refused(self):
        """The hard stop. Same chassis family, different brand, different EC
        stamping -- this is what the guard is for."""
        self.assertFalse(supported(
            sys_vendor="SchenkerTechnologiesGmbH", board_name="HYDROC-16",
            product_name="HYDROC-16", product_sku="HYDROC-16 G1"))

    def test_an_eluktronics_machine_that_is_not_a_hydroc_is_refused(self):
        self.assertFalse(supported(
            sys_vendor="ELUKTRONICS", board_name="MECH-15-G3R",
            product_name="MECH-15", product_sku="MECH-15 G3R"))

    def test_completely_blank_dmi_is_refused(self):
        """Absence is not identification."""
        self.assertFalse(supported(
            sys_vendor="", board_name="", product_name="", product_sku=""))


class InstallerAgreesTest(unittest.TestCase):
    """install.sh runs before anything is installed and must not disagree
    with the daemon about what machine this is."""

    def setUp(self):
        with open(os.path.join(_ROOT, "install.sh"), encoding="utf-8") as fh:
            self.src = fh.read()

    def test_it_checks_all_three_fields(self):
        for f in ("board_name", "product_name", "product_sku"):
            self.assertIn(f, self.src)

    def test_it_still_requires_the_vendor(self):
        self.assertIn('"$VENDOR" == "ELUKTRONICS"', self.src)

    def test_it_mentions_re_stamping(self):
        """Otherwise the failure after a firmware flash reads as 'wrong
        laptop', which is the one thing it is not."""
        self.assertIn("re-stamping", self.src)


if __name__ == "__main__":
    unittest.main()
