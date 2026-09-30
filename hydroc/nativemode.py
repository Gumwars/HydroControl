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

WHAT THIS DOES NOT DO YET

*It does not write fan tables.* The vendor loads a per-mode curve into
`0x0F00-0x0F5F`, and we have all three decoded from the hardware dumps. Two
things block using them, both real:

  * Their first point is 0 C at 0% duty — fans off when cold, which DESIGN.md
    separately measured as correct stock behaviour. `fancurve.MIN_DUTY` is 25
    and would reject it. That floor was added after a fan-stop incident and
    relaxing it is a deliberate decision, not a detail.
  * `fancurve.BASE` names `0x0F00` as `down_t` and `0x0F10` as `up_t`, and
    `validate()` enforces `down_t < up_t`. The vendor writes the *larger*
    value to `0x0F00` — `0x0F00[i]` equals `0x0F10[i+1]` — so our labels are
    inverted with respect to the hardware, and every curve this project has
    written has inverted bands.

Leaving the tables alone means a native mode inherits whatever curve is
already loaded, which is honest and safe. The EC's power limits and LED work
regardless.

*Office is not offered.* Its encoding is `0xA0`, which sets `0x0751` bit 7 —
the bit that stopped the fans on this machine once, with the tables empty.
Control Center only ever sets it with a table already loaded and universal fan
control on. Balanced (`0x00`) and Beast (`0x10`) do not touch bit 7, which is
why they come first.
"""

from __future__ import annotations

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
        "desc": "The machine's own Office mode. Not enabled: its encoding "
                "sets the fan-user bit.",
        "sets_fan_user_bit": True,
    },
}

# What the button cycles. Office is absent until its bit-7 hazard is tested.
CYCLE = ["balanced", "beast"]


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


def apply(ec, mode: str, *, boost: bool = False, allow_fan_user_bit: bool = False) -> None:
    """Select a native mode: zero the limits, clear the latch, write 0x0751.

    Order matters. The power limits are zeroed *first*, while the latch may
    still be armed, because `set_power_limit` writes are silently ignored with
    it clear -- so zeroing afterwards would leave stale values in
    `0x0783-0x0785`. They are inert once the latch is down, but leaving a
    previous preset's numbers sitting there would misreport what the machine
    is doing.
    """
    spec = MODES.get(mode)
    if spec is None:
        raise NativeModeError(f"unknown mode {mode!r}")
    if spec["sets_fan_user_bit"] and not allow_fan_user_bit:
        raise NativeModeError(
            f"{spec['name']} encodes as 0x{spec['value']:02X}, which sets "
            f"0x0751 bit 7 -- the bit that stopped the fans on this machine "
            f"with the tables empty. Control Center only sets it with a fan "
            f"table already loaded and universal fan control on. Reproduce "
            f"that state and pass allow_fan_user_bit=True, with a power cycle "
            f"ready.")

    for which in ("pl1", "pl2", "pl4"):
        ec.set_power_limit(which, 0)

    ec.set_custom_profile(False)

    value = spec["value"] | (BIT_FAN_BOOST if boost else 0)
    ec.write_verify(REG_FAN_MODE, value)
