# SPDX-License-Identifier: MIT
"""Userspace fan curves, via the EC's universal-fan-control tables.

Verified on hardware 2026-08-30 (DESIGN.md §3.6). The EC honours both the duty
and the hysteresis written into 0x0F00-0x0F5F, `SPLIT_TABLES` gives the GPU fan
its own table, and `ENABLE_UNIVERSAL_FAN_CTRL` is reversible.

Three facts shape everything here:

  * **The tables ship empty and the enable bit ships clear.** Setting the bit
    first hands the fans a curve that reads zero at every temperature. Populate,
    then enable -- and the intuitive order is the dangerous one, so `enable()`
    refuses unless the tables already hold a valid curve. That rule lives in
    code, not just in the docs.
  * **The EC slews at ~1.66 duty %/s** (measured: 8.9 hwmon units per 2.1 s,
    linear). A curve change takes seconds to land. Anything that reads back
    immediately and concludes the write did nothing is measuring the ramp.
  * **Whether the EC still overrides in a thermal emergency is UNKNOWN.** With
    universal control on it is running our table, and §4.2 established the
    firmware's own ramp lives on the same loop we are overriding. Until that is
    tested, no curve may command below MIN_DUTY and the editor enforces it. The
    stock zero-RPM band below 55 C is deliberately given up for now.
"""

from __future__ import annotations

from .ec import ECUnavailable, ECWriteRejected

REG_UNIVERSAL_FAN_CTRL = 0x07C5
SPLIT_TABLES = 0x80
REG_AP_OEM_6 = 0x07C6
ENABLE_UNIVERSAL = 0x04

TABLE_LEN = 16
PWM_MAX = 200                      # the register scale; duty% * 2

# (up_t, down_t, duty) per fan -- IN THAT ORDER, and the order is the point.
#
# Corrected 2026-09-30. This tuple was unpacked as (down_t, up_t, duty), which
# put the rise threshold in the fall register and the fall in the rise. Every
# curve this project ever wrote had inverted bands.
#
# The hardware settles it. Control Center's per-mode tables, read back off this
# machine in each mode (ec-mode-*.json):
#
#     office CPU [1]   0x0F00 = 57   0x0F10 = 48   duty = 60
#     beast  CPU [3]   0x0F00 = 65   0x0F10 = 61   duty = 80
#
# 0x0F00 always holds the LARGER value, and across the whole table
# 0x0F00[i] equals 0x0F10[i+1] (Beast) or one less (Office) -- so 0x0F10 opens
# a band and 0x0F00 closes it. That is rise and fall, and it matches what
# DESIGN.md already documented ("CPU temp start / end | 0x0F10 / 0x0F00",
# "each point is {UpT, DownT, Duty} -- rise threshold, fall threshold").
#
# Stored curves do not change meaning: a point is still [up_t, down_t, duty]
# and validate() still requires down_t < up_t. Only which register each one
# lands in is fixed, so saved curves need no editing -- they will simply be
# written the right way round for the first time.
BASE = {
    "cpu": (0x0F00, 0x0F10, 0x0F20),
    "gpu": (0x0F30, 0x0F40, 0x0F50),
}

# Until the emergency-override question is settled, this is the floor. It is a
# safety limit, not a preference: a curve that can command zero is a fan-stop
# waiting for the wrong temperature.
UNUSED = 0xFF                      # end-of-table marker, the vendor's own
MIN_REAL_POINTS = 3                # off band + at least two real bands
MIN_DUTY = 25
MAX_TEMP = 105                     # sanity bound for a table entry


class CurveError(ValueError):
    """A curve that must not reach the hardware."""


def _curve(anchors: list[tuple[int, int]], t_lo: int, t_hi: int,
           hysteresis: int = 5) -> list[list[int]]:
    """16 points as [up_t, down_t, duty], duty interpolated between anchors.

    Anchors are (temp, duty%) and are far easier to reason about than sixteen
    hand-written rows; interpolating guarantees the monotonicity `validate()`
    insists on rather than relying on nobody fat-fingering a table.
    """
    pts = []
    for i in range(TABLE_LEN):
        t = round(t_lo + (t_hi - t_lo) * i / (TABLE_LEN - 1))
        duty = anchors[0][1]
        for (t0, d0), (t1, d1) in zip(anchors, anchors[1:]):
            if t >= t1:
                duty = d1
            elif t > t0:
                duty = d0 + (d1 - d0) * (t - t0) / (t1 - t0)
                break
        pts.append([t, max(0, t - hysteresis), int(round(duty))])
    return pts


# One curve pair per preset, so the profile button cycles power and cooling
# together. Deliberately never quiet-at-any-cost: the GPU tops out earlier than
# the CPU because the dGPU throttles lower.
PRESET_CURVES: dict[str, dict[str, list[list[int]]]] = {
    "office": {
        "cpu": _curve([(45, 25), (65, 43), (85, 78), (95, 100)], 45, 100),
        "gpu": _curve([(45, 25), (65, 45), (82, 82), (90, 100)], 45, 95),
    },
    "balanced": {
        "cpu": _curve([(40, 30), (60, 52), (80, 86), (92, 100)], 40, 100),
        "gpu": _curve([(40, 30), (60, 55), (78, 88), (88, 100)], 40, 95),
    },
    "performance": {
        "cpu": _curve([(35, 40), (55, 68), (75, 95), (85, 100)], 35, 100),
        "gpu": _curve([(35, 40), (55, 72), (72, 97), (82, 100)], 35, 95),
    },
}


def validate(curve: list[list[int]], label: str = "curve") -> None:
    """Raise CurveError unless this is safe to put in front of the fans.

    Two shapes the original rule rejected that the hardware actually uses,
    found by reading Control Center's own per-mode tables off this machine:

    **The off band.** Point 0 is `up=55, down=0, duty=0` -- fans stopped below
    the first threshold. DESIGN.md measured that independently: at idle both
    fans sit at 0 rpm until ~55 C, and that is correct stock behaviour, not a
    stall. A blanket duty floor makes the machine louder than stock for no
    thermal benefit, so duty 0 is allowed at index 0 and nowhere else.

    **The end marker.** Unused slots carry `up_t = 0xFF`. The floor still
    applies to every real band, and a table must keep at least
    MIN_REAL_POINTS of them -- an all-marker table is the empty table that
    hands the fans nothing, which is the hazard this function exists for.
    """
    if len(curve) != TABLE_LEN:
        raise CurveError(f"{label}: need {TABLE_LEN} points, got {len(curve)}")

    real = 0
    ended = False
    prev_t = prev_d = -1
    for i, pt in enumerate(curve):
        if len(pt) != 3:
            raise CurveError(f"{label}[{i}]: expected [up_t, down_t, duty]")
        up_t, down_t, duty = (int(x) for x in pt)

        if up_t == UNUSED:
            ended = True
            continue
        if ended:
            raise CurveError(f"{label}[{i}]: a real point after the 0xFF end "
                             f"marker -- the EC stops reading at the marker, "
                             f"so this band would never apply")

        real += 1
        if not 0 <= up_t <= MAX_TEMP:
            raise CurveError(f"{label}[{i}]: up_t {up_t} out of range")
        if down_t >= up_t and up_t:
            raise CurveError(f"{label}[{i}]: down_t {down_t} must be below "
                             f"up_t {up_t}, or the point has no hysteresis")

        # Index 0 is the off band. Everywhere else a duty below the floor is
        # a fan-stop waiting for the wrong temperature.
        if i == 0:
            if duty and duty < MIN_DUTY:
                raise CurveError(f"{label}[0]: duty {duty}% is neither off (0) "
                                 f"nor at least {MIN_DUTY}%")
        elif not MIN_DUTY <= duty <= 100:
            raise CurveError(f"{label}[{i}]: duty {duty}% outside "
                             f"{MIN_DUTY}-100%. A curve that can command less "
                             "than the floor is a fan-stop waiting for the "
                             "wrong temperature.")

        if up_t < prev_t:
            raise CurveError(f"{label}[{i}]: temperatures must not decrease")
        if duty < prev_d:
            raise CurveError(f"{label}[{i}]: duty must not decrease with "
                             "temperature")
        prev_t, prev_d = up_t, duty

    if real < MIN_REAL_POINTS:
        raise CurveError(f"{label}: only {real} real points; an all-marker or "
                         f"near-empty table hands the fans nothing")


def read_curve(ec, fan: str) -> list[list[int]]:
    up_b, down_b, duty_b = BASE[fan]
    return [[ec.read(up_b + i), ec.read(down_b + i), ec.read(duty_b + i) // 2]
            for i in range(TABLE_LEN)]


def write_curve(ec, fan: str, curve: list[list[int]]) -> None:
    validate(curve, fan)
    up_b, down_b, duty_b = BASE[fan]
    for i, (up_t, down_t, duty) in enumerate(curve):
        ec.write_verify(up_b + i, up_t)
        ec.write_verify(down_b + i, down_t)
        ec.write_verify(duty_b + i, min(PWM_MAX, duty * 2))


def is_enabled(ec) -> bool:
    """Read from the hardware. Never infer this from stored intent."""
    return bool(ec.read(REG_AP_OEM_6) & ENABLE_UNIVERSAL)


def is_split(ec) -> bool:
    return bool(ec.read(REG_UNIVERSAL_FAN_CTRL) & SPLIT_TABLES)


def enable(ec, split: bool = True) -> None:
    """Hand the fans over to the tables -- only if they hold a valid curve.

    Reading them back first is the whole safeguard. The tables are empty on a
    cold boot, and enabling against an empty table commands the fans off at
    every temperature.
    """
    for fan in ("cpu", "gpu") if split else ("cpu",):
        try:
            validate(read_curve(ec, fan), f"{fan} table")
        except CurveError as e:
            raise CurveError(
                f"refusing to enable universal fan control: {e}. "
                "Write a curve first -- enabling against an empty or invalid "
                "table hands the fans a curve that reads zero.") from e

    ufc = ec.read(REG_UNIVERSAL_FAN_CTRL)
    ec.write_verify(REG_UNIVERSAL_FAN_CTRL,
                    ufc | SPLIT_TABLES if split else ufc & ~SPLIT_TABLES)
    oem6 = ec.read(REG_AP_OEM_6)
    ec.write_verify(REG_AP_OEM_6, oem6 | ENABLE_UNIVERSAL)


def disable(ec) -> None:
    """Return the fans to firmware control. Safe to call unconditionally."""
    try:
        oem6 = ec.read(REG_AP_OEM_6)
        ec.write_verify(REG_AP_OEM_6, oem6 & ~ENABLE_UNIVERSAL)
        ufc = ec.read(REG_UNIVERSAL_FAN_CTRL)
        ec.write_verify(REG_UNIVERSAL_FAN_CTRL, ufc & ~SPLIT_TABLES)
    except (ECUnavailable, ECWriteRejected):
        # Best effort: a power cycle restores firmware defaults regardless, and
        # raising here would mask whatever is being cleaned up after.
        pass


def apply_curves(ec, cpu: list[list[int]], gpu: list[list[int]]) -> None:
    """Populate both tables, then enable. Never the other way round."""
    validate(cpu, "cpu")
    validate(gpu, "gpu")
    write_curve(ec, "cpu", cpu)
    write_curve(ec, "gpu", gpu)
    enable(ec, split=True)
