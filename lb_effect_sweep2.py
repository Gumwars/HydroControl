#!/usr/bin/env python3
"""
lb_effect_sweep2.py — Extended effect sweep for the ITE 8233 lightbar.

Stage 1 (this file):
  * 0x16 family, effect IDs 0x00-0x3F, in BOTH byte orders:
      16 XX 00 00 00 00 00 00   (XX = byte 1)
      16 00 XX 00 00 00 00 00   (XX = byte 2)
  * Re-tests the exact save_effect values Control Center stores for the
    lightbar zones in RGBKeyboard.reg: 1, 2, 3, 5, 9, 13, 32 (0x20).

Each candidate is sent twice (raw, then raw+commit) because the ITE family
often needs the 1A commit after a state change.

Observe the bar and answer:
    c = still colour-cycling     s = static / solid colour
    o = off / dark               ? = something else (describe)
    q = quit

Run: sudo python3 lb_effect_sweep2.py
Logs to lb_effect_sweep2_log.json
"""

import array
import fcntl
import glob
import json
import os
import time

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
LOG = "/home/gumwars/HydroControl/lb_effect_sweep2_log.json"

# save_effect values that Control Center actually persists for the lightbar.
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


def try_effect(dev: str, label: str, pkt8: bytes) -> str:
    """Send raw, then raw+commit. Returns observation string."""
    status = feature(dev, pkt8)
    feature(dev, LB_COMMIT)
    time.sleep(1.2)
    try:
        obs = input(f"  {label} [{status}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "aborted"
    if obs == "q":
        return "quit"
    if obs == "?":
        try:
            extra = input("      describe: ").strip()
            return f"other: {extra}" if extra else "other"
        except (EOFError, KeyboardInterrupt):
            return "aborted"
    return CODES.get(obs, obs or "no change")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_effect_sweep2.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    print("Enabling bar (09 02 32) -- you should see the usual colour cycle.\n")
    enable(dev)
    time.sleep(1.5)

    print("Sweeping 0x16 effect family 0x00-0x3F in both byte orders,")
    print("then the Control Center save_effect values.\n")
    print("  c = cycling   s = static   o = off   ? = other   q = quit\n")

    results = []
    candidates = []

    for eff in range(0x00, 0x40):
        candidates.append(
            (f"16_{eff:02X}_00", bytes([0x16, eff, 0, 0, 0, 0, 0, 0])))
        candidates.append(
            (f"16_00_{eff:02X}", bytes([0x16, 0, eff, 0, 0, 0, 0, 0])))

    for eff in CC_EFFECTS:
        candidates.append(
            (f"cc_save_effect_{eff}", bytes([0x16, eff, 0, 0, 0, 0, 0, 0])))
        candidates.append(
            (f"cc_save_effect_{eff}_b2", bytes([0x16, 0, eff, 0, 0, 0, 0, 0])))

    for label, pkt in candidates:
        obs = try_effect(dev, label, pkt)
        results.append({
            "label": label,
            "packet": pkt.hex(" "),
            "result": obs,
        })
        if obs == "quit":
            break
        if obs == "aborted":
            break

    enable(dev)  # restore the known-good cycling state

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
    hits = [r for r in results if "STATIC" in r["result"]]
    for r in results:
        mark = " <-- HIT" if "STATIC" in r["result"] else ""
        print(f"  {r['label']:22s} {r['result']}{mark}")
    print("=" * 60)
    print(f"\nLogged to {LOG}")

    if hits:
        print(f"\nStatic-capable id(s): {', '.join(h['label'] for h in hits)}")
    else:
        print("\nNo static hit in 0x16 family. The steady mode may live in")
        print("another family, or a sequence (see lb_sequence_test.py).")


if __name__ == "__main__":
    main()