# SPDX-License-Identifier: MIT
"""What build is this?

A tester bug report is only actionable if you know which code produced it. The
bundle used to be an unlabelled tarball, so "are you on the build with the fix?"
was guesswork -- exactly the question version control exists to answer, thrown
away at the last step.

Two situations, one answer:

  * a git checkout (development) -- derive it from git, live
  * an unpacked bundle (a tester) -- read the stamp make-bundle.sh wrote, since
    there is no git there

Neither is a failure: an unknown build is reported as unknown rather than
guessed at. A tree with uncommitted changes is marked `-dirty`, because a build
from one is not reproducible and a report against it cannot be trusted to match
the commit it names.
"""

import json
import os
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP = os.path.join(_ROOT, "build.json")


def _from_stamp() -> dict | None:
    try:
        with open(STAMP, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("commit") else None


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(("git", "-C", _ROOT, *args),
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _from_git() -> dict | None:
    commit = _git("rev-parse", "--short=12", "HEAD")
    if not commit:
        return None
    dirty = bool(_git("status", "--porcelain"))
    return {"commit": commit,
            "dirty": dirty,
            "date": _git("log", "-1", "--format=%cs") or "",
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "",
            "source": "git"}


_UNKNOWN = {"commit": "unknown", "dirty": False, "date": "", "branch": "",
            "source": "unknown"}


def build_info() -> dict:
    """{commit, dirty, date, branch, source}. Never raises.

    doctor() calls this before anything else, so a broken stamp or a missing
    git must degrade to "unknown" rather than take the health check down with
    it. The individual readers already guard their own failure modes; this
    catch-all is what makes the promise in that first sentence true rather than
    merely intended.
    """
    try:
        info = _from_stamp()
        if info:
            info.setdefault("source", "bundle")
            info.setdefault("dirty", False)
            return info
        info = _from_git()
        if info:
            return info
    except Exception:                              # noqa: BLE001
        pass
    return dict(_UNKNOWN)


def build_id() -> str:
    """One line, safe to paste into a bug report."""
    i = build_info()
    if i["commit"] == "unknown":
        return "unknown build (no git checkout and no build stamp)"
    parts = [i["commit"]]
    if i.get("dirty"):
        parts.append("-dirty")
    tail = " ".join(x for x in (i.get("date", ""), f"({i['source']})") if x)
    return f"{''.join(parts)} {tail}".strip()


if __name__ == "__main__":
    print(build_id())
