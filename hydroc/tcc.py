# SPDX-License-Identifier: MIT
"""
hydroc.tcc — what TCC offset the CPU is actually running, read from the kernel.

The TCC offset lowers the temperature at which the CPU throttles itself: it
throttles at TjMax minus the offset. Control Center sets it through the EC, at
0x0786 (hydroc/ec.py), and never touches the CPU. The CPU holds the value that
is really in force, in MSR_TEMPERATURE_TARGET bits 29:24, and the kernel's
intel_tcc_cooling driver exposes that as a cooling device of type
"TCC Offset". The driver only registers when the offset is programmable and
unlocked, so its presence is itself a capability check.

This module only reads. The setting goes through the EC, as Windows does it;
comparing the two is how tcc_survival_probe.py tells what the EC enforces.

    /sys/class/thermal/cooling_device*/type       "TCC Offset"
    /sys/class/thermal/cooling_device*/cur_state  offset in degrees
    coretemp hwmon temp1_crit                      TjMax, millidegrees
"""

from __future__ import annotations

import glob
import os

THERMAL = "/sys/class/thermal"
HWMON = "/sys/class/hwmon"
DEVICE_TYPE = "TCC Offset"


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def device(root: str = THERMAL) -> str | None:
    """The intel_tcc_cooling device's directory, or None without one."""
    for d in sorted(glob.glob(os.path.join(root, "cooling_device*"))):
        if _read(os.path.join(d, "type")) == DEVICE_TYPE:
            return d
    return None


def applied(root: str = THERMAL) -> int | None:
    """The offset the CPU is running now, in degrees below TjMax."""
    d = device(root)
    v = _read(os.path.join(d, "cur_state")) if d else None
    return int(v) if v and v.isdigit() else None


def maximum(root: str = THERMAL) -> int | None:
    d = device(root)
    v = _read(os.path.join(d, "max_state")) if d else None
    return int(v) if v and v.isdigit() else None


def tjmax(root: str = HWMON) -> int | None:
    """TjMax in degrees, from coretemp's package sensor."""
    for h in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        if _read(os.path.join(h, "name")) != "coretemp":
            continue
        v = _read(os.path.join(h, "temp1_crit"))
        if v and v.lstrip("-").isdigit():
            return int(v) // 1000
    return None
