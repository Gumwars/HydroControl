# SPDX-License-Identifier: MIT
"""Root scripts must not be a softer way to do a dangerous thing.

Two of them had drifted into exactly that.

gpu_mode.py kept its own copy of the EFI variable handling and its own idea
of when a write is allowed:

    gpu_mode.py      if n and not accept_risks:  refuse
    hydroc.gpumode   if not confirm:             refuse, always

A mode change whose preflight came back clean therefore reached non-volatile
firmware with nothing asked, while the same change through the package or the
UI could not. The gate that was missing is the one for an accidental call,
which is the likelier mistake of the two.

fan_characterise.py polled EC registers for fan state during a deliberate
heat soak. DESIGN.md 4.2 records what that does: starving the EC of its 6 ms
between accesses stopped the fans at 0 rpm. fan_characterise_safe.py reads
hwmon instead and adds a monitor thread, a panic key and a duty floor. Two
scripts for one job, and the one without the safety net was the shorter name.
"""

import ast
import os
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def source(name):
    with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class GpuModeCliTest(unittest.TestCase):

    def setUp(self):
        self.src = source("gpu_mode.py")

    def test_it_delegates_rather_than_reimplementing(self):
        self.assertIn("from hydroc.gpumode import", self.src)

    def test_it_does_not_write_efi_variables_itself(self):
        """The duplicated write path is what allowed the gates to differ."""
        for marker in ("efivarfs", "def write_var", "immutable", "FS_IOC_SETFLAGS"):
            self.assertNotIn(marker, self.src,
                             f"{marker}: still writing firmware directly")

    def test_every_call_to_set_mode_passes_confirm(self):
        """Parsed, not grepped: a confirm=False slipping in later should
        fail here rather than in someone's firmware."""
        calls = [n for n in ast.walk(ast.parse(self.src))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "set_mode"]
        self.assertTrue(calls, "no set_mode call found")
        for c in calls:
            kw = {k.arg: k.value for k in c.keywords}
            self.assertIn("confirm", kw)
            self.assertIs(getattr(kw["confirm"], "value", None), True)

    def test_there_is_no_path_that_writes_without_asking(self):
        """Either an interactive confirmation or an explicit --yes."""
        self.assertIn("--yes", self.src)
        self.assertIn("if not args.yes", self.src)

    def test_accept_risks_is_not_the_only_thing_guarding_a_write(self):
        """The old shape was `if n and not accept_risks: refuse`, so a clean
        preflight skipped the check entirely.

        Checked against code with the docstrings removed -- this file's own
        docstring quotes the old line, and a substring search reports the
        explanation as the offence."""
        tree = ast.parse(self.src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                body = node.body
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body.pop(0)
        code = ast.unparse(tree)
        self.assertNotIn("accept_risks", code.split("set_mode")[0],
                         "accept_risks is gating something before set_mode; "
                         "the package decides that, not the CLI")
        self.assertIn("acknowledge_risks=args.accept_risks", code)


class FanCharacteriseTest(unittest.TestCase):

    def test_only_the_monitored_variant_remains(self):
        self.assertFalse(
            os.path.exists(os.path.join(_ROOT, "fan_characterise.py")),
            "the unmonitored variant is back; it polls the EC for fan state "
            "during a heat soak, which DESIGN.md 4.2 records as stopping the "
            "fans")
        self.assertTrue(
            os.path.exists(os.path.join(_ROOT, "fan_characterise_safe.py")))

    def test_the_survivor_reads_rpm_through_the_driver(self):
        src = source("fan_characterise_safe.py")
        self.assertIn("find_hwmon", src)

    def test_the_survivor_can_put_the_fans_back(self):
        self.assertIn("def restore_fans", source("fan_characterise_safe.py"))


if __name__ == "__main__":
    unittest.main()
