#!/usr/bin/env python3
"""
lb_sequence_test.py — Full multi-step sequences for the ITE 8233 lightbar.

Single-command probes (family 0x16/0x14/0x08 alone) all failed, but the
keyboard's static colour only works as a 4-step SEQUENCE. This sends the
same sequences to the lightbar, varying the effect selector.

Keyboard baseline (works on 8291, from hidpoke.py):
    [16 00 01]               effect = static
    [14 01 00 00 00 FF 00 00] params (brightness 0xFF)
    [08 00 RR GG BB 00 00 00] colour
    [1A 00 01 04 00 00 00 01] commit

Observe the bar and answer:
    c = still colour-cycling     s = static / solid colour
    o = off / dark               ? = something else (describe)
    q = quit

Run: sudo python3 lb_sequence_test.py
Logs to lb_sequence_test_log.json
"""

import array
import fcntl
import glob
import json
import os
import time

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
LOG = "/home/gumwars/HydroControl/lb_sequence_test_log.json"

CC_EFFECTS = [1, 2, 3, 5, 9, 13, 32]

CODES = {
    "c": "still colour-cycling",
    "s": "STATIC / solid colour",
    "o": "off / dark",
}


def find_lightbar() -> str:
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            blob = open(os.path.join(p, "device", "uevent")).read().upper()
        except OSError:
            continue
        if "0000048D:00007001" in blob:
            return "/dev/" + os.path.basename(p)
    raise SystemExit("ITE 8233 lightbar (048d:7001) not found")


def feature(dev: str, pkt8: bytes) -> str:
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")
    try:
        fd = os.open(dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
        finally:
            os.close(fd)
        return "ok"
    except OSError as e:
        return f"FAILED: {e}"


def enable(dev: str) -> None:
    feature(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    feature(dev, LB_COMMIT)


def seq(dev: str, steps: list[bytes], label: str) -> str:
    """Run a multi-step sequence, commit at the end. Returns observation."""
    for i, pkt in enumerate(steps):
        feature(dev, pkt)
        time.sleep(0.15)
    time.sleep(1.5)
    try:
        obs = input(f"  {label}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "aborted"
    if obs == "q":
        return "quit"
    if obs == "?":
        try:
            return f"other: {input('      describe: ').strip()}"
        except (EOFError, KeyboardInterrupt):
            return "aborted"
    return CODES.get(obs, obs or "no change")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_sequence_test.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    enable(dev)
    print("Enabled (09 02 32). You should see the colour cycle.\n")

    seqs = []

    # Keyboard-style static colour, every CC effect id as the selector.
    for eff in CC_EFFECTS + [0]:
        seqs.append((
            f"kb_seq_eff{eff}_RED",
            [
                bytes([0x16, 0x00, eff, 0, 0, 0, 0, 0]),
                bytes([0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00]),
                bytes([0x08, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]),
                LB_COMMIT,
            ],
        ))

    # Same but effect id in byte 1 (16 XX 00).
    for eff in CC_EFFECTS + [0]:
        seqs.append((
            f"kb_seq_eff{eff}_b1_RED",
            [
                bytes([0x16, eff, 0x00, 0, 0, 0, 0, 0]),
                bytes([0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00]),
                bytes([0x08, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]),
                LB_COMMIT,
            ],
        ))

    # Palette-slot colour write (14 00 slot RR GG BB) + effect re-select.
    seqs.append((
        "palette_slot1_blue_eff32",
        [
            bytes([0x16, 0x00, 0x20, 0, 0, 0, 0, 0]),
            bytes([0x14, 0x00, 0x01, 0x00, 0x00, 0xFF, 0x00, 0x00]),
            LB_COMMIT,
        ],
    ))

    # Colour write via 08 with colour first (per-key style).
    seqs.append((
        "color_first_eff32",
        [
            bytes([0x08, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]),
            bytes([0x16, 0x00, 0x20, 0, 0, 0, 0, 0]),
            LB_COMMIT,
        ],
    ))

    results = []
    for label, steps in seqs:
        obs = seq(dev, steps, label)
        results.append({
            "label": label,
            "packet_hex": [s.hex(" ") for s in steps],
            "result": obs,
        })
        if obs in ("quit", "aborted"):
            break

    enable(dev)  # restore known-good state

    existing = []
    if os.path.exists(LOG):
        try:
            existing = json.load(open(LOG))
        except (OSError, ValueError):
            pass
    existing.extend(results)
    with open(LOG, "w") as fh:
        json.dump(existing, fh, indent=2)

    print("\n" + "=" * 60)
    for r in results:
        mark = " <-- HIT" if "STATIC" in r["result"] else ""
        print(f"  {r['label']:24s} {r['result']}{mark}")
    print("=" * 60)
    print(f"\nLogged to {LOG}")

    hits = [r for r in results if "STATIC" in r["result"]]
    if hits:
        print(f"\nWorking sequence(s): {', '.join(h['label'] for h in hits)}")
    else:
        print("\nNo static hit. Next: decompile SystrayComponent.exe / capture")
        print("on Windows for ground truth.")


if __name__ == "__main__":
    main()