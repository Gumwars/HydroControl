# SPDX-License-Identifier: MIT
"""
hydroc.displaymode — Control Center's display colour modes, as a colour matrix.

What Windows does (decompiled, `DisplayFeatureManager_Intel` / `ColorGamma`):
every mode is one call, `Set(brightness, R, G, B, colourTemp, contrast)`,
which builds a 256-entry gamma ramp per channel --

    ramp[x] = contrast * (x/256)^(1/gamma) * 65535 + offset * 255

-- and loads it with `SetDeviceGammaRamp`. Brightness, R/G/B and temperature
are all additive *offsets* into that ramp. Its presets:

    Standard   neutral
    Gaming     neutral (identical to Standard until edited)
    Video      brightness 100 instead of 90: +10 offset, about +4% of full scale
    Read       Blue 0 instead of 128: blue offset -128, half of full scale
    Custom     the sliders, neutral by default

Why that cannot be copied here: the gamma ramp is where Hyprland loads the
panel's factory calibration (the ICC profile's `vcgt` curve, see
colorprofile.py). A mode written there replaces the calibration. Hyprland has
a second stage that does not -- a per-output 3x3 colour matrix (CTM), the one
hyprsunset uses -- so modes are matrices here, applied on top of calibration.

A matrix is linear, so it expresses gains, not offsets. The translation keeps
each mode's intent:

    brightness   a uniform gain, percent (100 neutral)
    R / G / B    0..255 as in Control Center, 128 neutral: gain 0.5 + v/256,
                 so Read's Blue 0 is blue at half -- the same half-of-full-scale
                 cut, as a gain instead of a subtraction
    temperature  Kelvin, 6500 neutral -- the panel's calibrated white point
                 (the profile says D6500), not Control Center's arbitrary 4200

Contrast and Video's lifted blacks have no matrix equivalent and are not
offered rather than faked.

Pure: no I/O. The Wayland client is displayd.py.
"""

from __future__ import annotations

import math

NEUTRAL = {"brightness": 100, "red": 128, "green": 128, "blue": 128,
           "temperature": 6500}

# Ranges the UI and the service both enforce.
LIMITS = {"brightness": (50, 110), "red": (0, 255), "green": (0, 255),
          "blue": (0, 255), "temperature": (2500, 10000)}

MODES = {
    "standard": {"name": "Standard", "params": dict(NEUTRAL)},
    "gaming": {"name": "Gaming", "params": dict(NEUTRAL)},
    "video": {"name": "Video", "params": {**NEUTRAL, "brightness": 104}},
    "read": {"name": "Read", "params": {**NEUTRAL, "blue": 0}},
    "custom": {"name": "Custom", "params": dict(NEUTRAL)},
}
ORDER = ("standard", "gaming", "video", "read", "custom")

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def clamp_params(params: dict) -> dict:
    """Fill gaps from NEUTRAL and clamp every value into LIMITS."""
    out = {}
    for k, (lo, hi) in LIMITS.items():
        v = params.get(k, NEUTRAL[k])
        try:
            v = int(round(float(v)))
        except (TypeError, ValueError):
            v = NEUTRAL[k]
        out[k] = min(hi, max(lo, v))
    return out


def _kelvin_rgb(k: float) -> tuple[float, float, float]:
    """Blackbody colour, 0..1 per channel (Tanner Helland's fit)."""
    t = k / 100.0
    if t <= 66:
        r = 1.0
        g = (99.4708025861 * math.log(t) - 161.1195681661) / 255
        b = 0.0 if t <= 19 else (138.5177312231 * math.log(t - 10) - 305.0447927307) / 255
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592 / 255
        g = 288.1221695283 * (t - 60) ** -0.0755148492 / 255
        b = 1.0
    return tuple(min(1.0, max(0.0, c)) for c in (r, g, b))


def temperature_gains(kelvin: float) -> tuple[float, float, float]:
    """Per-channel gains that move white from 6500 K to `kelvin`.

    Relative to 6500 K, so 6500 is exactly identity, then scaled so the
    largest gain is 1: a temperature shift never brightens a channel past
    what the panel shows now, so nothing clips.
    """
    ref = _kelvin_rgb(6500)
    raw = [c / r for c, r in zip(_kelvin_rgb(kelvin), ref)]
    top = max(raw)
    return tuple(c / top for c in raw)


def channel_gain(v: int) -> float:
    """Control Center's 0..255 channel slider as a gain: 128 -> 1.0."""
    return 1.0 if v == 128 else 0.5 + v / 256


def matrix(params: dict) -> tuple[float, ...]:
    """Row-major 3x3 for hyprland_ctm_control_v1. All entries >= 0."""
    p = clamp_params(params)
    if p == NEUTRAL:
        return IDENTITY
    t = temperature_gains(p["temperature"])
    b = p["brightness"] / 100
    r, g, bl = (b * t[i] * channel_gain(p[c])
                for i, c in enumerate(("red", "green", "blue")))
    return (r, 0.0, 0.0, 0.0, g, 0.0, 0.0, 0.0, bl)


def new_state() -> dict:
    return {"mode": "standard",
            "modes": {k: dict(v["params"]) for k, v in MODES.items()}}


def normalise_state(state: dict | None) -> dict:
    """A saved state with unknown modes dropped and every mode present."""
    out = new_state()
    if not isinstance(state, dict):
        return out
    if state.get("mode") in MODES:
        out["mode"] = state["mode"]
    for k, v in (state.get("modes") or {}).items():
        if k in MODES and isinstance(v, dict):
            out["modes"][k] = clamp_params(v)
    return out


def describe(state: dict) -> list[dict]:
    """The mode list the UI renders."""
    return [{"id": k, "name": MODES[k]["name"], "params": state["modes"][k],
             "default": MODES[k]["params"],
             "edited": state["modes"][k] != MODES[k]["params"]}
            for k in ORDER]
