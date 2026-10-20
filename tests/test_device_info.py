# SPDX-License-Identifier: MIT
"""The title bar's device line is read from DMI, never assumed."""

import os
import tempfile
import unittest

from hydroc.hardware import device_info


def _put(d, name, value):
    with open(os.path.join(d, name), "w") as fh:
        fh.write(value + "\n")


class DeviceInfoTest(unittest.TestCase):

    def test_reads_what_the_firmware_reports(self):
        with tempfile.TemporaryDirectory() as d:
            _put(d, "product_sku", "HYDROC-16 G1")
            _put(d, "ec_firmware_release", "1.17")
            _put(d, "bios_version", "N.1.09ELUK")
            info = device_info(d)
        self.assertEqual(info["model"], "HYDROC-16 G1")
        self.assertEqual(info["ec_firmware"], "1.17")
        self.assertEqual(info["bios"], "N.1.09ELUK")
        self.assertTrue(info["kernel"])

    def test_falls_back_to_product_name(self):
        with tempfile.TemporaryDirectory() as d:
            _put(d, "product_name", "HYDROC-16")
            self.assertEqual(device_info(d)["model"], "HYDROC-16")

    def test_missing_fields_are_none_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            _put(d, "product_sku", "")                      # blank after a flash
            info = device_info(d)
        self.assertIsNone(info["model"])
        self.assertIsNone(info["ec_firmware"])


if __name__ == "__main__":
    unittest.main()
