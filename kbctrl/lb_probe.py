#!/usr/bin/env python3
"""
lb_probe.py — Interactive ITE 8233 lightbar reverse-engineering tool.

Sends candidate packets to /dev/hidraw1 one at a time, pauses for you
to observe the lightbar, then records your annotation to lb_probe_log.json.

Run with:  sudo python3 lb_probe.py
Results:   lb_probe_log.json  (append-only, safe to re-run)

Usage tips
----------
- Press Enter to skip a packet (no visible change → just move on).
- Type a short description of what happened, e.g.  "breathing blue"
- Type "x" to skip the rest of the current group and jump to the next.
- Type "q" to quit and save progress.
"""

import array
import fcntl
import json
import os
import sys
import time
from pathlib import Path

DEVICE = "/dev/hidraw1"
LOG_PATH = Path("lb_probe_log.json")
HIDIOCSFEATURE_9 = 0xC0094806
_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])


# ── low-level send ─────────────────────────────────────────────────────────────

def _send_raw(pkt8: bytes, commit: bool = True) -> str:
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")
    try:
        fd = os.open(DEVICE, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
            if commit:
                fcntl.ioctl(fd, HIDIOCSFEATURE_9,
                            array.array("B", bytes([0x00]) + _COMMIT), True)
        finally:
            os.close(fd)
        return ""
    except OSError as e:
        return str(e)


def send(pkt8: bytes, commit: bool = True) -> bool:
    hex_str = " ".join(f"{b:02X}" for b in pkt8)
    err = _send_raw(pkt8, commit)
    if err:
        print(f"  !! ERROR: {err}")
        return False
    return True


# ── probe groups ───────────────────────────────────────────────────────────────

def group_brightness():
    """09 02 XX — brightness control (00 = off is confirmed)."""
    for level in [0, 5, 10, 20, 30, 40, 50]:
        yield f"09 02 brightness={level}", bytes([0x09, 0x02, level, 0x00, 0x00, 0x00, 0x00, 0x00])


def group_static_color():
    """14 01 01 RR GG BB — static mono color (same family as keyboard)."""
    colors = [
        ("red",   0xFF, 0x00, 0x00),
        ("green", 0x00, 0xFF, 0x00),
        ("blue",  0x00, 0x00, 0xFF),
        ("white", 0xFF, 0xFF, 0xFF),
    ]
    for name, r, g, b in colors:
        yield f"14 01 01 static-{name}", bytes([0x14, 0x01, 0x01, r, g, b, 0x00, 0x00])


def group_static_color_alt():
    """14 00 01 RR GG BB — alternate static (slot 0 instead of mode 01)."""
    colors = [
        ("red",  0xFF, 0x00, 0x00),
        ("blue", 0x00, 0x00, 0xFF),
    ]
    for name, r, g, b in colors:
        yield f"14 00 01 alt-static-{name}", bytes([0x14, 0x00, 0x01, r, g, b, 0x00, 0x00])


def group_effect_08():
    """08 02 XX — effect selector sweep (XX = effect ID 0x00..0x15)."""
    # Speed=5, brightness=25, color=1 (blue slot), direction=1
    for eid in range(0x00, 0x16):
        yield (f"08 02 eid=0x{eid:02X} spd=5 bri=25 col=1 dir=1",
               bytes([0x08, 0x02, eid, 0x05, 0x19, 0x01, 0x01, 0x00]))


def group_effect_08_nodirection():
    """08 02 XX — same sweep but direction=0 (some effects ignore direction)."""
    for eid in [0x02, 0x03, 0x04, 0x05, 0x06, 0x0A, 0x0E, 0x11]:
        yield (f"08 02 eid=0x{eid:02X} dir=0",
               bytes([0x08, 0x02, eid, 0x05, 0x19, 0x01, 0x00, 0x00]))


def group_effect_16():
    """16 00 XX — effect-select family used by keyboard (may differ on lightbar)."""
    for eid in range(0x00, 0x10):
        yield (f"16 00 eid=0x{eid:02X}",
               bytes([0x16, 0x00, eid, 0x00, 0x00, 0x00, 0x00, 0x00]))


def group_palette():
    """14 00 slot RR GG BB — palette slot writes (no commit, then commit)."""
    yield ("14 00 slot=1 blue (no commit)",
           bytes([0x14, 0x00, 0x01, 0x00, 0x00, 0xFF, 0x00, 0x00]))
    # after slot write, does 08 02 effect need re-send?
    yield ("08 02 eid=0x02 after palette slot write",
           bytes([0x08, 0x02, 0x02, 0x05, 0x19, 0x01, 0x01, 0x00]))


GROUPS = [
    ("brightness (09 02 XX)", group_brightness),
    ("static color (14 01 01 RR GG BB)", group_static_color),
    ("static color alt (14 00 01 RR GG BB)", group_static_color_alt),
    ("effect sweep 08 02 XX (with direction=1)", group_effect_08),
    ("effect sweep 08 02 XX (no direction)", group_effect_08_nodirection),
    ("effect sweep 16 00 XX", group_effect_16),
    ("palette slot + effect", group_palette),
]


# ── log helpers ────────────────────────────────────────────────────────────────

def load_log() -> list[dict]:
    if LOG_PATH.exists():
        try:
            return json.loads(LOG_PATH.read_text())
        except Exception:
            pass
    return []


def save_log(entries: list[dict]) -> None:
    LOG_PATH.write_text(json.dumps(entries, indent=2))


def already_tested(entries: list[dict], label: str) -> str | None:
    for e in entries:
        if e["label"] == label:
            return e.get("result", "")
    return None


# ── interactive loop ───────────────────────────────────────────────────────────

def run_probe():
    entries = load_log()
    print(f"\n{'='*60}")
    print("  ITE 8233 lightbar probe")
    print(f"  Device: {DEVICE}")
    print(f"  Log:    {LOG_PATH}")
    print(f"  Previously logged: {len(entries)} entries")
    print("="*60)
    print("\nFor each packet:")
    print("  <Enter>     = skip (no change)")
    print("  description = describe what you saw")
    print("  'x'         = skip to next group")
    print("  'r'         = re-send this packet (look again)")
    print("  'q'         = quit and save")
    print()

    # First, always turn off so we start from known state
    print("Turning lightbar off (09 02 00) as baseline...")
    _send_raw(bytes([0x09, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
    time.sleep(0.5)

    try:
        for group_name, group_fn in GROUPS:
            print(f"\n{'─'*60}")
            print(f"GROUP: {group_name}")
            print('─'*60)

            skip_group = False
            for label, pkt in group_fn():
                if skip_group:
                    break

                prior = already_tested(entries, label)
                if prior is not None:
                    print(f"  [skip, already logged: {label!r} → {prior!r}]")
                    continue

                hex_str = " ".join(f"{b:02X}" for b in pkt)
                print(f"\n  Sending: {hex_str}")
                print(f"  Label:   {label}")

                while True:
                    ok = send(pkt)
                    if not ok:
                        break
                    time.sleep(0.3)
                    ans = input("  What happened? ").strip()

                    if ans == "q":
                        save_log(entries)
                        print(f"\nSaved {len(entries)} entries to {LOG_PATH}")
                        sys.exit(0)
                    elif ans == "x":
                        skip_group = True
                        # Turn off after each group
                        _send_raw(bytes([0x09, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
                        break
                    elif ans == "r":
                        # re-send and ask again
                        continue
                    else:
                        entries.append({"label": label, "packet": hex_str, "result": ans})
                        save_log(entries)
                        break

                # turn off between tests so changes are obvious
                _send_raw(bytes([0x09, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]))
                time.sleep(0.2)

    except KeyboardInterrupt:
        pass

    save_log(entries)
    print(f"\nSaved {len(entries)} entries to {LOG_PATH}")
    print_summary(entries)


def print_summary(entries: list[dict]) -> None:
    interesting = [e for e in entries if e.get("result", "").strip()]
    if not interesting:
        print("\nNo interesting results logged yet.")
        return
    print(f"\n{'='*60}")
    print("  RESULTS (non-empty observations)")
    print("="*60)
    for e in interesting:
        print(f"  {e['packet']}")
        print(f"    → {e['result']}")
        print()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: must run as root (sudo python3 lb_probe.py)", file=sys.stderr)
        sys.exit(1)
    if not Path(DEVICE).exists():
        print(f"ERROR: {DEVICE} not found", file=sys.stderr)
        sys.exit(1)
    run_probe()
