#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_restore.py — Emergency recovery for stuck Uniwill/Tongfang keyboard EC.

Symptom: keyboard RGB unresponsive after per-key mode was selected without
completing the full frame transfer. The EC stalls waiting for frame data.

Strategy:
  1. Flush any stalled per-key buffer with empty 0x08 frames
  2. Force Rainbow Wave (0x05) — no per-key dependency, should always work
  3. If user reports no change: try static mono via 0x08/global method
  4. If still nothing: try Breathing effect (often un-sticks firmware)

All known VID/PID pairs are tried during device discovery.
"""

import os, fcntl, array, sys, time

HIDIOCSFEATURE = 0xC0094806

KNOWN_VIDS_PIDS = [
    ("0000048D", "00006006"),   # ITE Tech (primary)
    ("0000093A", "00000255"),   # Uniwill
    ("0000048D", "0000600B"),   # ITE Tech variant (seen in some builds)
]

def open_ec(max_devs=20):
    for i in range(max_devs):
        path = f"/dev/hidraw{i}"
        uevent = f"/sys/class/hidraw/hidraw{i}/device/uevent"
        try:
            fd = os.open(path, os.O_RDWR)
            with open(uevent, "r") as f:
                data = f.read().upper()
            for vid, pid in KNOWN_VIDS_PIDS:
                if f"{vid}:{pid}" in data:
                    print(f"[+] Found keyboard EC at {path}  ({vid}:{pid})")
                    return fd, path
            os.close(fd)
        except (OSError, FileNotFoundError):
            pass
    print("[-] No keyboard HID device found.")
    print("    Make sure you are running with sudo, or have udev rules granting access.")
    sys.exit(1)

def send(fd, pkt, label):
    padded = [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))
    buf = array.array("B", padded)
    print(f"    [{label:<22}] {' '.join(f'{x:02X}' for x in pkt)}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def commit(fd):
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "Commit")

def pause(msg):
    print()
    input(f"  >> {msg} — press Enter to continue...")
    print()

def attempt_flush(fd):
    """Flush any stalled per-key buffer by sending 16 empty 0x08 frames."""
    print("[*] Flushing stalled per-key buffer (16 empty frames)...")
    for i in range(16):
        send(fd, [0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], f"Flush {i:02d}")
        time.sleep(0.05)
    commit(fd)
    time.sleep(0.2)

def attempt_rainbow(fd):
    """Select Rainbow Wave (0x05). No per-key data needed."""
    print("[*] Attempt A: Rainbow Wave (effect 0x05) at full brightness...")
    send(fd, [0x16, 0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x00], "Select Rainbow")
    send(fd, [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00], "Params speed/bright")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)

def attempt_static_mono(fd):
    """Static mono via 0x08/global method (as seen in hidpoke.py captures)."""
    print("[*] Attempt B: Static mono — white via global 0x08 color method...")
    send(fd, [0x16, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00], "Select Static(0x01)")
    send(fd, [0x14, 0x01, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00], "Params brightness")
    send(fd, [0x08, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00], "Global color white")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)

def attempt_breathing(fd):
    """Breathing effect — often un-sticks a firmware that ignores static commands."""
    print("[*] Attempt C: Breathing red (effect 0x06)...")
    send(fd, [0x16, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00], "Select Breathing")
    send(fd, [0x14, 0x01, 0x00, 0x0A, 0x01, 0xFF, 0x00, 0x00], "Params speed/bright")
    send(fd, [0x08, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00], "Global color red")
    send(fd, [0x09, 0x02, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00], "Brightness 100%")
    commit(fd)

def main():
    print("=" * 60)
    print("  kb_restore.py — Keyboard RGB recovery")
    print("=" * 60)
    print()

    fd, devpath = open_ec()

    # Step 1: always flush first
    attempt_flush(fd)

    # Step 2: Rainbow Wave
    attempt_rainbow(fd)
    pause("Did the keyboard light up with a rainbow wave?  (y=done / Enter=try next)")

    # Step 3: Static mono
    attempt_static_mono(fd)
    pause("Is the keyboard showing solid white?  (y=done / Enter=try next)")

    # Step 4: Breathing
    attempt_breathing(fd)
    pause("Is the keyboard doing a breathing red pulse?  (y=done / Enter=continue)")

    print("[*] All attempts completed.")
    print("    If none worked, the EC may need a power cycle (full shutdown + unplug).")
    print("    If something did work, run:  python3 rgb_tui.py  to resume normal control.")

    os.close(fd)

if __name__ == "__main__":
    main()
