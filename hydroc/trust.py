# SPDX-License-Identifier: MIT
"""Is a path safe to hand to root?

Code root runs must be code only root can change. A checkout in someone's
home fails that however it is invoked -- systemd, sudo or a pkexec prompt --
because whatever can write to the checkout can rewrite what root is about to
execute, and a genuine password dialog then approves the result.

kbctrl.hardware carries its own copy of these two checks, because kbctrl is
also used without this package.
"""

from __future__ import annotations

import os
import stat


def root_only(path: str) -> bool:
    """True if `path` and every directory above it is root's alone."""
    try:
        p = os.path.realpath(path)
        while True:
            st = os.stat(p)
            if st.st_uid != 0 or st.st_mode & 0o022:
                return False
            up = os.path.dirname(p)
            if up == p:
                return True
            p = up
    except OSError:
        return False


def tree_root_only(top: str) -> bool:
    """root_only(top), and nothing under it is a symlink or non-root's."""
    if not root_only(top):
        return False
    for d, dirs, files in os.walk(top):
        for name in dirs + files:
            try:
                st = os.lstat(os.path.join(d, name))
            except OSError:
                return False
            if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                return False
    return True
