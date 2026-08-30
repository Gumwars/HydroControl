#!/usr/bin/env python3
"""
Probe the ITE 8233 chin bar's 64-byte OUTPUT report for static-colour control.

Background
----------
The HID report descriptor for 048d:7001 declares three channels:

    09 20  81 02   64-byte INPUT   (Data,Var,Abs)
    09 21  91 02   64-byte OUTPUT  (Data,Var,Abs)
    09 22  b1 02    8-byte FEATURE (Data,Var,Abs)

kbctrl's existing 61-packet sweep only ever used the 8-byte FEATURE channel
(HIDIOCSFEATURE), where the sole working command is `09 02 XX` -- brightness of
a firmware colour-cycle. The 64-byte OUTPUT channel has never been written to,
and is the natural home for a colour payload.

This script walks candidate framings of that OUTPUT report. Every candidate
encodes RED, so any static red is an unambiguous hit against the colour-cycle
baseline.

Run: sudo python3 lb_output_probe.py
Results are appended to lb_output_probe_log.json.
"""

import array
import fcntl
import glob
import json
import os
import sys
import time

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
LOG = "/home/gumwars/HydroControl/lb_output_probe_log.json"

RED = (0xFF, 0x00, 0x00)


def find_lightbar() -> str:
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            blob = open(os.path.join(p, "device", "uevent")).read().upper()
        except OSError:
            continue
        if "0000048D:00007001" in blob:
            return "/dev/" + os.path.basename(p)
    raise SystemExit("ITE 8233 lightbar (048d:7001) not found")


def pad(data: bytes, n: int = 64) -> bytes:
    return bytes(data[:n]).ljust(n, b"\x00")


def triples(rgb, count=21, header=b"") -> bytes:
    """header + RGB triple repeated to fill the report."""
    return pad(bytes(header) + bytes(rgb) * count)


def planes(rgb, count=21, header=b"") -> bytes:
    """header + [R]*n + [G]*n + [B]*n -- the layout ITE 8291 uses for keys."""
    r, g, b = rgb
    return pad(bytes(header) + bytes([r] * count + [g] * count + [b] * count))


CANDIDATES = [
    ("triples_bare",        triples(RED)),
    ("triples_hdr00",       triples(RED, 20, b"\x00")),
    ("triples_hdr_0902",    triples(RED, 20, b"\x09\x02\x32")),
    ("triples_hdr_14",      triples(RED, 20, b"\x14\x00\x01")),
    ("triples_hdr_08",      triples(RED, 20, b"\x08\x00")),
    ("planes_bare",         planes(RED)),
    ("planes_hdr00",        planes(RED, 20, b"\x00")),
    ("planes_hdr_14",       planes(RED, 20, b"\x14\x00\x01")),
    ("single_triple",       pad(bytes(RED))),
    ("all_ff",              pad(b"\xff" * 64)),
]


def feature(dev: str, pkt8: bytes) -> None:
    report = bytes([0x00]) + pad(pkt8, 8)
    fd = os.open(dev, os.O_RDWR)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
    finally:
        os.close(fd)


def commit(dev: str) -> None:
    feature(dev, LB_COMMIT)


def send_output(dev: str, payload64: bytes) -> str:
    """Write a 64-byte OUTPUT report. hidraw wants report-ID 0 prefixed."""
    buf = bytes([0x00]) + pad(payload64)
    fd = os.open(dev, os.O_RDWR)
    try:
        n = os.write(fd, buf)
        return f"ok ({n} bytes)"
    except OSError as e:
        return f"FAILED: {e}"
    finally:
        os.close(fd)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_output_probe.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    print("Baseline: enabling the bar via the known FEATURE command 09 02 32")
    print("You should see the usual colour cycle now.\n")
    feature(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    commit(dev)
    time.sleep(1)

    print("Each candidate encodes RED. Watch for the cycle stopping on a")
    print("static colour -- that is a hit. Press Enter with no text for")
    print("'no change'. Type 'q' to stop early.\n")

    results = []
    for name, payload in CANDIDATES:
        status = send_output(dev, payload)
        commit(dev)
        time.sleep(1.5)

        print(f"\n[{name}]  write: {status}")
        print(f"  payload: {payload[:16].hex(' ')} ...")
        try:
            obs = input("  observation: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            break

        results.append({
            "name": name,
            "payload": payload.hex(" "),
            "write_status": status,
            "result": obs or "no change",
        })

        if obs.lower() == "q":
            break

    # Restore the known-good state.
    feature(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    commit(dev)

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
        print(f"  {r['name']:20s} {r['write_status']:16s} {r['result']}")
    print("=" * 60)
    print(f"\nLogged to {LOG}")

    if all("FAILED" in r["write_status"] for r in results):
        print("\nEvery OUTPUT write failed. The kernel may not expose an output\n"
              "endpoint for this interface -- next step would be libusb with a\n"
              "direct interrupt-OUT transfer, bypassing hidraw.")


if __name__ == "__main__":
    main()
