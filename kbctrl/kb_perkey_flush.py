#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_perkey_flush.py — Complete a stalled per-key frame sequence.

Theory: rgb_tui.py sent 0x16 00 00 (custom/per-key mode) then stopped.
The EC is now waiting for per-key frame data before it will accept any
other commands. We satisfy the wait by sending 128 indexed 0x08 02 frames
(all black/off), then commit, then immediately select a visible effect.
"""

import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806
DEVICE = "/dev/hidraw0"

def pad(pkt):
    return [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))

def send(fd, pkt, label=""):
    buf = array.array("B", pad(pkt))
    if label:
        print(f"  [{label:<26}] {' '.join(f'{x:02X}' for x in pkt)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def commit(fd):
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def main():
    print(f"Opening {DEVICE}...")
    try:
        fd = os.open(DEVICE, os.O_RDWR)
    except OSError as e:
        print(f"[!] {e}")
        sys.exit(1)

    # --- Phase 1: complete the stalled per-key sequence ---
    print("\nPhase 1: Completing stalled per-key sequence...")
    print("  Sending 128 indexed per-key frames (all black) to satisfy EC buffer...")
    for idx in range(128):
        # 08 02 [idx] [R] [G] [B] 00 [enabled=0 → off]
        send(fd, [0x08, 0x02, idx, 0x00, 0x00, 0x00, 0x00, 0x00])
        if idx % 16 == 0:
            print(f"  ... frame {idx}/128")
        time.sleep(0.02)
    commit(fd)
    time.sleep(0.3)
    print("  Done. Waiting 1s...")
    time.sleep(1.0)

    # --- Phase 2: force-select Rainbow Wave ---
    print("\nPhase 2: Forcing Rainbow Wave (0x05)...")
    send(fd, [0x16, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00], "Select Rainbow 0x05")
    send(fd, [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00], "Params speed/bright")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)
    time.sleep(0.5)

    # --- Phase 3: also try static white via both methods ---
    print("\nPhase 3: Static white (kbctrl method)...")
    send(fd, [0x14, 0x01, 0x01, 0xFF, 0xFF, 0xFF, 0x00, 0x00], "0x14 mono white")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)
    time.sleep(0.3)

    print("\nPhase 4: Static white (hidpoke/global method)...")
    send(fd, [0x16, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00], "Select Static 0x01")
    send(fd, [0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00], "Params brightness")
    send(fd, [0x08, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00], "0x08 global white")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)

    os.close(fd)
    print("\nDone. Did the keyboard respond?")

if __name__ == "__main__":
    main()
