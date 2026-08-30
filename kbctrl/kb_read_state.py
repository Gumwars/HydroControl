#!/usr/bin/env python3
"""Read feature report from hidraw0 to probe device state."""
import os, fcntl, array, sys

HIDIOCGFEATURE = 0xC0094807
DEVICE = "/dev/hidraw0"

fd = os.open(DEVICE, os.O_RDWR)

# Try reading feature report with report ID 0x00
buf = array.array("B", [0x00] + [0x00] * 64)
try:
    ret = fcntl.ioctl(fd, HIDIOCGFEATURE, buf, True)
    print(f"GET_FEATURE returned {ret} bytes:")
    print(" ".join(f"{b:02X}" for b in buf[:max(ret,16)]))
except OSError as e:
    print(f"GET_FEATURE failed: {e}")

# Also try reading raw data (interrupt IN)
import select
print("\nListening for interrupt IN packets for 3 seconds...")
r, _, _ = select.select([fd], [], [], 3)
if r:
    data = os.read(fd, 64)
    print("Got:", " ".join(f"{b:02X}" for b in data))
else:
    print("No unsolicited data received.")

os.close(fd)
