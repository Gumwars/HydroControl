#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify a built bundle is complete and runnable, before it reaches a tester.

`make-bundle.sh` is a hand-maintained file list, and two of its copy loops use
`[[ -e ]] && copy`, so a module that is added but not listed -- or a source file
that is renamed -- is skipped *silently*. The bundle then imports fine and fails
later, at runtime, on someone else's machine: hydroc/deps.py and hydroc/kmod.py
shipped missing exactly this way, and `doctor` died with ModuleNotFoundError
because both are imported inside diagnose() rather than at module level.

So checking imports at module scope is not enough. This runs three passes:

  1. static   -- every intra-package import in the bundle, at any nesting depth,
                 resolves to a file that is actually present
  2. compile  -- every bundled .py parses
  3. runtime  -- the entry points actually execute, which is the only thing that
                 exercises imports deferred inside functions

Failing checks are fine (an unprivileged build machine cannot reach the EC); a
traceback is not. That is the distinction this asserts.

    python3 bundle_smoke.py <bundle-dir>
"""

import ast
import os
import subprocess
import sys

# Packages the bundle ships. Imports of anything else are somebody else's
# problem and are deliberately not checked here.
OWN_PACKAGES = ("hydroc", "kbctrl")

# install.sh copies these out of the bundle to build the module. make-bundle.sh
# skips missing ones without complaint, so assert them explicitly.
REQUIRED_MODULE_SRC = ("uniwill-acpi.c", "uniwill-wmi.c", "uniwill-wmi.h",
                       "Makefile", "dkms.conf")

failures: list[str] = []


def fail(check: str, detail: str) -> None:
    failures.append(f"{check}: {detail}")
    print(f"  FAIL  {check}\n        {detail}")


def ok(check: str, detail: str = "") -> None:
    print(f"  ok    {check}{'  ' + detail if detail else ''}")


def _module_targets(path: str, bundle: str):
    """Yield dotted module names imported by `path` that belong to us."""
    rel = os.path.relpath(path, bundle)
    pkg_parts = os.path.dirname(rel).split(os.sep)

    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)

    # ast.walk reaches imports nested inside functions and try blocks, which is
    # the case that actually broke.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:                      # relative: from .kmod import x
                base = pkg_parts[:len(pkg_parts) - node.level + 1]
                if node.module:
                    yield ".".join(base + node.module.split("."))
                else:                           # from . import kmod
                    for a in node.names:
                        yield ".".join(base + [a.name])
            elif node.module and node.module.split(".")[0] in OWN_PACKAGES:
                yield node.module
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in OWN_PACKAGES:
                    yield a.name


def check_imports(bundle: str) -> None:
    pyfiles = [os.path.join(r, f)
               for r, _, fs in os.walk(bundle)
               for f in fs if f.endswith(".py")]
    missing = set()
    checked = 0

    for path in pyfiles:
        try:
            targets = list(_module_targets(path, bundle))
        except (SyntaxError, UnicodeDecodeError) as e:
            fail("import scan", f"{os.path.relpath(path, bundle)}: {e}")
            continue
        for dotted in targets:
            checked += 1
            if dotted.split(".")[0] not in OWN_PACKAGES:
                continue
            # Callers add <bundle>/kbctrl to sys.path before importing
            # kbctrl.hardware, so that directory is a package root too.
            roots = (bundle, os.path.join(bundle, "kbctrl"))
            stems = [os.path.join(r, *dotted.split(".")) for r in roots]
            if not any(os.path.isfile(st + ".py")
                       or os.path.isfile(os.path.join(st, "__init__.py"))
                       for st in stems):
                missing.add(f"{dotted}  (imported by "
                            f"{os.path.relpath(path, bundle)})")

    if missing:
        for m in sorted(missing):
            fail("missing module", m)
    else:
        ok("intra-package imports", f"{checked} resolved across {len(pyfiles)} files")


def check_compiles(bundle: str) -> None:
    r = subprocess.run([sys.executable, "-m", "compileall", "-q", bundle],
                       capture_output=True, text=True)
    if r.returncode != 0:
        fail("compileall", (r.stdout + r.stderr).strip()[:400])
    else:
        ok("every .py parses")
    # compileall leaves caches the bundle should not ship with.
    subprocess.run(["find", bundle, "-name", "__pycache__", "-type", "d",
                    "-exec", "rm", "-rf", "{}", "+"], capture_output=True)


def check_runs(bundle: str) -> None:
    """Execute the entry points. Non-zero exit is fine; a traceback is not."""
    env = dict(os.environ, PYTHONPATH=bundle, PYTHONDONTWRITEBYTECODE="1")
    for label, args in (
            ("hydroc.cli doctor",   ["-m", "hydroc.cli", "doctor"]),
            ("hydroc.deps check",   ["-m", "hydroc.deps", "check", "bleak"]),
            ("hydroc.kmod check",   ["-m", "hydroc.kmod", "check",
                                     "uniwill-laptop"]),
    ):
        r = subprocess.run([sys.executable, *args], cwd=bundle, env=env,
                           capture_output=True, text=True, timeout=120)
        blob = r.stdout + r.stderr
        if ("Traceback" in blob or "ModuleNotFoundError" in blob
                or "No module named" in blob):
            tail = [l for l in blob.strip().splitlines() if l.strip()][-1:]
            fail(label, tail[0] if tail else "traceback")
        else:
            ok(label, f"exit {r.returncode}, no traceback")


def check_stamp(bundle: str) -> None:
    """An unlabelled bundle makes a tester's report unattributable."""
    import json
    path = os.path.join(bundle, "build.json")
    if not os.path.isfile(path):
        fail("build stamp", "build.json missing -- the bundle is unidentifiable")
        return
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        fail("build stamp", f"build.json unreadable: {e}")
        return
    if not data.get("commit") or data["commit"] == "unknown":
        fail("build stamp", "build.json names no commit")
        return
    ok("build stamp", data["commit"] + (" (dirty)" if data.get("dirty") else ""))


def check_installer(bundle: str) -> None:
    sh = os.path.join(bundle, "install.sh")
    if not os.path.isfile(sh):
        fail("install.sh", "not in the bundle")
        return
    r = subprocess.run(["bash", "-n", sh], capture_output=True, text=True)
    if r.returncode != 0:
        fail("install.sh syntax", r.stderr.strip()[:300])
    else:
        ok("install.sh parses")

    for f in REQUIRED_MODULE_SRC:
        if not os.path.isfile(os.path.join(bundle, "uniwill-laptop", f)):
            fail("kernel module source", f"uniwill-laptop/{f} missing "
                                         "(install.sh copies it to build DKMS)")
    if not failures:
        ok("kernel module sources", f"all {len(REQUIRED_MODULE_SRC)} present")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <bundle-dir>", file=sys.stderr)
        return 64
    bundle = os.path.abspath(sys.argv[1])
    if not os.path.isdir(bundle):
        print(f"no such bundle: {bundle}", file=sys.stderr)
        return 64

    print(f"smoke-testing {bundle}")
    check_imports(bundle)
    check_compiles(bundle)
    check_stamp(bundle)
    check_installer(bundle)
    check_runs(bundle)

    print()
    if failures:
        print(f"BUNDLE IS BROKEN -- {len(failures)} problem(s). Not shippable.")
        return 1
    print("bundle OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
