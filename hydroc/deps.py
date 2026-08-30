# SPDX-License-Identifier: MIT
"""Can the root daemon import this? -- one answer, used everywhere.

Three surfaces used to ask this question independently and disagree about it:

  * `install.sh` imported the library in whatever shell ran the installer
  * `hydroc.cli.diagnose` asked whether *this* process could reach the hardware
  * `hydroc.lppd` asked at runtime, as root under systemd, and printed its own
    remedy text

Only the third asks the question that matters. The daemons run as root under
systemd, so "can the caller import it" is a different question with a different
answer, and answering the wrong one told a tester their library was fine when
the sidecar could not load it. It also let `install.sh` recommend a plain
`pip install`, which lands in the caller's ~/.local where root cannot see it --
the same launcher-dependence trap as DESIGN.md §4.4, third occurrence.

So: probe in a subprocess that reproduces the daemon's environment (root,
HOME=/root, no inherited PYTHONPATH or XDG_*/SUDO_USER), and keep the remedy
text here so no two surfaces can drift apart again.

The result is deliberately three-valued. An unprivileged caller gets `unknown`,
never `False` -- DESIGN.md §4.3.

    python3 -m hydroc.deps check bleak     # 0 = ok, 1 = missing, 2 = unknown
    python3 -m hydroc.deps list
"""

import os
import subprocess
import sys
from dataclasses import dataclass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# systemd gives a service almost nothing. Reproducing that is the whole point:
# inheriting PYTHONPATH or the caller's HOME is what made the old checks lie.
_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class Dep:
    label: str
    probe: str          # python source; exit 0 = present, prints a detail line
    remedy: str
    optional: bool = True


DEPS: dict[str, Dep] = {
    "bleak": Dep(
        label="bleak (LPP cooling dock)",
        probe=("import bleak, sys;"
               "sys.stdout.write(getattr(bleak, '__version__', 'present'))"),
        remedy=("Only needed if you own the LPP dock. Install it for ROOT:\n"
                "  sudo pacman -S python-bleak                      (Arch/CachyOS)\n"
                "  sudo pip install --break-system-packages bleak   (elsewhere)\n"
                "A plain 'pip install bleak' installs into your home directory,\n"
                "where the root sidecar cannot see it."),
    ),
    "ite8291r3-ctl": Dep(
        label="ite8291r3-ctl (keyboard RGB)",
        # Ask kbctrl itself rather than guessing at a path: it injects the pipx
        # venv site-packages of every home, and a check that reimplemented that
        # search would drift out of agreement with the daemon.
        probe=("import sys;"
               f"sys.path.insert(0, {os.path.join(_ROOT, 'kbctrl')!r});"
               "import kbctrl.hardware as h;"
               "sys.stdout.write(h.ite_version() if h.HAS_ITE else '');"
               "sys.exit(0 if h.HAS_ITE else 1)"),
        remedy=("Keyboard RGB only. Install as your own user, not root:\n"
                "  pipx install ite8291r3-ctl\n"
                "The daemon searches every home's pipx venv, so it is found\n"
                "wherever it lands -- but it must be installed somewhere."),
    ),
}


def _daemon_env() -> dict:
    """The environment systemd hands a service -- notably without PYTHONPATH."""
    return {"PATH": _SAFE_PATH,
            "HOME": "/root",
            "LANG": os.environ.get("LANG", "C.UTF-8")}


def check(name: str) -> dict:
    """Three-valued: ok True / False / None when we cannot know.

    Returns {name, label, ok, unknown, detail, remedy, optional}.
    """
    dep = DEPS[name]

    if os.geteuid() != 0:
        # Not a failure -- a question we are not in a position to answer.
        return {"name": name, "label": dep.label, "ok": None, "unknown": True,
                "detail": "not checked -- re-run as root to test the daemon's view",
                "remedy": None, "optional": dep.optional}

    try:
        r = subprocess.run([sys.executable, "-c", dep.probe],
                           capture_output=True, text=True,
                           env=_daemon_env(), timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"name": name, "label": dep.label, "ok": False, "unknown": False,
                "detail": f"probe failed to run: {e}", "remedy": dep.remedy,
                "optional": dep.optional}

    ok = r.returncode == 0
    if ok:
        detail = r.stdout.strip() or "present"
    else:
        err = r.stderr.strip().splitlines()
        detail = err[-1] if err else "not importable by root"

    return {"name": name, "label": dep.label, "ok": ok, "unknown": False,
            "detail": detail, "remedy": None if ok else dep.remedy,
            "optional": dep.optional}


def remedy(name: str) -> str:
    """The install advice for `name` -- single-sourced so surfaces agree."""
    return DEPS[name].remedy


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv[:1] == ["list"]:
        for n, d in DEPS.items():
            print(f"{n}\t{d.label}")
        return 0

    if len(argv) == 2 and argv[0] == "check" and argv[1] in DEPS:
        res = check(argv[1])
        if res["unknown"]:
            print(res["detail"], file=sys.stderr)
            return 2
        if res["ok"]:
            print(res["detail"])
            return 0
        print(res["detail"], file=sys.stderr)
        print(res["remedy"], file=sys.stderr)
        return 1

    print(f"usage: {sys.argv[0]} check <{'|'.join(DEPS)}> | list", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
