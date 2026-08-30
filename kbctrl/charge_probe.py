#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
charge_probe.py — HID feature report probe for battery charge limit on HYDROC-16 G1.

Usage:
  sudo python3 charge_probe.py --set 80              # try all candidates at 80%
  sudo python3 charge_probe.py --set 80 --cmd 0x08   # try one specific command byte
  sudo python3 charge_probe.py --scan 80             # walk 0x00–0x1F, prompt each

The EC write is one-way (no reliable readback). Verification is behavioural:
plug in, charge to target, observe whether charging stops at the set level.

Protocol notes (from GCUBridge / kbctrl analysis):
  RGB keyboard uses command families 0x08 (data) and 0x1A (commit).
  GCUBridge topics: RGBKeyboard08, RGBKeyboard1A, SmartLightbar08, SmartLightbar1A,
  PowerMode, BatteryLimitation (packed in FWBuffer / NvramVariable).
  Packet format: [cmd, sub, data...] padded to 64 bytes + 1-byte report-ID prefix.
"""

import os, fcntl, array, sys, time, argparse

HIDIOCSFEATURE = 0xC0094806

# ── EC device detection ────────────────────────────────────────────────────────

def open_ec():
    for i in range(10):
        try:
            fd = os.open(f"/dev/hidraw{i}", os.O_RDWR)
            with open(f"/sys/class/hidraw/hidraw{i}/device/uevent") as f:
                ue = f.read().upper()
                if "0000048D:00006006" in ue or "0000048D:0000600B" in ue or "0000093A:00000255" in ue:
                    print(f"[+] Found EC at /dev/hidraw{i}")
                    return fd
            os.close(fd)
        except Exception:
            pass
    print("[-] EC hidraw not found (try running as root).")
    sys.exit(1)

# ── Packet helpers ─────────────────────────────────────────────────────────────

def send(fd, pkt, label=""):
    padded = [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))
    buf = array.array("B", padded)
    tag = f"[{label}]" if label else ""
    print(f"  {tag:20} TX: {' '.join(f'{x:02X}' for x in pkt[:12])}{'...' if len(pkt) > 12 else ''}")
    fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)

def commit(fd):
    send(fd, [0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01], "commit 0x1A")

# ── Candidate sequences ────────────────────────────────────────────────────────
#
# Each entry: (label, packets_list, do_commit)
# packets_list is a list of (tag, [bytes]) pairs.
#
# Reasoning for each candidate:
#   0x08 / sub=0x02 : 0x08 is EC data family; sub=0x02 seen in kbctrl for per-key.
#                     Battery might use sub=0x02 or 0x03 with limit% at byte 2.
#   0x08 / sub=0x01 : Another common sub-command in this family.
#   0x12            : Used for power/thermal settings on some Tongfang/Clevo SKUs.
#   0x0B            : Observed on related ITE EC variants for system config writes.
#   0x0C            : Adjacent; fan/power on some boards.
#   0x15            : Used in Tuxedo driver for battery charge on related hardware.
#   0x06            : Low-numbered family; sometimes system/EC init or power policy.

def build_candidates(limit):
    pct = limit & 0xFF
    return [
        ("0x08 sub=0x02 (data family)",
         [("charge pkt", [0x08, 0x02, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x08 sub=0x01",
         [("charge pkt", [0x08, 0x01, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x08 sub=0x03 (raindrop sub seen in EFI var)",
         [("charge pkt", [0x08, 0x03, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x12 (power/thermal family)",
         [("charge pkt", [0x12, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x12 sub=0x01",
         [("charge pkt", [0x12, 0x01, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x0B (system config)",
         [("charge pkt", [0x0B, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x0C (fan/power adjacent)",
         [("charge pkt", [0x0C, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x15 (Tuxedo battery candidate)",
         [("charge pkt", [0x15, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        ("0x06 (system policy)",
         [("charge pkt", [0x06, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
        # PowerMode-style: profile index (0=full, 1=balanced, 2=stationary)
        # GCUBridge has 'PowerMode' as a topic — maybe charge profile is packed here
        ("0x16 sub=0x02 (mode family, profile index 2=stationary)",
         [("mode pkt",   [0x16, 0x00, 0x02]),
          ("charge pkt", [0x14, 0x02, pct, 0x00, 0x00, 0x00, 0x00, 0x00])],
         True),
    ]

# ── Modes ─────────────────────────────────────────────────────────────────────

def run_candidates(fd, limit, only_cmd=None):
    candidates = build_candidates(limit)
    if only_cmd is not None:
        # Filter to just the matching first-byte
        candidates = [(l, p, c) for l, p, c in candidates
                      if p and p[0][1][0] == only_cmd]
        if not candidates:
            print(f"[!] No candidate found for cmd=0x{only_cmd:02X}. "
                  f"Building a raw packet.")
            candidates = [(f"0x{only_cmd:02X} raw",
                           [("charge pkt", [only_cmd, 0x00, limit & 0xFF,
                                            0x00, 0x00, 0x00, 0x00, 0x00])],
                           True)]

    print(f"\nTesting {len(candidates)} candidate(s) for charge limit = {limit}%")
    print("After each send, plug in and watch whether charging stops at the target.\n")

    for label, packets, do_commit in candidates:
        print(f"\n{'='*55}")
        print(f"  Candidate: {label}")
        print(f"{'='*55}")
        ans = input("  Send this packet? [y/N/q] ").strip().lower()
        if ans == 'q':
            print("Aborted.")
            break
        if ans != 'y':
            print("  Skipped.")
            continue

        for tag, pkt in packets:
            send(fd, pkt, tag)
            time.sleep(0.05)
        if do_commit:
            commit(fd)
        print("  Sent. Plug in and test charging behaviour.")
        print("  (The EC write is one-way — no readback available.)")


def run_scan(fd, limit):
    """Walk 0x00–0x1F with confirmation between each byte."""
    pct = limit & 0xFF
    print(f"\nScanning command bytes 0x00–0x1F with limit={limit}%")
    print("Prompt before each send. Ctrl-C or 'q' to stop.\n")

    for cmd in range(0x20):
        # Skip known RGB commands to avoid disrupting the display
        if cmd in (0x08, 0x14, 0x16, 0x1A):
            print(f"  [skip] 0x{cmd:02X} — known RGB/commit command")
            continue

        pkt = [cmd, 0x00, pct, 0x00, 0x00, 0x00, 0x00, 0x00]
        print(f"\n  CMD 0x{cmd:02X}: {' '.join(f'{x:02X}' for x in pkt)}")
        try:
            ans = input("  Send + commit? [y/N/q] ").strip().lower()
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        if ans == 'q':
            break
        if ans != 'y':
            continue

        send(fd, pkt, f"cmd=0x{cmd:02X}")
        time.sleep(0.05)
        commit(fd)
        print("  Sent. Test charging then press Enter to continue...")
        try:
            input()
        except KeyboardInterrupt:
            break


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Probe HID charge limit commands on HYDROC-16 G1 EC")
    ap.add_argument("--set",  type=int, metavar="PCT",
                    help="Charge limit percent to send (1–100)")
    ap.add_argument("--scan", type=int, metavar="PCT",
                    help="Walk 0x00–0x1F prompting before each cmd byte")
    ap.add_argument("--cmd",  type=lambda x: int(x, 0), metavar="HEX",
                    help="Restrict --set to this specific command byte (e.g. 0x12)")
    args = ap.parse_args()

    if not args.set and not args.scan:
        ap.print_help()
        sys.exit(0)

    pct = args.set or args.scan
    if not (1 <= pct <= 100):
        print("[!] Percent must be 1–100.")
        sys.exit(1)

    fd = open_ec()

    try:
        if args.scan:
            run_scan(fd, pct)
        else:
            run_candidates(fd, pct, only_cmd=args.cmd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
