# SPDX-License-Identifier: MIT
"""
kbctrl.layout — Keyboard layout definitions and key-matrix loader.

Each key entry: (display_label, matrix_row, matrix_col, display_width_chars)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent.parent  # repo root
KEY_MATRIX_PATH = _SCRIPT_DIR / "key_matrix.json"

# ── Short display labels for verbose scan names ───────────────────────────────
KM_LABELS: dict[str, str] = {
    "Left Arrow": "←",    "Right Arrow": "→",
    "Up Arrow":   "↑",    "Down Arrow":  "↓",
    "Left Ctrl":  "Ctrl", "Right Ctrl":  "Ctrl",
    "Left Alt":   "Alt",  "Right Alt":   "Alt",
    "Left Shift": "Shift","Right Shift": "Shift",
    "Caps Lock":  "Caps", "Backspace":   "BkSp",
    "Delete":     "Del",  "Print Screen":"PrtSc",
    "ScreenCap":  "SnipT","Page Up":     "PgUp",
    "Page Down":  "PgDn", "Num Lock":    "NmLk",
    "Num Enter":  "NPEnt","Num +":       "NP+",
    "Num -":      "NP-",  "Num *":       "NP*",
    "Num /":      "NP/",  "Escape":      "Esc",
    "Num 0":      "NP0",  "Num .":       "NP.",
    "Num 1":      "NP1",  "Num 2":       "NP2",
    "Num 3":      "NP3",  "Num 4":       "NP4",
    "Num 5":      "NP5",  "Num 6":       "NP6",
    "Num 7":      "NP7",  "Num 8":       "NP8",
    "Num 9":      "NP9",  "Super":       "Win",
    "Enter":      "Ent",  "Home":        "Home",
    "End":        "End",
}

KM_WIDTHS: dict[str, int] = {
    "Space": 12,       "Left Shift": 7,  "Right Shift": 7,
    "Backspace": 6,    "Caps Lock": 6,   "Enter": 6,
    "Num Enter": 5,    "Tab": 5,
    "Left Ctrl": 5,    "Right Ctrl": 5,
    "Left Alt": 5,     "Right Alt": 5,
    "Super": 5,        "Fn": 4,
    "Num 0": 5,        "Escape": 4,
    "Delete": 4,       "Home": 4,        "End": 4,
    "Page Up": 5,      "Page Down": 5,
    "Print Screen": 5, "ScreenCap": 5,
    "Num Lock": 5,     "Num /": 4,
    "Num *": 4,        "Num -": 4,       "Num +": 5,
}

# Fallback layout (used when key_matrix.json is absent)
KEYBOARD_ROWS: list[list[tuple[str, int, int, int]]] = [
    [("Esc",  0, 0, 4), ("F1",  0, 1, 3), ("F2",  0, 2, 3), ("F3",  0, 3, 3),
     ("F4",   0, 4, 3), ("F5",  0, 5, 3), ("F6",  0, 6, 3), ("F7",  0, 7, 3),
     ("F8",   0, 8, 3), ("F9",  0, 9, 3), ("F10", 0,10, 3), ("F11", 0,11, 3),
     ("F12",  0,12, 3), ("Ins", 0,13, 3), ("Del", 0,14, 3),
     ("NmLk", 0,15, 4), ("NP/", 0,16, 4), ("NP*", 0,17, 4), ("NP-", 0,18, 4)],
    [("`",    1, 0, 3), ("1",   1, 1, 3), ("2",   1, 2, 3), ("3",   1, 3, 3),
     ("4",    1, 4, 3), ("5",   1, 5, 3), ("6",   1, 6, 3), ("7",   1, 7, 3),
     ("8",    1, 8, 3), ("9",   1, 9, 3), ("0",   1,10, 3), ("-",   1,11, 3),
     ("=",    1,12, 3), ("BkSp",1,13, 6),
     ("NP7",  1,15, 4), ("NP8", 1,16, 4), ("NP9", 1,17, 4), ("NP+", 1,18, 4)],
    [("Tab",  2, 0, 5), ("Q",   2, 1, 3), ("W",   2, 2, 3), ("E",   2, 3, 3),
     ("R",    2, 4, 3), ("T",   2, 5, 3), ("Y",   2, 6, 3), ("U",   2, 7, 3),
     ("I",    2, 8, 3), ("O",   2, 9, 3), ("P",   2,10, 3), ("[",   2,11, 3),
     ("]",    2,12, 3), ("\\",  2,13, 4),
     ("NP4",  2,15, 4), ("NP5", 2,16, 4), ("NP6", 2,17, 4)],
    [("Caps", 3, 0, 6), ("A",   3, 1, 3), ("S",   3, 2, 3), ("D",   3, 3, 3),
     ("F",    3, 4, 3), ("G",   3, 5, 3), ("H",   3, 6, 3), ("J",   3, 7, 3),
     ("K",    3, 8, 3), ("L",   3, 9, 3), (";",   3,10, 3), ("'",   3,11, 3),
     ("Ent",  3,12, 6),
     ("NP1",  3,15, 4), ("NP2", 3,16, 4), ("NP3", 3,17, 4), ("NPE", 3,18, 4)],
    [("Shift",4, 0, 7), ("Z",   4, 1, 3), ("X",   4, 2, 3), ("C",   4, 3, 3),
     ("V",    4, 4, 3), ("B",   4, 5, 3), ("N",   4, 6, 3), ("M",   4, 7, 3),
     (",",    4, 8, 3), (".",   4, 9, 3), ("/",   4,10, 3), ("Shft",4,11, 6),
     ("NP0",  4,15, 5), ("NP.", 4,16, 4)],
    [("Ctrl", 5, 0, 5), ("Win", 5, 1, 4), ("Alt", 5, 2, 4), ("Space",5,3,12),
     ("Alt",  5, 4, 4), ("Fn",  5, 5, 3),
     ("←",    5,13, 4), ("↑",   5,14, 4), ("↓",   5,15, 4), ("→",   5,16, 4)],
]


def load_key_matrix(path: Path | None = None) -> list | None:
    """
    Load key_matrix.json and return a KEYBOARD_ROWS-compatible list,
    or None if the file is absent/invalid.
    Matrix rows are stored as 5→0 (top→bottom physically), so we reverse.
    """
    if path is None:
        path = KEY_MATRIX_PATH
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    by_row: dict[int, list] = {}
    for key_str, label in data.items():
        r, c = map(int, key_str.split(","))
        by_row.setdefault(r, []).append((c, label))
    for r in by_row:
        by_row[r].sort()

    result = []
    for matrix_row in sorted(by_row.keys(), reverse=True):
        row_keys = []
        for col, label in by_row[matrix_row]:
            display = KM_LABELS.get(label, label)
            width   = KM_WIDTHS.get(label, 4)
            row_keys.append((display, matrix_row, col, width))
        result.append(row_keys)
    return result
