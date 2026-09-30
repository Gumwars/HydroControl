# SPDX-License-Identifier: MIT
"""
hydroc.nativemode — the machine's own performance modes, as the EC runs them.

Different from `hydroc.presets`, and deliberately kept separate. A preset is
Custom with numbers we chose: it arms the custom latch (`0x0727` bit 6) and
writes explicit power limits. A native mode hands control back — it clears the
latch, selects the mode in `0x0751`, and lets the EC apply limits it already
holds.

Established 2026-09-30 by decompiling Control Center and reading the registers
back through each mode on the hardware (`TCC-SERVICE-FINDINGS.md`,
`ec-mode-*.json`). The vendor service does exactly four things per mode: load
the fan table, clear the latch, write `0x0751`, and set the power limits to 0
meaning "use your own defaults".

Two things this buys that presets cannot:

**The EC's own limits.** They have been in the chip the whole time, and the
Turbo set is well beyond anything this project has offered:

    Gaming  0x0730-0x0733    75 /  75 / 125 W
    Office  0x0734-0x0737    45 /  45 / 125 W
    Turbo   0x07A7-0x07AA   205 / 205 / 200 W

**The profile LED.** The EC derives its colour from `0x0751`. `presets.py` used
to say the colour "is not in the EC at all", which was wrong — we never found
it because we never wrote that register. Clearing the latch and selecting a
mode gives the stock colour back.

THE FAN TABLES

`CURVES` below is Control Center's own per-mode curves, read back off this
machine in each mode rather than copied from a config file. They are written
with the mode unless `curve=False`, which is what makes a native mode sound
like the stock machine and not merely draw the stock power.

One byte was normalised. Office GPU point 3 reads `up=57, down=58` in the
vendor's table -- a fall threshold above its own rise, with points 2 and 3
both rising at 57. Duty is 30% across points 2 to 4 so it changes nothing
audible, but it violates the hysteresis invariant, and weakening the
validator to admit malformed vendor data would be the wrong trade. It is
recorded here as `[57, 56, 30]`, following the pattern of every neighbouring
point.

OFFICE AND THE FAN-USER BIT

Office encodes as `0xA0`, which sets `0x0751` bit 7 -- the bit that stopped
the fans on this machine once. The thing that made that dangerous was setting
it over *empty* tables; Control Center only ever sets it with a curve already
loaded and universal fan control on.

So the precondition is checked rather than promised. `apply()` reads the
tables back and refuses Office unless they hold a real curve. An override
exists for deliberate testing, but the default path cannot walk into the
failure by being called in the wrong order.
"""

from __future__ import annotations

from . import fancurve

REG_FAN_MODE = 0x0751           # performance mode; NOT only the manual-fan bits
BIT_FAN_BOOST = 0x40            # ORs into any mode

# Mode encoding, read back off the hardware in each mode.
MODE_OFFICE = 0xA0              # bits 7+5 -- bit 7 is the hazard
MODE_BALANCED = 0x00
MODE_BEAST = 0x10
MODE_MASK = 0xB0                # the bits that carry the mode (7, 5, 4)

# Where the EC keeps each mode's limits. Read-only here: these are the
# firmware's, and writing them is not what the vendor service does.
EC_LIMITS = {
    "balanced": {"pl1": 0x0730, "pl2": 0x0731, "pl4": 0x0732, "tcc": 0x07D8},
    "office":   {"pl1": 0x0734, "pl2": 0x0735, "pl4": 0x0736, "tcc": 0x07D9},
    "beast":    {"pl1": 0x07A7, "pl2": 0x07A8, "pl4": 0x07A9, "tcc": 0x07DA},
}

# Control Center's own curves, read off this machine. [up_t, down_t, duty%],
# 0xFF marking unused slots, exactly as the hardware stores them.
CURVES: dict[str, dict[str, list[list[int]]]] = {
    "office": {
        "cpu": [
            [ 55,   0,   0], [ 57,  48,  30], [ 59,  58,  30], [ 61,  60,  30],
            [ 63,  62,  30], [ 65,  64,  35], [ 67,  66,  35], [ 69,  68,  45],
            [255,  70,  55], [255, 255,  55], [255, 255,  55], [255, 255,  55],
            [255, 255,  55], [255, 255,  55], [255, 255,  55], [255, 255,  55],
        ],
        "gpu": [
            [ 55,   0,   0], [ 55,  48,  30], [ 57,  56,  30], [ 57,  56,  30],
            [ 59,  58,  30], [ 62,  60,  35], [ 64,  63,  35], [ 66,  65,  45],
            [255,  67,  55], [255, 255,  55], [255, 255,  55], [255, 255,  55],
            [255, 255,  55], [255, 255,  55], [255, 255,  55], [255, 255,  55],
        ],
    },
    "balanced": {
        "cpu": [
            [ 55,   0,   0], [ 58,  48,  30], [ 61,  58,  35], [ 65,  61,  40],
            [ 69,  65,  50], [ 72,  69,  55], [ 75,  73,  60], [ 78,  76,  65],
            [ 81,  79,  75], [255,  82,  80], [255, 255,  80], [255, 255,  80],
            [255, 255,  80], [255, 255,  80], [255, 255,  80], [255, 255,  80],
        ],
        "gpu": [
            [ 65,   0,   0], [ 65,  48,  30], [ 65,  58,  35], [ 65,  58,  40],
            [ 65,  58,  50], [ 68,  66,  55], [ 71,  69,  60], [ 74,  72,  65],
            [ 77,  75,  75], [255,  78,  80], [255, 255,  80], [255, 255,  80],
            [255, 255,  80], [255, 255,  80], [255, 255,  80], [255, 255,  80],
        ],
    },
    "beast": {
        "cpu": [
            [ 55,   0,   0], [ 58,  48,  30], [ 61,  58,  35], [ 65,  61,  40],
            [ 69,  65,  50], [ 72,  69,  55], [ 75,  73,  60], [ 78,  76,  65],
            [ 81,  79,  75], [ 84,  82,  80], [255,  85,  90], [255, 255,  90],
            [255, 255,  90], [255, 255,  90], [255, 255,  90], [255, 255,  90],
        ],
        "gpu": [
            [ 65,   0,   0], [ 65,  48,  30], [ 65,  58,  35], [ 65,  58,  40],
            [ 65,  58,  50], [ 68,  66,  55], [ 71,  69,  60], [ 74,  72,  65],
            [ 77,  75,  75], [ 80,  78,  80], [255,  81,  90], [255, 255,  90],
            [255, 255,  90], [255, 255,  90], [255, 255,  90], [255, 255,  90],
        ],
    },
}

MODES = {
    "balanced": {
        "name": "Balanced",
        "value": MODE_BALANCED,
        "desc": "The machine's own Gaming mode. The EC picks the limits.",
        "sets_fan_user_bit": False,
    },
    "beast": {
        "name": "Beast",
        "value": MODE_BEAST,
        "desc": "The machine's own Turbo mode. Substantially more power than "
                "any preset here offers.",
        "sets_fan_user_bit": False,
    },
    "office": {
        "name": "Office",
        "value": MODE_OFFICE,
        "desc": "The machine's own Office mode. Quietest, and the only one "
                "that sets the fan-user bit.",
        "sets_fan_user_bit": True,
    },
}

CYCLE = ["office", "balanced", "beast"]


def next_in_cycle(current: str | None) -> str:
    """Advance the profile button. Anything else starts the cycle over.

    Matches Control Center's order -- Office, Gaming, Turbo -- so a press does
    what the same press does under Windows, including the LED colour. The
    vendor's cycle has a fourth position for Custom on some paths and skips it
    on others; this skips it, because Custom here means one of several presets
    and "which one" has no answer the button could give.
    """
    if current not in CYCLE:
        return CYCLE[0]
    return CYCLE[(CYCLE.index(current) + 1) % len(CYCLE)]


def describe() -> list[dict]:
    """Mode list for the UI, in cycle order, with the EC's own limits."""
    return [{"id": k, "name": MODES[k]["name"], "desc": MODES[k]["desc"],
             "value": MODES[k]["value"]} for k in CYCLE]


class NativeModeError(RuntimeError):
    """A native mode that must not reach the hardware as asked."""


def decode(value: int) -> str | None:
    """`0x0751` -> mode name, ignoring the boost bit. None if unrecognised."""
    v = value & MODE_MASK
    for name, spec in MODES.items():
        if spec["value"] == v:
            return name
    return None


def current(ec) -> dict:
    """What the hardware is in right now. Never inferred from stored intent."""
    raw = ec.read(REG_FAN_MODE)
    return {
        "raw": raw,
        "mode": decode(raw),
        "boost": bool(raw & BIT_FAN_BOOST),
        # A native mode only means anything with the latch clear: armed, the
        # EC treats the machine as Custom whatever 0x0751 says.
        "custom_latched": ec.custom_profile_enabled(),
    }


def ec_limits(ec, mode: str) -> dict:
    """The limits the EC holds for a mode. Read-only."""
    regs = EC_LIMITS.get(mode)
    if regs is None:
        raise NativeModeError(f"unknown mode {mode!r}")
    out = {k: ec.read(a) for k, a in regs.items()}
    # PL4 is stored at half scale where 0x0727 bit 7 is set, same as the
    # setting register -- reading it raw reports half the truth.
    if ec.has_double_pl4():
        out["pl4"] *= 2
    return out


def tables_populated(ec) -> bool:
    """Do the EC's fan tables actually hold a curve right now?

    Read back, not remembered. This is the precondition for Office, and the
    whole point is that it cannot be satisfied by intent -- the tables were
    empty the time bit 7 stopped the fans, and a flag saying "I promise they
    are populated" would have been set in exactly that situation too.
    """
    for fan in ("cpu", "gpu"):
        try:
            fancurve.validate(fancurve.read_curve(ec, fan), fan)
        except fancurve.CurveError:
            return False
    return True


def apply(ec, mode: str, *, boost: bool = False, curve: bool = True,
          allow_fan_user_bit: bool = False) -> None:
    """Select a native mode, the way Control Center does it.

    Order is the vendor's and matters at two points. The fan table goes first,
    because HANDOFF.md's rule is populate-then-enable and because Office's
    precondition is that a table exists. The power limits are zeroed before
    the latch comes down, because `set_power_limit` is silently ignored with
    it clear -- zeroing afterwards would leave a previous preset's numbers in
    0x0783-0x0785 to be read back and reported as what the machine is doing.

    `curve=False` leaves the tables alone and inherits whatever is loaded.
    """
    spec = MODES.get(mode)
    if spec is None:
        raise NativeModeError(f"unknown mode {mode!r}")

    if curve:
        fancurve.apply_curves(ec, CURVES[mode]["cpu"], CURVES[mode]["gpu"])

    if spec["sets_fan_user_bit"] and not allow_fan_user_bit:
        if not tables_populated(ec):
            raise NativeModeError(
                f"{spec['name']} encodes as 0x{spec['value']:02X}, which sets "
                f"0x0751 bit 7 -- the bit that stopped the fans on this "
                f"machine with the tables EMPTY. They are empty or invalid "
                f"now. Write a curve first (curve=True does it), or pass "
                f"allow_fan_user_bit=True deliberately, with a power cycle "
                f"ready.")

    for which in ("pl1", "pl2", "pl4"):
        ec.set_power_limit(which, 0)

    ec.set_custom_profile(False)

    value = spec["value"] | (BIT_FAN_BOOST if boost else 0)
    ec.write_verify(REG_FAN_MODE, value)
