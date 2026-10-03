# SPDX-License-Identifier: MIT
"""One sysfs reader, and the distinction it exists to preserve.

Eleven root scripts each wrote their own, in four spellings with two
different failure sentinels. There was no bug in any of them -- unlike the
ACPI readers, which were all wrong the same way -- so this is tidying, with
one exception worth stating.

The exception: unreadable must not become zero. On this machine 0 RPM is a
real fan state, not a missing reading (DESIGN.md 4.2 records the fans
actually stopping), and 0 mA is a real charge current. A reader that returns
0 when the file is absent makes a monitor confidently report the thing it
exists to detect. read_int() returns None, and these tests hold it there.
"""

import os
import re
import unittest

from hydroc.hardware import battery_int, battery_text, read_int, read_text

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AbsenceIsNotZeroTest(unittest.TestCase):

    MISSING = "/nonexistent/hydroc-test/value"

    def test_read_text_returns_none(self):
        self.assertIsNone(read_text(self.MISSING))

    def test_read_int_returns_none_not_zero(self):
        v = read_int(self.MISSING)
        self.assertIsNone(v)
        self.assertNotEqual(v, 0)

    def test_unparseable_contents_are_none(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("not a number\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(read_text(path), "not a number")
        self.assertIsNone(read_int(path))

    def test_a_real_value_round_trips(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("  4200 \n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(read_text(path), "4200")
        self.assertEqual(read_int(path), 4200)

    def test_zero_is_preserved_as_zero(self):
        """The other half: a genuine 0 must not come back as None, or a
        stopped fan reads as a missing sensor."""
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write("0\n")
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(read_int(path), 0)
        self.assertIsNotNone(read_int(path))

    def test_battery_helpers_take_an_explicit_base(self):
        self.assertIsNone(battery_text("capacity", "/nonexistent/bat"))
        self.assertIsNone(battery_int("capacity", "/nonexistent/bat"))


class NoHandRolledHwmonDiscoveryTest(unittest.TestCase):
    """find_hwmon() resolves by name over a SORTED glob. The copies in the
    probe scripts did not sort, so with more than one matching node the
    answer could differ between runs."""

    @staticmethod
    def offenders():
        bad = []
        for name in sorted(os.listdir(_ROOT)):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
                src = fh.read()
            # An unsorted glob of hwmon paths, outside the package helper.
            for m in re.finditer(r'glob\.glob\(\s*["\']/sys/class/hwmon', src):
                line = src[:m.start()].count("\n") + 1
                if "sorted(" not in src[max(0, m.start() - 12):m.start()]:
                    bad.append(f"{name}:{line}")
        return bad

    def test_no_script_rediscovers_hwmon_itself(self):
        bad = self.offenders()
        self.assertEqual(
            bad, [],
            "use find_hwmon() from hydroc.hardware, which sorts: "
            + ", ".join(bad))

    def test_the_package_helper_sorts(self):
        import inspect
        from hydroc import hardware
        self.assertIn("sorted(", inspect.getsource(hardware.find_hwmon))


class SeamsPreservedTest(unittest.TestCase):
    """charge_ctrl_watch's sysfs/sysfs_int are how the tests inject
    readings. Replacing the calls at the use sites would have removed the
    seam along with the duplication."""

    def test_the_patched_names_still_exist(self):
        with open(os.path.join(_ROOT, "charge_ctrl_watch.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("def sysfs(", src)
        self.assertIn("def sysfs_int(", src)


if __name__ == "__main__":
    unittest.main()
