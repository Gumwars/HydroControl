#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_output_restore.py — Try restoring keyboard via interrupt OUT (write) path.

The HID descriptor shows:
  Feature report: 8 bytes  (control endpoint, HIDIOCSFEATURE)
  Output report:  64 bytes (interrupt OUT endpoint, write())

We have been using ONLY HIDIOCSFEATURE (feature/control path).
The keyboard may also need 64-byte interrupt OUT writes for per-key data
and possibly for all commands.

This script tries four approaches in sequence, pausing between each:
  A. write() 64-byte rainbow command (treat everything as output)
  B. write() 64-byte per-key white fill for all keys, then feature commit
  C. Feature report rainbow + write() per-key white fill + feature commit
  D. write() all-white single-color global command variants
"""

import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806
DEVICE = "/dev/hidraw0"

def pad_feature(pkt):
    """Pad to 65 bytes for HIDIOCSFEATURE (report ID 0x00 + 64 bytes)."""
    return bytes([0x00]) + bytes(pkt) + bytes(64 - len(pkt))

def pad_output(pkt):
    """Pad to 64 bytes for write() (output report, no report ID prefix)."""
    return bytes(pkt) + bytes(64 - len(pkt))

def feat(fd, pkt, label):
    buf = array.array("B", pad_feature(pkt))
    print(f"  [FEAT {label:<22}] {' '.join(f'{x:02X}' for x in pkt)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def out(fd, pkt, label):
    data = pad_output(pkt)
    print(f"  [OUT  {label:<22}] {' '.join(f'{x:02X}' for x in pkt)}")
    os.write(fd, data)

def commit_feat(fd):
    feat(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def commit_out(fd):
    out(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def pause(msg):
    input(f"\n  >> {msg}\n     Press Enter to continue...")
    print()

def approach_A(fd):
    """All commands as 64-byte output reports."""
    print("\n=== Approach A: All commands via write() (interrupt OUT) ===")
    out(fd, [0x16, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00], "Rainbow 0x05")
    time.sleep(0.1)
    out(fd, [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00], "Params")
    time.sleep(0.1)
    out(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Bright 100%")
    time.sleep(0.1)
    commit_out(fd)

def approach_B(fd):
    """Per-key white fill via write(), commit via feature report."""
    print("\n=== Approach B: Per-key white via write() + feature commit ===")
    # First: select custom mode via feature
    feat(fd, [0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], "Custom mode")
    time.sleep(0.1)
    feat(fd, [0x14, 0x01, 0x03, 0x04, 0xFF, 0xFF, 0x71, 0x00], "Per-key params")
    time.sleep(0.1)

    # Send 64-byte per-key packets via write() — all white, enabled
    print("  Sending 128 per-key OUT packets (white)...")
    for idx in range(128):
        pkt = [0x08, 0x02, idx & 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x01]
        data = pad_output(pkt)
        os.write(fd, data)
        if idx % 32 == 0:
            print(f"    ... key {idx}/128")
        time.sleep(0.01)

    time.sleep(0.2)
    commit_feat(fd)

def approach_C(fd):
    """Rainbow via feature, then per-key white fill via write(), then commit."""
    print("\n=== Approach C: Rainbow feat + per-key white OUT + feat commit ===")
    feat(fd, [0x16, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00], "Rainbow 0x05")
    feat(fd, [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00], "Params")
    feat(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Bright 100%")
    time.sleep(0.1)

    # Also send per-key white via write() to clear any stalled buffer
    print("  Sending 64 per-key OUT packets (white) to clear buffer...")
    for idx in range(64):
        pkt = [0x08, 0x02, idx, 0xFF, 0xFF, 0xFF, 0x00, 0x01]
        os.write(fd, pad_output(pkt))
        time.sleep(0.01)

    time.sleep(0.2)
    commit_feat(fd)

def approach_D(fd):
    """Global/single-color variants all as write() output reports."""
    print("\n=== Approach D: Single-color global variants via write() ===")
    # Static mode + global white (hidpoke method)
    out(fd, [0x16, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00], "Static 0x01")
    time.sleep(0.1)
    out(fd, [0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00], "Params")
    time.sleep(0.1)
    out(fd, [0x08, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00], "Global white")
    time.sleep(0.1)
    out(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Bright 100%")
    time.sleep(0.1)
    commit_out(fd)
    time.sleep(0.1)
    # Also try with feature commit
    commit_feat(fd)

def main():
    print("kb_output_restore.py — Testing interrupt OUT (write) path")
    print()

    try:
        fd = os.open(DEVICE, os.O_RDWR)
    except OSError as e:
        print(f"[!] Cannot open {DEVICE}: {e}")
        sys.exit(1)

    try:
        approach_A(fd)
        pause("Approach A done — did the keyboard respond? (Enter to try next)")

        approach_B(fd)
        pause("Approach B done — did the keyboard respond? (Enter to try next)")

        approach_C(fd)
        pause("Approach C done — did the keyboard respond? (Enter to try next)")

        approach_D(fd)
        pause("Approach D done — did the keyboard respond?")

    except Exception as e:
        print(f"\n[!] Error: {e}")
        import traceback; traceback.print_exc()
    finally:
        os.close(fd)

    print("All approaches complete.")

if __name__ == "__main__":
    main()
