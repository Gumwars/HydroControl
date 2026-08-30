# SPDX-License-Identifier: MIT
"""Is it safe to unload this module right now?

On a rolling distro a kernel upgrade replaces `/lib/modules/<version>` while
the old kernel is still running. Everything keeps working -- the modules are
already resident -- but there is no longer a file on disk to load them from,
and DKMS has built for the kernel you will boot *next*, not the one you are on.

That opens a window in which `modprobe -r uniwill-laptop` succeeds and the
matching `modprobe` cannot, leaving the machine with no driver and `0x0741`
bit 0 cleared (DESIGN.md §4.1) until a reboot. Both reload paths -- the doctor
Repair action and `install.sh` -- unloaded first and discovered the problem
afterwards, which is the one ordering that cannot be undone.

This is distinct from the stale-`.ko`-shadowing-DKMS trap already recorded in
HANDOFF: there the mitigation is DKMS, and here DKMS is precisely what does not
help, because it built for a kernel that is not running.

    python3 -m hydroc.kmod check uniwill-laptop   # 0 = safe, 1 = unsafe
"""

import os
import subprocess
import sys

MODULES_DIR = "/lib/modules"


def running_kernel() -> str:
    return os.uname().release


def module_tree() -> str:
    return os.path.join(MODULES_DIR, running_kernel())


def module_tree_present() -> bool:
    """Does the running kernel still have its module tree on disk?"""
    return os.path.isdir(module_tree())


def module_file(name: str) -> str | None:
    """Path to `name`'s module file for the RUNNING kernel, or None."""
    try:
        r = subprocess.run(["modinfo", "-n", name],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    path = r.stdout.strip()
    return path if r.returncode == 0 and path else None


def reboot_pending() -> bool:
    """True when the running kernel's modules have been removed underneath it.

    The signature of a kernel upgrade applied while this kernel was running.
    """
    return not module_tree_present()


def safe_to_reload(name: str) -> tuple[bool, str]:
    """(ok, reason). Never unload a module this returns False for."""
    kernel = running_kernel()

    if not module_tree_present():
        return False, (
            f"the running kernel ({kernel}) has no module tree at "
            f"{module_tree()} -- its package was upgraded since boot. "
            f"Unloading {name} now would leave no driver until you reboot.")

    if module_file(name) is None:
        return False, (
            f"no {name} module file for the running kernel ({kernel}). "
            f"Unloading it now would leave no driver until you reboot.")

    return True, f"{name} is present for {kernel}"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "check":
        ok, reason = safe_to_reload(argv[1])
        print(reason, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    print(f"usage: {sys.argv[0]} check <module>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main())
