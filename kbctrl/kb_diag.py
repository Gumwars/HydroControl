#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_diag.py — Enumerate hidraw devices and probe each one for keyboard response.

Prints the uevent data for every hidraw node so we can identify which node
is the keyboard vs. light bar, then optionally sends test packets to each.
"""

import os, fcntl, array, sys, time, struct

HIDIOCSFEATURE = 0xC0094806
HIDIOCGRAWINFO = 0x80084803   # get bustype/vendor/product

def get_raw_info(fd):
    """Return (bustype, vendor, product) from ioctl, or None on failure."""
    try:
        buf = array.array('I', [0, 0, 0])
        fcntl.ioctl(fd, HIDIOCGRAWINFO, buf, True)
        bustype, vendor, product = buf[0], buf[1] & 0xFFFF, buf[2] & 0xFFFF
        return bustype, vendor, product
    except Exception:
        return None

def pad(pkt):
    return [0x00] + pkt + [0x00] * (65 - 1 - len(pkt))

def send_quiet(fd, pkt):
    try:
        buf = array.array("B", pad(pkt))
        fcntl.ioctl(fd, HIDIOCSFEATURE, buf, True)
        return True
    except Exception as e:
        return False

def probe_device(devpath, i):
    print(f"\n{'='*60}")
    print(f"  /dev/hidraw{i}")
    print(f"{'='*60}")

    uevent_path = f"/sys/class/hidraw/hidraw{i}/device/uevent"
    try:
        with open(uevent_path, "r") as f:
            uevent = f.read().strip()
        print(f"  uevent:\n    " + "\n    ".join(uevent.splitlines()))
    except FileNotFoundError:
        print("  [no uevent]")
        uevent = ""

    try:
        fd = os.open(devpath, os.O_RDWR)
    except OSError as e:
        print(f"  [cannot open: {e}]")
        return

    info = get_raw_info(fd)
    if info:
        bus, vid, pid = info
        print(f"  HIDIOCGRAWINFO → bus={bus:#x}  VID={vid:#06x}  PID={pid:#06x}")

    # Read device name
    try:
        name_path = f"/sys/class/hidraw/hidraw{i}/device/name"
        with open(name_path, "r") as f:
            print(f"  name: {f.read().strip()}")
    except FileNotFoundError:
        pass

    # Try to read report descriptor size
    try:
        rdesc_path = f"/sys/class/hidraw/hidraw{i}/device/report_descriptor"
        size = os.path.getsize(rdesc_path)
        print(f"  report descriptor size: {size} bytes")
    except Exception:
        pass

    # Probe: can we even send a feature report?
    test_pkt = [0x14, 0x01, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00]  # red mono color
    ok = send_quiet(fd, test_pkt)
    print(f"  feature report writeable: {'YES' if ok else 'NO (permission or unsupported)'}")

    os.close(fd)

def main():
    print("kb_diag.py — hidraw enumeration")
    print()

    found = 0
    for i in range(20):
        devpath = f"/dev/hidraw{i}"
        if os.path.exists(devpath):
            probe_device(devpath, i)
            found += 1

    if found == 0:
        print("No /dev/hidraw* devices found at all.")
        sys.exit(1)

    print(f"\n\nFound {found} hidraw device(s) total.")
    print()
    print("Next steps:")
    print("  1. Look for the uevent lines with HID_ID containing 048D:6006 or 093A:0255")
    print("     — those are your Uniwill/ITE controller nodes.")
    print("  2. Note which node number(s) those are.")
    print("  3. If there are multiple matching nodes, the keyboard and light bar")
    print("     may be on separate hidraw interfaces.")

if __name__ == "__main__":
    main()
