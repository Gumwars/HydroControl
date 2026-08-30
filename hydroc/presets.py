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
preset is active. We cannot colour it blue/green/purple -- that was the OEM service
writing state we have not found, and probing says it is not in the EC at all.
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
