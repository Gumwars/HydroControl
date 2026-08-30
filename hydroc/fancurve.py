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

BASE = {                           # (down_t, up_t, duty) per fan
    "cpu": (0x0F00, 0x0F10, 0x0F20),
    "gpu": (0x0F30, 0x0F40, 0x0F50),
}

# Until the emergency-override question is settled, this is the floor. It is a
# safety limit, not a preference: a curve that can command zero is a fan-stop
# waiting for the wrong temperature.
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
    """Raise CurveError unless this is safe to put in front of the fans."""
    if len(curve) != TABLE_LEN:
        raise CurveError(f"{label}: need {TABLE_LEN} points, got {len(curve)}")
    prev_t = prev_d = -1
    for i, pt in enumerate(curve):
        if len(pt) != 3:
            raise CurveError(f"{label}[{i}]: expected [up_t, down_t, duty]")
        up_t, down_t, duty = (int(x) for x in pt)
        if not 0 <= up_t <= MAX_TEMP:
            raise CurveError(f"{label}[{i}]: up_t {up_t} out of range")
        if down_t >= up_t and up_t:
            raise CurveError(f"{label}[{i}]: down_t {down_t} must be below "
                             f"up_t {up_t}, or the point has no hysteresis")
        if not MIN_DUTY <= duty <= 100:
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


def read_curve(ec, fan: str) -> list[list[int]]:
    down_b, up_b, duty_b = BASE[fan]
    return [[ec.read(up_b + i), ec.read(down_b + i), ec.read(duty_b + i) // 2]
            for i in range(TABLE_LEN)]


def write_curve(ec, fan: str, curve: list[list[int]]) -> None:
    validate(curve, fan)
    down_b, up_b, duty_b = BASE[fan]
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
