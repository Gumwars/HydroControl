#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Does a charging profile change the *termination voltage*?

We already know the profiles do not cap by percentage: a full cycle under
`Stationary` charged to a true 100%, `CHARGE_CTRL_REACHED` never armed, and
`charge_full` still equals `charge_full_design` -- so the documented behaviour
(cap at 80, report 100) is not merely ineffective, its mechanism is absent.

That is not the whole question. Percentage caps are one way to do battery care;
lowering the charge termination voltage is the other, and it is invisible to
every measurement above. If `Stationary` stopped at 4.10 V/cell instead of 4.23,
the gauge would rescale, report 100%, and look exactly like what we recorded --
while genuinely extending pack life. Before anyone says these profiles do
nothing, that has to be ruled out.

Our existing logs cannot rule it out. Across cycle.csv and battery-v.csv there
are 68 samples at >=99% capacity and *none* with current near zero. Under charge,
`voltage_now` is the charger's applied voltage, not the cell's (HANDOFF,
"Traps"), so every reading we have is unusable for this.

WHAT THIS DOES DIFFERENTLY -- no discharge between profiles.

Testing three profiles the obvious way needs three full cycles and three
discharges. It does not have to. Charge to full under the *lowest* profile, let
the current fall to zero, and record the resting voltage. Then raise the
profile. If the lower profile really did terminate lower, the charger has no
choice but to resume -- current rises, voltage climbs -- and that resume is
itself the answer, visible in minutes instead of days.

    no resume at either step, all three rest at the same voltage
        -> the profiles do not affect termination. They are inert.
    resume after raising the profile
        -> the lower profile does cap, by voltage rather than percentage,
           and "does nothing" is the wrong description.

Writes one EC register (the charging profile at 0x07A6, bits 5:4), which is
volatile EC state a power cycle undoes, and restores the profile it found on
the way out.

    sudo python3 charge_profile_probe.py -o charge-profiles.csv
    sudo python3 charge_profile_probe.py --status        # one sample, no writes
    sudo python3 charge_profile_probe.py --dry-run       # print the plan
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime

# One correct reader, not nine. Each of these scripts hand-rolled the
# /proc/acpi/call read as fh.read().strip().rstrip("\x00"), which cannot see
# a null in the MIDDLE of the buffer -- so a short reply followed by the tail
# of a longer previous one came through as e.g. '0x3e\x00alled'. hydroc.ec
# was fixed for this; these were the copies that were not.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hydroc.ec import parse_reply                                # noqa: E402

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

# 0x07A6 is shared: bits 5:4 are the charging profile, bit 1 is
# OVERBOOST_DYN_TEMP_OFF and bit 6 is TOUCHPAD_TOGGLE_OFF. Mask, never assign.
REG_OEM_4 = 0x07A6
PROFILE_MASK = 0x30
PROFILE_SHIFT = 4
REG_CHARGE_CTRL = 0x07B9        # recorded only, never written here

# 0x07C6 is sampled read-only, and it may matter more than 0x07B9 does.
# It is the same byte that holds ENABLE_UNIVERSAL_FAN_CTRL -- the switch that
# turned a feature we had written off as absent into a working one -- and two
# bits away it carries a battery status bit and a "full for 24 hours" flag that
# nothing in the driver reads or writes. Together with the WMI event
# UNIWILL_OSD_BAT_ERM_UPDATE (0xBF), which the keymap explicitly ignores, that
# is the shape of a battery-care subsystem which is NOT the 0x07B9 threshold.
# If the real mechanism lives here, a full charge cycle is when it would show.
# The whole byte is logged, not just the two known bits: reading the
# neighbourhood is what caught 0x0984 being unmapped rather than confirming.
REG_AP_OEM_6 = 0x07C6

# The footprint registers. Added 2026-09-28 after decoding the ceiling routine
# out of 117.ELUK and finding that it is present, identical to the G2 build,
# and that every guard it tests passes on this machine while the bit it exists
# to set never arms.
#
# That leaves two possibilities which look identical from outside: the routine
# never runs, or it runs and reads a different 0x07B9 than the one we read back.
# These three registers tell them apart, because the EC writes them itself.
#
# 0x0742 bit 2 is the decisive one. Immediately before the capacity comparison
# the firmware does, on every pass, one of:
#
#     90 07 42  E0  44 04  F0      0x0742 |= 0x04
#     90 07 42  E0  54 FB  F0      0x0742 &= ~0x04
#
# One branch or the other, unconditionally. So the bit is a footprint: if it
# ever changes, that code ran. If it never moves across a whole charge, the
# subsystem is idle and the cause is upstream of the EC's own logic.
#
# 0x0490 bits 0 and 2 are the two guards in front of the comparison; they read
# 0x0F throughout a charge in lowec-charge.json, and recording them here means
# we are not relying on one survey taken at one capacity.
#
# 0x0497 moves on its own, which is worth having next to the others for the
# simple reason that it proves the sampling can see the EC changing something.
# 0x0741, recorded for what it is rather than what this file briefly claimed.
#
# Bit 0 is ENABLE_MANUAL_CTRL -- manual FAN control, defined in
# uniwill-acpi.c and used by this project's own fan code. It is not a
# charging enable. An earlier version of this comment said it was, on the
# strength of finding it read by a routine that also clears the charging
# profile:
#
#     90 07 8E  E0  44 08      A = [0x078E] | 0x08
#     12 <callee>              callee: F0                    write bit 3 back
#                                      90 07 41 E0 54 01 22  return 0741 bit 0
#     70 07                    JNZ -> skip
#     90 07 A6  E0  54 CF  F0  clear the profile bits
#     12 <call> 70 0F
#     90 09 C7  F0 / 90 09 C8  F0 / 90 09 C9  F0
#
# Read past the profile clear and the shape changes: it clears three more
# unrelated locations straight after. This is a restore-defaults path where
# the profile is one item on a list, not a gate deciding whether the feature
# is honoured. Clearing bit 3 of a capability byte is not what a disable
# looks like either -- the routine SETS it.
#
# Kept in the log anyway. Bit 0 tells us whether anything held manual fan
# control during a capture, which is worth knowing when reading fan or power
# behaviour alongside charging, and bit 2 is set on this machine and is
# undocumented in the driver. Both are witnesses, neither is a verdict.
# The gate. Decoded 2026-09-28 from 117.ELUK: the charge-ceiling task at
# bank2:0xC88C opens with
#
#     LCALL CFED     ->  F0 90 07 C3 E0 64 04 22   A = [0x07C3] ^ 4
#     JZ             ->  arm if 0x07C3 == 4
#     LCALL C49C     ->  0x0770 == 4   (dead here: 0x0770 is ROMID_START
#                                       and reads 0xFF on this machine)
#
# and sets 0x0742 bit 2 accordingly. Our 0x0742 reads 0x22 -- bit 2 clear --
# for every sample of every capture, which is what a closed gate looks like.
#
# 0x07C3 is read at 19 sites in the firmware and written at none: no MOVX,
# no split DPL/DPH load, and no MOV DPTR,#07C0-07C2 that could INC into it.
# It is an input to the 8051 application, compared against about a dozen
# values, and 4 is the one that arms the ceiling.
#
# Logged here because a one-off dump pasted into a conversation is not
# evidence this project can cite. Sampling it across charge, full, discharge
# and AC transitions also answers the question that matters more than its
# value right now: does 0x07C3 ever reach 4 on its own? If it does, the state
# is something we can get the machine into, and nothing needs to be written.
# The profile OUTPUT path, which is gated separately from the ceiling.
#
# 0x07A6 bits 5:4 select a constant -- Stationary 200, Balanced 100, 150
# default, 250 on a high-voltage branch -- and 0xBE02 computes
#
#     0x0522:0x0523  =  [0x0A5A:0x0A5B]  -  (constant x [0x0A51])
#
# where 0x0A51 is a charge-rate code (4/3/2, also used as rate x 1040 mA) and
# 0x0A5A:0x0A5B is seeded from 0x030E:0x030F. The result reaches the charger
# over SMBus. So Stationary reduces a charge target rather than capping a
# percentage -- and a reduced target makes a pack terminate early, which is
# what an observer would describe as a ceiling.
#
# This matters because it is a DIFFERENT mechanism from the 0x07C3 gate. The
# ceiling can be shut while the profile still does something, and if
# 0x0522:0x0523 moves when the profile changes, that is the proof.
#
# 0x03xx and 0x05xx are windows this project has never read. 0x08xx is known
# to be outside the ECRR window -- 0x087F reads 0xFF, and a probe was built on
# that address before anyone checked. So these are recorded with the unmapped
# marker handled up front rather than discovered later.
REG_CHG_TARGET_LO = 0x0522      # profile output, low byte
REG_CHG_TARGET_HI = 0x0523
REG_HW_BASE_LO = 0x030E         # the value the profile constant is subtracted from
REG_HW_BASE_HI = 0x030F

REG_GATE = 0x07C3               # == 4 arms the ceiling; never written by the EC
REG_ROMID = 0x0770              # second gate; ROMID_START, unprogrammed here
REG_AP_OEM = 0x0741             # bit 0 = ENABLE_MANUAL_CTRL (fans), bit 2 = ?
REG_SUPPORT_5 = 0x0742          # bit 2 = the footprint
REG_BATT_STATUS = 0x0490        # bits 0 and 2 = the ceiling's guards
REG_CHARGE_LIMIT_MODE = 0x0497
BATTERY_CHARGE_FULL_OVER_24H = 1 << 3
BATTERY_ERM_STATUS_REACHED = 1 << 4

# Encodings straight out of uniwill-acpi.c. Ordered low-to-high on purpose:
# the whole design depends on raising the profile, never lowering it.
PROFILES = [
    ("stationary", 0x02, "Stationary -- the one documented as an 80% cap"),
    ("balanced", 0x01, "Balanced"),
    ("high_capacity", 0x00, "High Capacity -- the unrestricted reference"),
]
BY_NAME = {n: v for n, v, _ in PROFILES}
BY_VALUE = {v: n for n, v, _ in PROFILES}

BAT = "/sys/class/power_supply/BAT0"
AC = "/sys/class/power_supply/AC0/online"

# Sampling cadence. The reported phantom climb runs ~2% every 5-10 s, and the
# old 60 s idle interval applied to exactly the stretch where it happens --
# everything below --full-pct -- so it could not have resolved it.
# 0x07C6 reads 0x04 thousands of times and then one garbage byte. Four such
# one-offs in a single run had bit 4 set, which is enough to report ERM as
# armed if you believe a single sample. Believe three in a row instead.
OEM6_CONFIRM = 3

DEFAULT_INTERVAL = 5.0
DEFAULT_IDLE_INTERVAL = 5.0

# 6 ms between EC accesses. DESIGN.md 4.2: sustained EC traffic is the hazard,
# and this script runs for hours unattended.
EC_DELAY = 0.006
_last_call = 0.0

# Which door set_profile() uses. Changed only by --set-via, and defaulting to
# the one every previous run used, so nothing changes unless it is asked for.
SET_VIA = "ecrw"


def _call(expr: str) -> str:
    global _last_call
    gap = time.monotonic() - _last_call
    if gap < EC_DELAY:
        time.sleep(EC_DELAY - gap)
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        raw = parse_reply(fh.read())
    _last_call = time.monotonic()
    return raw


def ec_read(addr: int):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def ec_write(addr: int, val: int) -> None:
    _call(f"{ECRW} 0x{addr:X} 0x{val & 0xFF:X}")


# --- the other door -------------------------------------------------------
#
# Everything above reaches the EC through \_SB.INOU.ECRR/ECRW, which the DSDT
# implements as MMIO. tuxedo-drivers instead calls a WMI method, and a user on
# this same model sees the charging ceiling engage while we never have. Both
# doors set the byte -- wmi_ec_probe.py confirmed they agree on seven registers
# -- but "the byte is set" and "the EC acted on it" are different claims.
#
# The read encoding is duplicated from wmi_ec_probe.py on purpose: that file is
# contractually unable to write, and its tests assert the absence of any write
# symbol. Importing a write into it would break the one guarantee it makes.
WMBC = r"\_SB.AMW0.WMBC"
WMI_INSTANCE, WMI_METHOD_ID = 0x00, 0x04
WMI_FUNCTION = {"read": 1, "write": 0}
WMI_ARG_LEN = 8                       # what tuxedo actually sends; see the probe


def _wmi(addr: int, data: int | None):
    """One WMI EC access. data=None reads; otherwise writes. (value, error)."""
    buf = bytearray(WMI_ARG_LEN)
    buf[0] = addr & 0xFF
    buf[1] = (addr >> 8) & 0xFF
    if data is None:
        buf[5] = WMI_FUNCTION["read"]
    else:
        buf[2] = data & 0xFF
        buf[5] = WMI_FUNCTION["write"]
    raw = _call(f"{WMBC} {WMI_INSTANCE:#x} {WMI_METHOD_ID:#x} b{bytes(buf).hex()}")
    if raw.startswith("Error"):
        return None, raw
    try:
        vals = ([int(x, 16) for x in raw.strip("{}").split(",") if x.strip()]
                if raw.startswith("{") else [int(raw, 16) & 0xFF])
    except ValueError:
        return None, f"unparsable {raw[:40]}"
    if not vals:
        return None, "empty response"
    if len(vals) >= 4 and vals[:4] == [0xFE, 0xFE, 0xFE, 0xFE]:
        return None, "firmware returned 0xFEFEFEFE"
    return vals[0], None


def wmi_set_profile(value: int) -> bool:
    """Set the charging profile through the WMI door.

    Read-modify-write for the same reason as the MMIO path: 0x07A6 also carries
    OVERBOOST_DYN_TEMP_OFF (bit 1) and TOUCHPAD_TOGGLE_OFF (bit 6).

    Verified through BOTH doors afterwards. If a WMI write lands somewhere ECRR
    cannot see -- or the reverse -- that is a shadow register, which would be a
    larger finding than the one we are chasing and must stop the run rather
    than be averaged away.
    """
    cur, err = _wmi(REG_OEM_4, None)
    if cur is None:
        print(f"  ! WMI read failed: {err}", flush=True)
        return False
    want = (cur & ~PROFILE_MASK) | (value << PROFILE_SHIFT)
    _wmi(REG_OEM_4, want)
    time.sleep(0.2)
    via_wmi, _ = _wmi(REG_OEM_4, None)
    via_ecrr = ec_read(REG_OEM_4)
    if via_wmi != via_ecrr:
        print(f"  !! the two doors disagree after a WMI write: "
              f"WMI reads 0x{via_wmi:02X}, ECRR reads 0x{via_ecrr:02X}. "
              f"That is a shadow register, not a profile change. Stopping.",
              flush=True)
        return False
    return via_wmi is not None and (via_wmi & PROFILE_MASK) >> PROFILE_SHIFT == value


def read_profile():
    v = ec_read(REG_OEM_4)
    return None if v is None else (v & PROFILE_MASK) >> PROFILE_SHIFT


def set_profile(value: int) -> bool:
    """Read-modify-write; returns whether it read back as asked.

    Read-modify-write is not politeness. 0x07A6 also carries
    OVERBOOST_DYN_TEMP_OFF (bit 1) and TOUCHPAD_TOGGLE_OFF (bit 6), so a blind
    byte write here disables the touchpad on a machine nobody is sitting at.
    """
    if SET_VIA == "wmi":
        return wmi_set_profile(value)
    cur = ec_read(REG_OEM_4)
    if cur is None:
        return False
    ec_write(REG_OEM_4, (cur & ~PROFILE_MASK) | (value << PROFILE_SHIFT))
    time.sleep(0.2)
    return read_profile() == value


def sysfs_int(name: str, base=BAT):
    """None means unreadable, which is NOT the same as zero."""
    try:
        with open(os.path.join(base, name)) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def sysfs_str(name: str):
    try:
        with open(os.path.join(BAT, name)) as fh:
            return fh.read().strip()
    except OSError:
        return None


def on_ac() -> bool:
    try:
        with open(AC) as fh:
            return fh.read().strip() == "1"
    except OSError:
        return False


def cell_count() -> int:
    """4S here, but derive it rather than assume -- this file is meant to
    survive being pointed at a sibling chassis."""
    vmin = sysfs_int("voltage_min_design")
    if not vmin:
        return 4
    return max(1, round(vmin / 1e6 / 3.7))


def be16(hi, lo):
    """A 16-bit big-endian pair. 0x03xx is stored high byte first.

    The firmware's own compare proves it: testing 0x0A5A:0x0A5B against
    500 (0x01F4) does SUBB A,#F4 on 0x0A5B and SUBB A,#01 on 0x0A5A, so
    0x0A5A holds the high byte -- and 0x0A5A:0x0A5B is copied verbatim from
    0x030E:0x030F at 0x1BCF8.

    The output at 0x0522:0x0523 is the other way round, low byte first. Reading
    both with le16 made hw_base 0x8845 = 34885 instead of 0x4588 = 17800, and
    an offset measured against that baseline is meaningless. Caught by
    DeepSeek; the mixed endianness within one computation is the trap.
    """
    return le16(lo, hi)


def le16(lo, hi):
    """A 16-bit little-endian pair, or None when the window does not answer.

    0xFFFF is unmapped space on this EC, not a value. 0x087F reads 0xFF and was
    read as data once already; returning None here means a register outside the
    ECRR window can never be plotted against battery telemetry and mistaken for
    a correlation.
    """
    if lo is None or hi is None:
        return None
    v = lo | (hi << 8)
    return None if v == 0xFFFF else v


def footprint(samples: list[dict]) -> dict:
    """Did the EC's own charge-ceiling code run during this capture?

    0x0742 bit 2 is written on every pass through the block immediately before
    the capacity comparison -- set on one branch, cleared on the other, never
    left alone. So a change in it is proof the code executed, and a flat line
    across a whole charge is the strongest evidence available that it did not.

    Deliberately asymmetric. "It moved" is a positive observation and is
    reported as one. "It never moved" is an absence, and absence over one
    capture is weaker than presence, so it is reported as a suggestion rather
    than a finding -- the same distinction this project got wrong when it read
    a single 0x078E dump as proof of a capability.
    """
    apoem = [s.get("ap_oem") for s in samples if s.get("ap_oem") is not None]
    gates = [s.get("gate") for s in samples if s.get("gate") is not None]
    seen = [s["support5"] for s in samples if s.get("support5") is not None]
    bits = [s["ran"] for s in samples if s.get("ran") is not None]
    guards = [(s.get("guard0"), s.get("guard2")) for s in samples
              if s.get("guard0") is not None]
    moved = len(set(seen)) > 1
    return {
        "gate_register": "0x07C3",
        "gate_values_seen": sorted(set(gates)),
        "gate_ever_armed": any(g == "0x04" for g in gates),
        "gate_reading": (
            "not read" if not gates else
            "0x07C3 reached 0x04 during this capture -- the gate opens on its "
            "own in some state, so the ceiling needs the machine put into that "
            "state, not a register written."
            if any(g == "0x04" for g in gates) else
            f"0x07C3 never reached 0x04 (saw {sorted(set(gates))}). The gate "
            f"stayed shut for every sample, which is consistent with 0x0742 "
            f"bit 2 reading clear throughout."),
        "ap_oem_register": "0x0741",
        "ap_oem_values_seen": sorted(set(apoem)),
        "ap_oem_reading": (
            "not read" if not apoem else
            "0x0741 bit 0 is ENABLE_MANUAL_CTRL -- manual fan control, not a "
            "charging enable. Recorded as context, not as a verdict on the "
            "profiles."),
        "register": "0x0742",
        "samples": len(seen),
        "values_seen": sorted(set(seen)),
        "bit2_moved": moved,
        "bit2_values": sorted(set(bits)),
        "guards_always_passed": bool(guards) and all(g == (1, 1) for g in guards),
        "guard_values_seen": sorted(set(guards)),
        "reading": (
            "0x0742 bit 2 changed, so the code around the capacity comparison "
            "ran. The ceiling failing is then not a matter of that code being "
            "idle -- look at whether it reads the same 0x07B9 we do."
            if moved else
            "0x0742 never changed across this capture. That is consistent with "
            "the subsystem never running, which would put the cause upstream "
            "of the EC's own logic. One capture is not proof; it is a reason "
            "to repeat it."),
    }


def sample(cells: int) -> dict:
    raw = ec_read(REG_CHARGE_CTRL)
    oem6 = ec_read(REG_AP_OEM_6)
    tgt_lo, tgt_hi = ec_read(REG_CHG_TARGET_LO), ec_read(REG_CHG_TARGET_HI)
    base_lo, base_hi = ec_read(REG_HW_BASE_LO), ec_read(REG_HW_BASE_HI)
    gate = ec_read(REG_GATE)
    romid = ec_read(REG_ROMID)
    apoem = ec_read(REG_AP_OEM)
    sup5 = ec_read(REG_SUPPORT_5)
    bstat = ec_read(REG_BATT_STATUS)
    climit = ec_read(REG_CHARGE_LIMIT_MODE)
    v_uv = sysfs_int("voltage_now")
    i_ua = sysfs_int("current_now")
    return {
        "t": datetime.now().isoformat(timespec="seconds"),
        "ac": int(on_ac()),
        "status": sysfs_str("status"),
        "capacity": sysfs_int("capacity"),
        "charge_now": sysfs_int("charge_now"),
        "charge_full": sysfs_int("charge_full"),
        # Both sysfs, no EC cost. charge_full dropping below design is the one
        # prediction that separates "the EC caps and lies about it" from "a
        # relearn redefined full and the gauge is being honest against the new
        # reference" -- and cycle_count moving is what a relearn looks like.
        "charge_full_design": sysfs_int("charge_full_design"),
        "cycle_count": sysfs_int("cycle_count"),
        "current_ma": None if i_ua is None else round(i_ua / 1000),
        "rate": infer_rate(None if i_ua is None else round(i_ua / 1000)),
        "voltage_uv": v_uv,
        "v_per_cell": None if v_uv is None else round(v_uv / 1e6 / cells, 4),
        "profile": read_profile(),
        "threshold": None if raw is None else raw & 0x7F,
        "reached": None if raw is None else int(bool(raw & 0x80)),
        "oem6": None if oem6 is None else f"0x{oem6:02X}",
        "erm_reached": None if oem6 is None else int(bool(oem6 & BATTERY_ERM_STATUS_REACHED)),
        "full_24h": None if oem6 is None else int(bool(oem6 & BATTERY_CHARGE_FULL_OVER_24H)),
        "chg_target": le16(tgt_lo, tgt_hi),
        # big-endian: 0x030E is the high byte
        "hw_base": be16(base_lo, base_hi),
        "gate": None if gate is None else f"0x{gate:02X}",
        # The whole question in one column.
        "gate_armed": None if gate is None else int(gate == 4),
        "romid0": None if romid is None else f"0x{romid:02X}",
        "ap_oem": None if apoem is None else f"0x{apoem:02X}",
        # ENABLE_MANUAL_CTRL. Named for what it is: manual fan control, not
        # anything to do with charging.
        "manual_fan_ctrl": None if apoem is None else int(bool(apoem & 0x01)),
        "support5": None if sup5 is None else f"0x{sup5:02X}",
        # The footprint. If this ever differs between two samples, the code
        # around the capacity comparison executed.
        "ran": None if sup5 is None else int(bool(sup5 & 0x04)),
        "batt_status": None if bstat is None else f"0x{bstat:02X}",
        "guard0": None if bstat is None else int(bool(bstat & 0x01)),
        "guard2": None if bstat is None else int(bool(bstat & 0x04)),
        "charge_limit_mode": None if climit is None else f"0x{climit:02X}",
    }


class Log:
    COLS = ["t", "phase", "ac", "status", "capacity", "charge_now", "charge_full",
            "charge_full_design", "cycle_count",
            "current_ma", "rate", "voltage_uv", "v_per_cell", "profile", "threshold",
            "reached", "oem6", "erm_reached", "full_24h",
            "chg_target", "hw_base",
            "gate", "gate_armed", "romid0", "ap_oem", "manual_fan_ctrl",
            "support5", "ran", "batt_status", "guard0", "guard2",
            "charge_limit_mode"]

    def __init__(self, path):
        self.oem6_seen: dict[str, int] = {}
        # Only the footprint fields, not whole samples. A capture runs for
        # hours at a few seconds a sample and the rest is already on disk.
        self.footprint_samples: list[dict] = []
        self._runs: dict[str, int] = {}
        self.erm_events: list[dict] = []
        self.full24_events: list[dict] = []
        fresh = not path or not os.path.exists(path) or os.path.getsize(path) == 0
        if path and not fresh:
            # Appending to a file whose header predates a schema change writes
            # new rows in the new column order under the old header, which
            # shifts every field after the inserted one. It happened: a capture
            # was restarted into a file written before the `rate` column
            # existed, and 42 rows landed with current_ma's value under `rate`,
            # v_per_cell under `profile`, and so on -- all plausible numbers in
            # the wrong columns, which is the worst way for data to be wrong.
            with open(path) as fh:
                existing = fh.readline().strip().split(",")
            if existing != self.COLS:
                added = [c for c in self.COLS if c not in existing]
                gone = [c for c in existing if c not in self.COLS]
                raise SystemExit(
                    f"{path} has a {len(existing)}-column header and this "
                    f"probe writes {len(self.COLS)}.\n"
                    + (f"  added since: {', '.join(added)}\n" if added else "")
                    + (f"  removed since: {', '.join(gone)}\n" if gone else "")
                    + "Appending would put values under the wrong headings.\n"
                    + "Use a new -o filename; the old file is still valid for "
                    + "its own schema.")
        self.fh = open(path, "a") if path else None
        if self.fh and fresh:
            self.fh.write(",".join(self.COLS) + "\n")
            self.fh.flush()

    def write(self, s: dict, phase: str) -> None:
        self.note(s, phase)
        self._keep_footprint(s)
        if not self.fh:
            return
        row = dict(s, phase=phase)
        self.fh.write(",".join("" if row.get(c) is None else str(row[c])
                               for c in self.COLS) + "\n")
        self.fh.flush()          # hours unattended; never buffer the evidence


    def _keep_footprint(self, s: dict) -> None:
        self.footprint_samples.append(
            {k: s.get(k) for k in ("support5", "ran", "guard0", "guard2",
                                   "ap_oem", "manual_fan_ctrl", "gate",
                                   "gate_armed")})

    def note(self, s: dict, phase: str) -> None:
        """Record what 0x07C6 did, whether or not a CSV is being written.

        A bit is only believed after OEM6_CONFIRM consecutive samples show it.
        This byte reads 0x04 thousands of times and then, perhaps once in two
        hundred, returns something else entirely -- 0x5F, 0x37, 0xBE, 0x56 --
        often in the same read cycle that corrupts a neighbouring field. Four
        of those one-off values happen to have bit 4 set, so recording the
        first occurrence reported ERM as armed on a machine where it never was.

        The same mistake as the drift guard, made twice: acting on a single
        read of a register space that is demonstrably not reliable.
        """
        raw = s.get("oem6")
        if raw is None:
            self._runs = {}
            return
        self.oem6_seen[raw] = self.oem6_seen.get(raw, 0) + 1
        for bit, store in (("erm_reached", self.erm_events),
                           ("full_24h", self.full24_events)):
            if not s.get(bit):
                self._runs[bit] = 0
                continue
            self._runs[bit] = self._runs.get(bit, 0) + 1
            if self._runs[bit] >= OEM6_CONFIRM and not store:
                store.append({"t": s["t"], "phase": phase, "oem6": raw,
                              "capacity": s.get("capacity"),
                              "confirmed_over": OEM6_CONFIRM})


def show(s: dict, phase: str) -> None:
    print(f"{s['t'][11:]}  {phase:<14} {str(s['status'] or '?'):<12} "
          f"cap={str(s['capacity']):>3}%  {str(s['current_ma']):>6} mA  "
          f"{s['v_per_cell']} V/cell  prof={BY_VALUE.get(s['profile'], s['profile'])}"
          f"  oem6={s.get('oem6') or '--'}"
          f"{'  << ERM ARMED' if s.get('erm_reached') else ''}"
          f"{'  << FULL 24H' if s.get('full_24h') else ''}",
          flush=True)


def drifted(s: dict, expect: int) -> bool:
    """Did something else change the profile under us?

    The daemon stores a charging profile and re-applies it on any /api/apply,
    so one click in the app during a multi-hour run would swap the profile
    mid-leg. Every sample records the profile, but a settled reading is only
    meaningful if the profile held for the whole quiet window -- otherwise we
    would attribute one profile's resting voltage to another.
    """
    p = s.get("profile")
    return p is not None and p != expect


def arming_warning(on_ac_now: bool) -> str | None:
    """None if this run can arm a plug-in-latched ceiling; a warning if not.

    The 2026-09-07 run started with the charger already in and concluded the
    profiles were inert. A user on the same board then saw the ceiling engage
    by selecting the profile while unplugged and connecting afterwards. If the
    EC only evaluates the profile at plug-in, a run that begins on AC can only
    ever reproduce that null -- so it must say so rather than report "inert"
    as though the question had been asked.
    """
    if not on_ac_now:
        return None
    return ("started with the charger already connected, so the profile was "
            "written into a cycle that was already running. If this EC latches "
            "the profile at plug-in, the ceiling cannot arm and an 'inert' "
            "verdict from this run means nothing. Unplug, let it discharge, "
            "and start the probe on battery.")


class DriftTracker:
    """Believe drift only after several consecutive disagreements.

    A single bad EC read must never cause a write. On 2026-09-24 one sample
    read profile=0 and oem6=0x5F while every neighbour read 2 and 0x04 -- two
    registers wrong in the same cycle, which is a bad read, not a state
    change. The guard re-asserted anyway, writing 0x07A6 at 75% capacity. If
    this EC latches its charge ceiling at plug-in, that write is precisely what
    would clear it, so the guard written to protect the experiment is the
    prime suspect for having corrupted it.

    A real change -- the daemon re-applying its stored profile -- persists and
    will still be caught a few samples later. A glitch will not.
    """

    def __init__(self, confirm: int):
        self.confirm = confirm
        self.run = 0
        self.events: list[dict] = []

    def saw(self, s: dict, expect: int) -> bool:
        """True only when a re-assert is actually warranted."""
        if not drifted(s, expect):
            self.run = 0
            return False
        self.run += 1
        if self.run < self.confirm:
            return False
        self.run = 0
        self.events.append({"t": s.get("t"), "read": s.get("profile"),
                            "expected": expect, "capacity": s.get("capacity")})
        return True


def wait_for_ac(log, cells, args) -> None:
    if on_ac():
        return
    print("on battery -- waiting for the charger. Plug in whenever you like.\n",
          flush=True)
    while not on_ac():
        s = sample(cells)
        log.write(s, "wait_ac")
        time.sleep(args.idle_interval)


def settle(log, cells, args, phase: str, expect: int, drift) -> dict | None:
    """Wait until the pack is full and the current has genuinely stopped.

    'Genuinely' is the whole point. A momentary dip below the threshold is not
    termination -- the charger tapers for a long time near the top -- so this
    demands a sustained quiet period before it believes the reading, and it
    reports the median of that window rather than one sample.
    """
    quiet: list[dict] = []
    quiet_since = None
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        s = sample(cells)
        log.write(s, phase)

        if not s["ac"]:
            print("  ! charger removed -- this reading is void. Plug back in.",
                  flush=True)
            quiet, quiet_since = [], None
            time.sleep(args.idle_interval)
            continue

        if drift.saw(s, expect):
            print(f"  ! profile held at {BY_VALUE.get(s['profile'])} for "
                  f"{drift.confirm} samples -- re-asserting "
                  f"{BY_VALUE.get(expect)}. NOTE: this writes 0x07A6 mid-cycle "
                  f"and may clear a latched ceiling", flush=True)
            set_profile(expect)
            quiet, quiet_since = [], None
            time.sleep(args.interval)
            continue

        cur = s["current_ma"]
        near_full = (s["capacity"] or 0) >= args.full_pct
        is_quiet = cur is not None and abs(cur) <= args.settle_ma and near_full

        if is_quiet:
            quiet.append(s)
            quiet_since = quiet_since or time.monotonic()
            held = time.monotonic() - quiet_since
            if len(quiet) >= args.settle_samples and held >= args.settle_seconds:
                vs = [q["v_per_cell"] for q in quiet if q["v_per_cell"] is not None]
                out = dict(quiet[-1])
                out["v_per_cell"] = round(statistics.median(vs), 4) if vs else None
                out["settled_samples"] = len(quiet)
                out["settled_seconds"] = round(held)
                print(f"  settled: {out['v_per_cell']} V/cell over "
                      f"{len(quiet)} samples / {round(held)}s at "
                      f"{out['capacity']}%\n", flush=True)
                return out
        else:
            if quiet:
                print("  (current came back -- still tapering)", flush=True)
            quiet, quiet_since = [], None

        show(s, phase)
        time.sleep(args.interval if near_full else args.idle_interval)

    print(f"  ! gave up waiting after {args.timeout/3600:.1f} h", flush=True)
    return None


def watch_resume(log, cells, args, phase: str, expect: int, drift) -> bool:
    """After raising the profile: does the charger start again?

    This is the measurement. If the previous profile terminated lower, the pack
    is now below the new profile's target and charging MUST restart. Silence
    here is not a null result -- it is the answer.
    """
    deadline = time.monotonic() + args.resume_window
    while time.monotonic() < deadline:
        s = sample(cells)
        log.write(s, phase)
        show(s, phase)
        if drift.saw(s, expect):
            print(f"  ! profile drift confirmed -- re-asserting", flush=True)
            set_profile(expect)
            time.sleep(args.interval)
            continue
        cur = s["current_ma"]
        if cur is not None and cur > args.resume_ma:
            print(f"  -> RESUMED at {cur} mA. The previous profile was "
                  f"terminating lower.\n", flush=True)
            return True
        time.sleep(args.interval)
    print(f"  -> no resume in {args.resume_window/60:.0f} min "
          f"(above {args.resume_ma} mA).\n", flush=True)
    return False


def verdict(results: list[dict], args) -> dict:
    """Turn the readings into a claim, and say when there is not one to make."""
    good = [r for r in results if r.get("settled") and r["settled"].get("v_per_cell")]
    if len(good) < 2:
        return {"conclusion": "inconclusive",
                "detail": "fewer than two profiles produced a settled reading"}

    vs = {r["profile"]: r["settled"]["v_per_cell"] for r in good}
    spread_mv = round((max(vs.values()) - min(vs.values())) * 1000, 1)
    resumed = [r["profile"] for r in results if r.get("resumed")]

    if resumed:
        return {"conclusion": "profiles affect termination voltage",
                "detail": f"charging resumed after raising to {', '.join(resumed)}; "
                          f"resting spread {spread_mv} mV/cell",
                "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": resumed}
    if spread_mv <= args.same_mv:
        return {"conclusion": "profiles are inert",
                "detail": f"no profile change restarted the charger, and all "
                          f"settled within {spread_mv} mV/cell "
                          f"(noise floor {args.same_mv} mV)",
                "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": [],
                # Stated as a caveat rather than buried, because it is the one
                # way this null result could be wrong: an EC that latches the
                # profile at plug-in would ignore a mid-charge change and look
                # exactly like an EC that ignores the profile entirely.
                "caveat": "assumes the EC re-evaluates the profile while on AC. "
                          "To rule out a plug-in-latched profile, rerun with "
                          "--order high_capacity,stationary and unplug/replug "
                          "after the switch."}
    return {"conclusion": "unclear",
            "detail": f"nothing resumed, but resting voltages differ by "
                      f"{spread_mv} mV/cell -- more than the {args.same_mv} mV "
                      f"noise floor. Repeat before trusting it.",
            "spread_mv_per_cell": spread_mv, "resting": vs, "resumed": []}


def infer_rate(current_ma):
    """The charge-rate code, recovered from current.

    0x0A51 holds 2, 3 or 4 and is selected from charger status; the firmware
    uses it as rate * 1040 = mA elsewhere, so the code is recoverable from
    current even though 0x0Axx is not host-readable.

    It matters because the profile's output is
    chg_target = hw_base - (constant * rate), so a target that differs between
    two samples is only attributable to the constant if the rate held. Rate is
    temperature and voltage coupled, which is exactly what drifts between two
    sessions -- and the reason a single mid-charge switch beats two runs.
    """
    if current_ma is None or current_ma <= 0:
        return None
    r = round(current_ma / 1040)
    return r if 2 <= r <= 4 else None


def switch_run(cells: int, first: str, second: str, at_pct: int, args, log) -> dict:
    """Charge under one profile, switch to another mid-charge, watch the offset.

    Isolates the profile constant from everything else. The constant is not
    chosen by 0x07A6 alone -- the selection at 0x1BD07-0x1BE02 branches first on
    hw_base, 0x0A56:0x0A57, 0x09C9:0x09CA and 0x0A5C, and only then consults the
    profile bits. Those gate registers are unmapped, so two separate runs cannot
    be shown to have sat in the same branch, and "comparable capacity" is not a
    sufficient control.

    Across a single switch, seconds apart, the telemetry and the rate are
    effectively constant and the profile constant is the only thing that moves.
    Stationary is 200 and Balanced is 100, so the offset should shift by
    100 * rate; against the 150 default, 50 * rate.

    A mid-charge write to 0x07A6 is what --drift-confirm exists to prevent, but
    that guard protects a latched ceiling and the ceiling is shut here
    (0x07C3 reads 0x0D, not 4). The profile output is recomputed every pass,
    so there is no latch to disturb.
    """
    print(f"switch mode: {first} until >={at_pct}%, then {second}.\n", flush=True)
    set_profile(BY_NAME[first])

    before, after = [], []
    fired = None
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        s_ = sample(cells)
        log.write(s_, f"switch_{first}")
        cap = s_["capacity"]
        print(f"  {s_['t'][11:]}  {cap}%  {s_['current_ma']} mA  "
              f"target={s_['chg_target']} base={s_['hw_base']}", flush=True)
        if s_["status"] == "Charging":
            before.append(s_)
            if cap is not None and cap >= at_pct:
                fired = s_
                break
        time.sleep(args.interval)

    if fired is None:
        return {"switched": False, "why": "never reached the switch capacity"}

    print(f"\n  switching to {second} at {fired['capacity']}%\n", flush=True)
    set_profile(BY_NAME[second])

    watch_end = time.monotonic() + args.trigger_watch
    while time.monotonic() < watch_end:
        s_ = sample(cells)
        log.write(s_, f"switch_{second}")
        after.append(s_)
        print(f"  {s_['t'][11:]}  {s_['capacity']}%  {s_['current_ma']} mA  "
              f"target={s_['chg_target']} base={s_['hw_base']}", flush=True)
        time.sleep(1.0)

    return switch_verdict(before, after, first, second)


def switch_verdict(before, after, first, second) -> dict:
    def offsets(rows):
        out = []
        for r in rows:
            t, b = r.get("chg_target"), r.get("hw_base")
            if t is not None and b is not None:
                out.append(b - t)
        return out
    pre, post = offsets(before[-8:]), offsets(after)
    rates = [r for r in (infer_rate(x.get("current_ma")) for x in before[-8:] + after)
             if r is not None]
    bases = [x["hw_base"] for x in before[-8:] + after if x.get("hw_base") is not None]
    have = bool(pre or post)
    small_base = bool(bases) and all(b < 500 for b in bases)
    d = (round(sum(post)/len(post)) - round(sum(pre)/len(pre))) if pre and post else None
    rate = max(set(rates), key=rates.count) if rates else None
    return {
        "switched": True, "from": first, "to": second,
        "offset_before": round(sum(pre)/len(pre)) if pre else None,
        "offset_after": round(sum(post)/len(post)) if post else None,
        "offset_delta": d,
        "rate_inferred": rate,
        "constant_delta": None if (d is None or not rate) else round(d / rate),
        "hw_base_seen": sorted(set(bases))[:6],
        "reading": (
            "chg_target and hw_base never answered. 0x05xx/0x03xx are outside "
            "the ECRR window on this machine -- the wrong door, not a null "
            "result." if not have else
            "hw_base stayed below 500 for the whole window, and the firmware "
            "forces the constant to 0 in that case. An unchanged offset here "
            "means the profile chose no reduction, NOT that the path is idle."
            if small_base else
            f"the offset moved by {d} with rate {rate}, i.e. a constant change "
            f"of about {round(d/rate) if rate else '?'}. The profile path runs "
            f"on this machine."
            if d else
            "the offset did not move across the switch. With hw_base above 500 "
            "and the rate steady, that is evidence the profile constant is not "
            "reaching the charge target here."),
    }


def trigger_run(cells: int, profile: str, at_pct: int, args, log) -> dict:
    """Charge past the threshold with no limit set, then write the profile ONCE.

    ANSWERED, AND THE ANSWER IS NO. Do not spend a charge cycle on this.

    This was built to test whether the EC evaluates the ceiling when the
    profile register is written rather than polling on its own -- which would
    mean every null result here came from writing early, below the threshold,
    so the single evaluation found nothing to do.

    The experiment had already been run, twice, before this function existed:

        ceiling-test.csv   profile -> 2 at 85%, threshold 80, still charging
        ceiling-test.csv   profile -> 2 at 98%, threshold 80, 306 mA
        phantom-check.csv  profile -> 2 at 86%, threshold 80, 1190 mA

    All three wrote the profile with capacity already above the threshold.
    None engaged: CHARGE_CTRL_REACHED stayed clear and every one of them
    charged on to 100%. A single write above the threshold does nothing.

    (phantom-check.csv contains one sample reading reached=1. It is a glitch,
    not an event: the same sample reads the threshold as 72 instead of 80, and
    both fields come from the same byte -- 0x50 misread as 0xC8 -- with clean
    neighbours either side. The known ECRR single-byte glitch, same class as
    0x07C6 reading 0xC0 once in 980 samples.)

    Kept because it costs nothing and may behave differently on another
    Uniwill project, and because a documented negative is worth more than an
    absent test someone rebuilds. What it leaves standing is the distinction
    the data actually supports: sustained writes produced charging that
    started and stopped, single writes do nothing, and the difference between
    held and set is the open question. See DESIGN.md 4.1b before acting on
    that -- holding these registers is what latched this EC.

    The theory this tests: the EC does not poll capacity against the threshold
    on its own, it evaluates when something writes the register. Every negative
    result in this project set the profile once, early, while capacity was
    BELOW the threshold -- so the single evaluation that happened found nothing
    to do and was never repeated, and the pack charged past.

    The prediction is sharp. Write the profile once while capacity is already
    ABOVE the threshold and the ceiling should engage within seconds.

    Why one write and not a loop. Holding these registers by continuous
    re-writing is the only operation known to have damaged this machine: it
    produced bang-bang charging, then a latched state reporting 100% with the
    true charge unknown, which survived reboots (see DESIGN.md 4.1b). A single
    write is something this project has done hundreds of times. If evaluation
    really is write-driven then one write is all the theory needs, and the
    oscillation seen back then is explained rather than reproduced.

    Timing is the rest of it. An earlier attempt wrote at 94%, deep in the CV
    taper, where current is falling anyway and a ceiling is indistinguishable
    from normal termination. At 65% this pack draws ~3700 mA and at 85% about
    2600, so a collapse toward zero is unambiguous.

    The threshold is NOT written here. Set it beforehand through the driver:
        echo 60 > /sys/class/power_supply/BAT0/charge_control_end_threshold
    """
    print(f"trigger mode: charging with no profile set; at >={at_pct}% "
          f"a single write of {profile} goes in.\n", flush=True)

    before: list[dict] = []
    fired_at = None
    deadline = time.monotonic() + args.timeout

    while time.monotonic() < deadline:
        s_ = sample(cells)
        log.write(s_, "trigger_wait")
        cap, cur = s_["capacity"], s_["current_ma"]
        thr = s_["threshold"]
        print(f"  {s_['t'][11:]}  {cap}%  {cur} mA  thr={thr}  "
              f"reached={s_['reached']}", flush=True)
        if cap is not None and cur is not None and s_["status"] == "Charging":
            before.append(s_)
            if thr is not None and cap <= thr:
                print(f"    (capacity {cap}% is not above the threshold {thr}%; "
                      f"the write would prove nothing yet)", flush=True)
            elif cap >= at_pct:
                fired_at = s_
                break
        time.sleep(args.interval)

    if fired_at is None:
        return {"fired": False,
                "why": "never reached the trigger capacity while charging"}

    pre = [b["current_ma"] for b in before[-6:] if b["current_ma"] is not None]
    pre_ma = round(sum(pre) / len(pre)) if pre else None
    print(f"\n  writing {profile} once at {fired_at['capacity']}%, "
          f"{pre_ma} mA\n", flush=True)
    set_profile(BY_NAME[profile])

    # One write, then watch closely. Fast cadence only in this window, because
    # the whole question is how quickly current moves after a single poke.
    after: list[dict] = []
    watch_end = time.monotonic() + args.trigger_watch
    while time.monotonic() < watch_end:
        s_ = sample(cells)
        log.write(s_, "trigger_watch")
        after.append(s_)
        print(f"  {s_['t'][11:]}  {s_['capacity']}%  {s_['current_ma']} mA  "
              f"reached={s_['reached']}  ran={s_['ran']}", flush=True)
        time.sleep(1.0)

    post = [a["current_ma"] for a in after if a["current_ma"] is not None]
    min_ma = min(post) if post else None
    armed = any(a["reached"] for a in after if a["reached"] is not None)
    ran = len({a["ran"] for a in after if a["ran"] is not None}) > 1
    dropped = (pre_ma is not None and min_ma is not None
               and pre_ma > 500 and min_ma < pre_ma * 0.2)

    return {
        "fired": True,
        "profile": profile,
        "at": {k: fired_at[k] for k in ("t", "capacity", "threshold",
                                        "current_ma", "v_per_cell")},
        "current_before_ma": pre_ma,
        "current_min_after_ma": min_ma,
        "reached_armed": armed,
        "footprint_moved": ran,
        "watched_seconds": args.trigger_watch,
        "verdict": (
            "ENGAGED. Current collapsed after a single write with capacity "
            "already above the threshold. The EC evaluates the ceiling when "
            "the register is written, not on its own, which is why every "
            "set-once-then-charge run in this project measured nothing."
            if dropped or armed else
            "No response. One write above the threshold changed nothing, so "
            "write-triggered evaluation does not explain it on its own. Do "
            "NOT escalate to a sustained write loop from here -- that is the "
            "operation that latched this EC before. Watch the Control Center "
            "service on Windows instead."),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Do charging profiles change the termination voltage?")
    ap.add_argument("-o", "--output", default="charge-profiles.csv")
    ap.add_argument("--json", default=None,
                    help="summary path (default: alongside --output)")
    ap.add_argument("--order", default="stationary,balanced,high_capacity",
                    help="profiles low-to-high; raising is what forces a resume")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--idle-interval", type=float, default=DEFAULT_IDLE_INTERVAL)
    ap.add_argument("--full-pct", type=int, default=99,
                    help="capacity that counts as full")
    ap.add_argument("--settle-ma", type=int, default=60,
                    help="|current| at or below this counts as terminated")
    ap.add_argument("--settle-samples", type=int, default=12)
    ap.add_argument("--settle-seconds", type=float, default=300,
                    help="how long the current must stay down before believing it")
    ap.add_argument("--resume-ma", type=int, default=120,
                    help="current above this after a profile raise counts as a resume")
    ap.add_argument("--resume-window", type=float, default=900)
    ap.add_argument("--timeout", type=float, default=6 * 3600,
                    help="give up waiting for one settle after this long")
    ap.add_argument("--set-via", choices=("ecrw", "wmi"), default="ecrw",
                    help="which door writes the profile. ecrw is MMIO, the "
                         "path every run so far has used; wmi is the path "
                         "tuxedo-drivers uses on machines where the ceiling "
                         "engages")
    ap.add_argument("--drift-confirm", type=int, default=3,
                    help="consecutive disagreeing reads before believing the "
                         "profile actually changed. 1 restores the old "
                         "write-on-one-bad-read behaviour")
    ap.add_argument("--same-mv", type=float, default=15,
                    help="mV/cell within which two resting voltages are the same")
    ap.add_argument("--switch-at", type=int, metavar="PCT",
                    help="charge under --order's first profile, switch to its "
                         "second at this capacity, and watch the offset. "
                         "Isolates the profile constant from telemetry drift "
                         "better than two separate runs.")
    ap.add_argument("--trigger-at", type=int, metavar="PCT",
                    help="charge with no profile set, then write the profile "
                         "ONCE at this capacity. Meaningful only above the "
                         "threshold, and best well below the CV taper where a "
                         "current drop is unambiguous.")
    ap.add_argument("--trigger-watch", type=float, default=180,
                    help="seconds to sample at 1 s after the single write")
    ap.add_argument("--status", action="store_true", help="one sample, no writes")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    args = ap.parse_args()

    if not args.dry_run:
        if os.geteuid() != 0:
            raise SystemExit("must run as root")
        if not os.path.exists(CALL):
            raise SystemExit("run: sudo modprobe acpi_call")

    global SET_VIA
    SET_VIA = args.set_via

    cells = cell_count()
    order = [p.strip() for p in args.order.split(",") if p.strip()]
    for p in order:
        if p not in BY_NAME:
            raise SystemExit(f"unknown profile {p!r}; choose from {list(BY_NAME)}")

    if args.status:
        s = sample(cells)
        print(json.dumps(dict(s, cells=cells), indent=2))
        return 0

    if args.dry_run:
        print(f"{cells}S pack. Plan:")
        print(f"  1. wait for AC (currently {'on' if on_ac() else 'off'})")
        for i, p in enumerate(order, 1):
            print(f"  {i+1}. set {p}, charge to >={args.full_pct}%, wait for "
                  f"<={args.settle_ma} mA held {args.settle_seconds:.0f}s, "
                  f"record resting V/cell"
                  + ("" if i == 1 else " (watching first for a resume)"))
        print(f"  {len(order)+2}. restore the original profile and summarise")
        return 0

    original = read_profile()
    if original is None:
        raise SystemExit("cannot read the profile register -- refusing to write blind")

    if args.switch_at is not None:
        if len(order) != 2:
            raise SystemExit("--switch-at needs two profiles via --order")
        log = Log(args.output)
        try:
            res = switch_run(cells, order[0], order[1], args.switch_at, args, log)
        finally:
            set_profile(original)
        path = args.json or os.path.splitext(args.output)[0] + ".json"
        with open(path, "w") as fh:
            json.dump({"when": datetime.now().isoformat(timespec="seconds"),
                       "cells": cells, "mode": "switch", "switch": res,
                       "footprint": footprint(log.footprint_samples)}, fh, indent=2)
        print(f"\n{res.get('reading', res.get('why'))}\n\nsamples: "
              f"{args.output}\nsummary: {path}")
        return 0

    if args.trigger_at is not None:
        if len(order) != 1:
            raise SystemExit("--trigger-at takes exactly one profile via --order")
        log = Log(args.output)
        try:
            res = trigger_run(cells, order[0], args.trigger_at, args, log)
        finally:
            set_profile(original)
        summary = {"when": datetime.now().isoformat(timespec="seconds"),
                   "cells": cells, "mode": "trigger", "set_via": SET_VIA,
                   "trigger": res, "footprint": footprint(log.footprint_samples)}
        path = args.json or os.path.splitext(args.output)[0] + ".json"
        with open(path, "w") as fh:
            json.dump(summary, fh, indent=2)
        print(f"\n{res.get('verdict', res.get('why'))}\n\nsamples: "
              f"{args.output}\nsummary: {path}")
        return 0
    print(f"{cells}S pack. Starting profile: "
          f"{BY_VALUE.get(original, original)}. It will be restored on exit.\n")

    warning = arming_warning(on_ac())
    if warning:
        print(f"  {'!' * 3} {warning}\n", flush=True)

    log = Log(args.output)
    drift = DriftTracker(args.drift_confirm)
    results: list[dict] = []

    try:
        # Set the first profile BEFORE waiting for the charger. Our 2026-09-07
        # run set it into a cycle that was already running -- ac=1, Charging,
        # 61%, 4998 mA in the very first sample -- and measured nothing. A user
        # on the same board then saw the ceiling engage by selecting the profile
        # while unplugged and then connecting. If the EC latches the profile at
        # plug-in, that ordering is the whole experiment, and we had it backwards.
        first = order[0]
        if not on_ac():
            print(f"setting {first} before the charger goes in -- if the EC "
                  f"latches the profile at plug-in, this is the only ordering "
                  f"that arms it\n", flush=True)
            set_profile(BY_NAME[first])

        wait_for_ac(log, cells, args)

        for i, name in enumerate(order):
            print(f"=== {name} ===", flush=True)
            if not set_profile(BY_NAME[name]):
                print(f"  ! could not set {name} -- skipping\n", flush=True)
                results.append({"profile": name, "set": False})
                continue

            entry = {"profile": name, "set": True, "resumed": None}
            # The first profile has nothing to resume from; every later one is
            # a raise, and that is the actual experiment.
            if i > 0:
                entry["resumed"] = watch_resume(log, cells, args,
                                                f"raise:{name}", BY_NAME[name],
                                                drift)
            entry["settled"] = settle(log, cells, args, name, BY_NAME[name],
                                      drift)
            results.append(entry)

    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        if set_profile(original):
            print(f"restored profile {BY_VALUE.get(original, original)}")
        else:
            print(f"!! could not restore profile {original} -- set it in the app, "
                  f"or power cycle (this register is volatile)")

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "cells": cells,
        "charge_full": sysfs_int("charge_full"),
        "charge_full_design": sysfs_int("charge_full_design"),
        "set_via": SET_VIA,
        "order": order,
        # A null result from a run that could not arm the ceiling is not a
        # null result. Carried in the summary so it cannot be read without it.
        "arming_warning": warning,
        # Any mid-cycle write to 0x07A6 may clear a latched ceiling, so a run
        # containing one cannot be read as evidence that the ceiling is absent.
        "profile_reasserts": drift.events,
        "results": results,
        # Reported independently of the voltage verdict. This is a separate
        # question that happens to share a charge cycle, and it stands on its
        # own whichever way the profiles turn out.
        "oem6": {
            "register": "0x07C6",
            "values_seen": log.oem6_seen,
            "erm_first_armed": log.erm_events[0] if log.erm_events else None,
            "full_24h_first_set": log.full24_events[0] if log.full24_events else None,
        },
        # Whether the EC's own ceiling code ran at all. Independent of the
        # voltage verdict, and after 117.ELUK the more interesting question.
        "footprint": footprint(log.footprint_samples),
        "verdict": verdict(results, args),
    }
    path = args.json or (os.path.splitext(args.output)[0] + ".json")
    with open(path, "w") as fh:
        json.dump(summary, fh, indent=2)

    v = summary["verdict"]
    print(f"\n{'='*64}\n{v['conclusion'].upper()}\n  {v['detail']}")
    if v.get("caveat"):
        print(f"  caveat: {v['caveat']}")

    o = summary["oem6"]
    print(f"\n0x07C6 over the run: {', '.join(f'{k} x{n}' for k, n in o['values_seen'].items()) or 'never read'}")
    if o["erm_first_armed"]:
        e = o["erm_first_armed"]
        print(f"  ERM STATUS ARMED at {e['t']} ({e['phase']}, {e['capacity']}%, {e['oem6']})"
              f" -- follow this, it is a live battery mechanism the driver ignores")
    else:
        print("  ERM status never armed")
    if o["full_24h_first_set"]:
        print(f"  full-for-24h set at {o['full_24h_first_set']['t']}")
    print(f"{'='*64}")
    print(f"samples: {args.output}\nsummary: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
