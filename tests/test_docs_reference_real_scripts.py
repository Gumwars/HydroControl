# SPDX-License-Identifier: MIT
"""Anything the project tells you to run has to exist.

Retiring five lightbar probes broke two places that named one of them.
install.sh printed "sudo python3 lb_mode_probe.py" as its closing
instruction, so the last thing a successful install said was a command that
fails. make-bundle.sh listed it too, guarded by [[ -e ]], so it silently
shipped one fewer diagnostic than intended.

Neither is caught by running the test suite, by shellcheck, or by anything
else: they are strings. The deletions were right; not grepping for references
was not.

Scope is deliberately commands, not prose. HANDOFF.md and DESIGN.md name
these scripts while recounting what was learned from them, and that history
is still true -- rewriting it to hide that a tool once existed would be worse
than a dead link. Git has the scripts.
"""

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(_ROOT, name), encoding="utf-8") as fh:
        return fh.read()


def scripts_in(text):
    """`python3 something.py` -- the form a reader will copy and paste."""
    return set(re.findall(r'python3?\s+([A-Za-z0-9_./-]+\.py)', text))


def bash_blocks(md):
    return "\n".join(re.findall(r'```(?:bash|sh|console)\n(.*?)```', md, re.S))


class ExistsTest(unittest.TestCase):

    def check(self, where, text):
        missing = sorted(s for s in scripts_in(text)
                         if not os.path.exists(os.path.join(_ROOT, s)))
        self.assertEqual(missing, [],
                         f"{where} tells you to run: {', '.join(missing)}")

    def test_the_installer_only_names_scripts_that_exist(self):
        """Its closing message is the last thing a successful install says."""
        self.check("install.sh", read("install.sh"))

    def test_the_bundler_only_ships_scripts_that_exist(self):
        """Guarded by [[ -e ]], so a stale name is silent -- the bundle just
        comes out short."""
        src = read("make-bundle.sh")
        # Every for-loop, not the first: the file opens with
        # `for f in "$DIR"/hydroc/*.py`, a glob with no literal names, which
        # a non-greedy match finds and reports as an empty list.
        names = []
        for block in re.findall(r'for f in (.*?); do', src, re.S):
            if "*" in block:
                continue
            names += re.findall(r'(?<![/\w])([A-Za-z0-9_-]+\.py)', block)
        self.assertTrue(names, "the diagnostics list moved")
        missing = [n for n in names
                   if not os.path.exists(os.path.join(_ROOT, n))]
        self.assertEqual(missing, [], f"make-bundle.sh lists: {missing}")

    def test_readme_commands_name_scripts_that_exist(self):
        """Code blocks only. Prose may name a retired tool while explaining
        what it established."""
        self.check("README.md (bash blocks)", bash_blocks(read("README.md")))

    def test_the_retired_probes_are_not_offered_anywhere(self):
        retired = ("fan_characterise.py", "lb_effect_probe.py",
                   "lb_effect_sweep2.py", "lb_mode_probe.py",
                   "lb_output_probe.py", "lb_sequence_test.py")
        offered = set()
        for name in ("install.sh", "make-bundle.sh"):
            offered |= scripts_in(read(name))
        offered |= scripts_in(bash_blocks(read("README.md")))
        back = sorted(set(retired) & offered)
        self.assertEqual(back, [], f"retired and still offered: {back}")

    def test_the_detector_would_notice(self):
        """A guard that cannot fail protects nothing."""
        self.assertIn("ghost_script.py",
                      scripts_in("run `sudo python3 ghost_script.py` now"))


class SurvivorsTest(unittest.TestCase):
    """The two kept deliberately, because they still do something rather
    than having answered a question once."""

    def test_the_kept_lightbar_tools_are_still_here(self):
        for name in ("lb_set_color.py", "lb_user_mode.py"):
            self.assertTrue(os.path.exists(os.path.join(_ROOT, name)), name)

    def test_the_monitored_fan_tool_is_still_here(self):
        self.assertTrue(
            os.path.exists(os.path.join(_ROOT, "fan_characterise_safe.py")))


if __name__ == "__main__":
    unittest.main()
