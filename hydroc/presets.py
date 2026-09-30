# SPDX-License-Identifier: MIT
"""
hydroc.presets — named performance profiles, and the physical button that cycles them.

The machine's performance modes are not firmware state. The profile button raises
WMI 0xB0, uniwill-laptop turns that into KEY_F14 on the "Uniwill WMI hotkeys" input
device, and nothing else happens -- on Windows the OEM service is what decides the
mode and applies it. See DESIGN.md §3.7.

So a preset here is just a named bundle of settings we already drive, applied through
the same verified path as any other change. Nothing exotic:

    custom_profile    the 0x0727 bit 6 latch -- without it TDP writes are ignored
    cpu_pl1/2/4       watts (PL4 must be even; it is stored at half scale)
    gpu_ctgp_offset   watts on top of base TGP

One consequence worth being honest about in the UI: every preset arms the latch,
because that is what makes power-limit writes take effect. The EC drives the profile
LED white whenever that latch is armed, so the LED reads "Custom" no matter which
preset is active.

CORRECTED 2026-09-30. This used to add "we cannot colour it blue/green/purple --
that was the OEM service writing state we have not found, and probing says it is
not in the EC at all". Wrong on both counts, established by decompiling Control
Center and reading the registers back on the hardware:

  * The colour is not written at all. The EC derives it from 0x0751, the same
    register that selects the mode. Office reads green.
  * SetPowerLedStatus() writes 0x07A5 bits 1:0 and has no callers in the fan
    manager, so that register -- the one this project probed -- was never the
    mechanism.

What the OEM service actually does per mode is: load the fan table, CLEAR the
custom latch, write 0x0751 (0xA0 Office / 0x00 Gaming / 0x10 Turbo, +0x40 for
fan boost), and write PL1/PL2/PL4 = 0 meaning "use your own defaults". The EC
then applies per-mode limits it already holds:

    Gaming  0x0730-0x0733   75 / 75 / 125 W
    Office  0x0734-0x0737   45 / 45 / 125 W
    Turbo   0x07A7-0x07AA  205 / 205 / 200 W

So the presets below are not the machine's native modes -- they are Custom with
chosen numbers, which is a legitimate thing to offer but is not the same thing,
and it is why the LED never changes. Native mode support is a separate feature;
see TCC-SERVICE-FINDINGS.md. The hazard there is 0x0751 bit 7, which Office
sets: it is the bit that stopped the fans once on this machine, and Windows only
ever sets it with the fan tables already populated.
"""

from __future__ import annotations

# Firmware defaults on this chassis, measured with the latch disarmed:
# PL1 75, PL2 75, PL4 250. Presets sit either side of that.
#
# PL4 values must be EVEN -- half-scale storage means odd watts round down and
# would read back as drift that no re-apply could clear.
PRESETS: dict[str, dict] = {
    "office": {
        "name": "Office",
        "desc": "Quiet and cool. Enough for browsing, documents and calls.",
        "settings": {"custom_profile": True, "cpu_pl1": 35, "cpu_pl2": 45,
                     "cpu_pl4": 90, "gpu_ctgp_offset": 0},
    },
    "balanced": {
        "name": "Balanced",
        "desc": "Close to the firmware default, with some GPU headroom.",
        "settings": {"custom_profile": True, "cpu_pl1": 75, "cpu_pl2": 90,
                     "cpu_pl4": 150, "gpu_ctgp_offset": 10},
    },
    "performance": {
        "name": "Performance",
        "desc": "Everything the chassis will give. Loud under sustained load.",
        "settings": {"custom_profile": True, "cpu_pl1": 110, "cpu_pl2": 125,
                     "cpu_pl4": 250, "gpu_ctgp_offset": 25},
    },
}

# The order the physical button walks. "custom" is deliberately not in it: it is
# where you land by changing a slider, not somewhere you cycle to.
CYCLE = ["office", "balanced", "performance"]

CUSTOM = "custom"

PRESET_KEYS = ("custom_profile", "cpu_pl1", "cpu_pl2", "cpu_pl4", "gpu_ctgp_offset")


def fan_for(name: str) -> dict | None:
    """The fan curve pair belonging to a preset, or None.

    Deliberately NOT part of ["settings"]. match() compares every settings key
    against live state, and the curves are only readable while manual fan
    control is on -- folding them in would make every preset report "custom"
    whenever the fans are on firmware control, which is most of the time.
    Power and cooling are applied together; they are compared separately.
    """
    from . import fancurve
    pair = fancurve.PRESET_CURVES.get(name)
    return {"cpu": [list(p) for p in pair["cpu"]],
            "gpu": [list(p) for p in pair["gpu"]]} if pair else None


def match(state: dict) -> str:
    """Which preset the hardware currently matches, or 'custom'.

    Compares against live hardware rather than trusting a stored name, so a
    preset that partly failed to apply -- or an EC that reverted at power-on --
    reports honestly instead of claiming a mode it is not in.
    """
    for key, preset in PRESETS.items():
        if all(state.get(k) == v for k, v in preset["settings"].items()):
            return key
    return CUSTOM


def settings_for(name: str) -> dict | None:
    preset = PRESETS.get(name)
    return dict(preset["settings"]) if preset else None


def next_in_cycle(current: str) -> str:
    """Advance the button. Anything unrecognised starts the cycle over."""
    if current not in CYCLE:
        return CYCLE[0]
    return CYCLE[(CYCLE.index(current) + 1) % len(CYCLE)]


def describe() -> list[dict]:
    """Preset list for the UI, in cycle order."""
    return [{"id": k, "name": PRESETS[k]["name"], "desc": PRESETS[k]["desc"],
             "settings": PRESETS[k]["settings"]} for k in CYCLE]
