#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_rdesc.py — Read and parse the HID report descriptor from hidraw0.

The descriptor defines which report IDs and report types (input/output/feature)
the device actually supports. If we've been using the wrong report ID, every
feature report we sent was silently discarded by the firmware.
"""

import os, fcntl, array, struct, sys

DEVICE = "/dev/hidraw0"

# ioctls
HIDIOCGRDESCSIZE = 0x80044801
HIDIOCGRDESC     = 0x90044802

def get_report_descriptor(dev):
    fd = os.open(dev, os.O_RDWR)
    try:
        # Get descriptor size
        size_buf = array.array('I', [0])
        fcntl.ioctl(fd, HIDIOCGRDESCSIZE, size_buf, True)
        size = size_buf[0]
        print(f"Report descriptor size: {size} bytes")

        # Read descriptor
        # struct hidraw_report_descriptor { uint32_t size; uint8_t value[4096]; }
        rdesc_buf = array.array('B', [0] * (4 + 4096))
        struct.pack_into('I', rdesc_buf, 0, size)
        fcntl.ioctl(fd, HIDIOCGRDESC, rdesc_buf, True)

        desc = bytes(rdesc_buf[4:4+size])
        return desc
    finally:
        os.close(fd)

def parse_descriptor(desc):
    """Basic HID descriptor parser — extracts report IDs and types."""
    print(f"\nRaw descriptor ({len(desc)} bytes):")
    hex_rows = [desc[i:i+16].hex(' ') for i in range(0, len(desc), 16)]
    for row in hex_rows:
        print(f"  {row}")

    print("\n--- Parsed items ---")
    i = 0
    current_report_id = 0
    reports = {}  # report_id -> set of types

    while i < len(desc):
        b = desc[i]
        btype  = (b >> 2) & 0x3   # 0=Main, 1=Global, 2=Local, 3=Reserved
        btag   = (b >> 4) & 0xF
        bsize  = b & 0x3          # 0=0bytes, 1=1byte, 2=2bytes, 3=4bytes
        size   = [0, 1, 2, 4][bsize]

        val = 0
        for j in range(size):
            if i + 1 + j < len(desc):
                val |= desc[i + 1 + j] << (8 * j)

        tag_names = {
            (1, 0): "Usage Page",
            (2, 0): "Usage",
            (1, 1): "Logical Min",
            (1, 2): "Logical Max",
            (1, 3): "Physical Min",
            (1, 4): "Physical Max",
            (1, 5): "Unit Exp",
            (1, 6): "Unit",
            (1, 7): "Report Size",
            (1, 8): "Report ID",
            (1, 9): "Report Count",
            (0, 8): "Input",
            (0, 9): "Output",
            (0, 0xB): "Feature",
            (0, 0xA): "Collection",
            (0, 0xC): "End Collection",
        }
        name = tag_names.get((btype, btag), f"type={btype} tag={btag:#x}")

        if btype == 1 and btag == 8:  # Report ID
            current_report_id = val
            print(f"  [{i:3d}] Report ID: 0x{val:02X} ({val})")
        elif btype == 0 and btag in (8, 9, 0xB):  # Input/Output/Feature
            rtype = {8: "Input", 9: "Output", 0xB: "Feature"}[btag]
            if current_report_id not in reports:
                reports[current_report_id] = set()
            reports[current_report_id].add(rtype)
            print(f"  [{i:3d}] {rtype} report  (ID={current_report_id:#04x}, flags={val:#x})")

        i += 1 + size

    print("\n--- Report ID summary ---")
    for rid, types in sorted(reports.items()):
        print(f"  ID 0x{rid:02X} ({rid:3d}): {', '.join(sorted(types))}")

    return reports

def main():
    print(f"Reading HID report descriptor from {DEVICE}")
    try:
        desc = get_report_descriptor(DEVICE)
        reports = parse_descriptor(desc)

        feature_ids = [rid for rid, t in reports.items() if 'Feature' in t]
        print(f"\n*** Feature report IDs found: {[f'0x{r:02X}' for r in feature_ids]} ***")
        if 0x00 not in feature_ids and feature_ids:
            print(f"!!! We have been sending to report ID 0x00 but device expects: {[f'0x{r:02X}' for r in feature_ids]}")
            print("    This would explain why all our packets were silently ignored!")
    except OSError as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
