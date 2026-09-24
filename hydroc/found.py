# SPDX-License-Identifier: MIT
"""What the hardware was holding before we wrote to it.

`hydroc-apply.service` runs at boot and `hydroc-resume.service` after every
resume, and both write stored intent into the hardware. That is correct and it
is the reason this project exists -- almost nothing here survives a power cycle
(HANDOFF, "Almost nothing persists"). But it means every observation anyone has
ever taken after a boot was taken *after* we had already overwritten whatever
the hardware came up with.

That made a real question unanswerable. Asked on 2026-09-24 whether the charging
profile at 0x07A6 persists across a reboot, the honest answer was that we could
not tell, because our own boot service writes it before anything reads it. When
the profile was seen back on Stationary after a reboot, attributing that to
"the daemon re-applied it" was a guess; the EC could equally have held it.

So: read everything first, record it, then apply as before. One extra read pass,
no change to what gets written, and the question becomes permanently answerable.
A disagreement between what we found and what we intended is not noise -- it is
the same signal `drift` reports at runtime (HANDOFF, "Intent and state are
different things"), which has twice been mistaken for a bug rather than read as
the diagnosis. The difference here is that at boot it also tells us which
settings are volatile and which are not, and that is the only way to know which
ones should be *read* rather than applied, the way GPU mode already is.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime

STATE_DIR = "/var/lib/hydroc"
FOUND_PATH = os.path.join(STATE_DIR, "found-at-boot.json")

# Keys that are live readings rather than settings. Comparing them against
# stored intent would report a difference on every boot and teach everyone to
# ignore the whole record.
IGNORE = {"custom_profile"}


def differences(found: dict, intent: dict) -> list[dict]:
    """Settings where the hardware disagreed with what we last asked for.

    Only keys present in both are compared -- intent carries settings the
    hardware cannot report, and state carries readings that are not settings.
    Pure, so the decision can be tested without a machine.
    """
    out = []
    for key in sorted(intent):
        if key in IGNORE or key not in found:
            continue
        want, have = intent[key], found[key]
        if want != have:
            out.append({"setting": key, "intent": want, "found": have})
    return out


def record(found: dict, intent: dict, reason: str) -> dict:
    """Build the capture. Pure -- no clock, no disk, so tests can pin it."""
    return {
        "when": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "found": found,
        "differences": differences(found, intent),
    }


def write(rec: dict, path: str = FOUND_PATH) -> str | None:
    """Persist atomically. Returns the path, or None if it could not be saved.

    Never raises. This runs immediately before the boot-time apply, and a
    diagnostic that cannot be written must not stop settings being restored.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(rec, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def read(path: str = FOUND_PATH) -> dict | None:
    """The last capture, or None. Never raises."""
    try:
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def capture(hw, intent: dict, reason: str, path: str = FOUND_PATH) -> dict:
    """Read the hardware and save what it was holding. Never raises.

    Returns the record even when it could not be written, so a caller can still
    report the difference it found.
    """
    try:
        found = hw.read_state()
    except Exception:                                    # noqa: BLE001
        found = {}
    rec = record(found, intent, reason)
    rec["saved_to"] = write(rec, path)
    return rec
