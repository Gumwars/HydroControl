#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_probe_nodes.py — Send test packets to both ITE hidraw nodes independently
so we can determine which one is the keyboard vs. the light bar.

Targets:
  hidraw0  048D:600B  ITE Device(8291)
  hidraw1  048D:7001  ITE Device(8233)

For each node, sends a distinctive visible color (red then green then blue)
with a pause between so you can observe which zone reacts.
"""

import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806

NODES = [
    ("/dev/hidraw0", "048D:600B", "ITE 8291"),
    ("/dev/hidraw1", "048D:7001", "ITE 8233"),
]

def pad(pkt):
    return [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))

def send(fd, pkt, label):
    buf = array.array("B", pad(pkt))
    print(f"    [{label:<24}] {' '.join(f'{x:02X}' for x in pkt)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def commit(fd):
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def pause(msg):
    input(f"\n  >> {msg}\n     Press Enter to continue...")
    print()

def try_color_sequence(fd, r, g, b, name):
    """Try three different protocol approaches for setting a color."""
    # Approach 1: kbctrl method (0x14 mono color)
    send(fd, [0x14, 0x01, 0x01, r, g, b, 0x00, 0x00], f"0x14 mono {name}")
    commit(fd)
    time.sleep(0.15)

    # Approach 2: hidpoke method (0x16 select static + 0x08 global color)
    send(fd, [0x16, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00], "0x16 select static")
    send(fd, [0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00], "0x14 params")
    send(fd, [0x08, 0x00, r, g, b, 0x00, 0x00, 0x00],           f"0x08 global {name}")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00],  "0x09 bright 100%")
    commit(fd)
    time.sleep(0.15)

    # Approach 3: rainbow wave (no color dependency — just see if it moves)
    send(fd, [0x16, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00], "0x16 rainbow wave")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "0x09 bright 100%")
    commit(fd)

def probe_node(devpath, vid_pid, chip_name):
    print(f"\n{'='*60}")
    print(f"  Probing {devpath}  ({vid_pid}  {chip_name})")
    print(f"{'='*60}")

    try:
        fd = os.open(devpath, os.O_RDWR)
    except OSError as e:
        print(f"  [!] Cannot open {devpath}: {e}")
        return

    print("\n  Phase 1: FLUSH — sending 16 empty 0x08 frames to clear any stalled state...")
    for i in range(16):
        try:
            send(fd, [0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f"Flush {i:02d}")
        except Exception:
            pass
        time.sleep(0.04)
    try:
        commit(fd)
    except Exception:
        pass
    time.sleep(0.3)

    print("\n  Phase 2: RED sequence (all three protocol approaches)...")
    try:
        try_color_sequence(fd, 0xFF, 0x00, 0x00, "RED")
    except Exception as e:
        print(f"    [error: {e}]")

    pause(f"[{devpath}] Did anything light up RED on keyboard OR light bar?")

    print("  Phase 3: GREEN sequence...")
    try:
        try_color_sequence(fd, 0x00, 0xFF, 0x00, "GREEN")
    except Exception as e:
        print(f"    [error: {e}]")

    pause(f"[{devpath}] Did anything change to GREEN?")

    print("  Phase 4: BLUE sequence...")
    try:
        try_color_sequence(fd, 0x00, 0x00, 0xFF, "BLUE")
    except Exception as e:
        print(f"    [error: {e}]")

    pause(f"[{devpath}] Did anything change to BLUE?")

    os.close(fd)

def main():
    print("kb_probe_nodes.py — Identify keyboard vs. light bar hidraw node")
    print()
    print("Watch your keyboard AND light bar carefully during each phase.")
    print("We will probe each node separately with pause between each color.")
    print()
    input("Press Enter to begin probing hidraw0...")

    for devpath, vid_pid, chip_name in NODES:
        probe_node(devpath, vid_pid, chip_name)
        print(f"\n  [{devpath} done]")
        input("Press Enter to move to next node...")

    print("\n\nProbe complete.")
    print("Report back:")
    print("  - Which hidraw node caused the light bar to change?")
    print("  - Which hidraw node caused the keyboard to change (if any)?")
    print("  - Did the keyboard respond to anything at all?")

if __name__ == "__main__":
    main()
