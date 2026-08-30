# SPDX-License-Identifier: MIT
"""
kbctrl.config — XDG-aware profile storage.

Profile is stored as JSON at ~/.config/kbctrl/profile.json.
It captures the full keyboard state so kbctrl-apply can restore it at boot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "kbctrl"
PROFILE_PATH = CONFIG_DIR / "profile.json"


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_profile() -> dict:
    try:
        with open(PROFILE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_profile(profile: dict) -> None:
    _ensure_dir()
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)


# ── Helpers to convert between JSON-serialisable types and runtime types ──────

def encode_key_colors(key_colors: dict[tuple[int, int], tuple[int, int, int]]) -> dict:
    """Convert {(row, col): (r,g,b)} → {"row,col": [r,g,b]}"""
    return {f"{r},{c}": list(rgb) for (r, c), rgb in key_colors.items()}


def decode_key_colors(raw: dict) -> dict[tuple[int, int], tuple[int, int, int]]:
    """Convert {"row,col": [r,g,b]} → {(row, col): (r,g,b)}"""
    result = {}
    for key, val in raw.items():
        try:
            r, c = map(int, key.split(","))
            result[(r, c)] = tuple(val)
        except (ValueError, TypeError):
            pass
    return result
