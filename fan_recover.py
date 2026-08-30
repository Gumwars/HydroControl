#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_recover.py — put the fan control registers back to firmware-managed state.

Run this if the fans read 0 rpm, or read wrong, after a fan probe. It reports
what every relevant register actually holds, clears the manual-control bits,
and re-reads so you can see whether that fixed it.

    sudo python3 fan_recover.py            # report and clear manual bits
    sudo python3 fan_recover.py --report   # report only, change nothing

If this does not restore the reading, a FULL POWER CYCLE will: everything the
fan probes touched lives in volatile EC RAM. Shut down completely -- not
reboot -- and start again.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected     # noqa: E402
from hydroc.hardware import find_hwmon                       # noqa: E402

REG_MANUAL_FAN_CTRL = 0x0751
REG_PWM_1, REG_PWM_2 = 0x075B, 0x075C
REG_SWITCH_STATUS = 0x0768

# tacho registers, high/low byte pairs
REG_FAN1_RPM = (0x0464, 0x0465)
REG_FAN2_RPM = (0x046C, 0x046D)

FAN_LEVEL_MASK = 0b111
FAN_MODE_TURBO = 1 << 4
FAN_MODE_HIGH = 1 << 5
FAN_MODE_BOOST = 1 << 6
FAN_MODE_USER = 1 << 7
MANUAL_BITS = FAN_MODE_USER | FAN_MODE_TURBO | FAN_MODE_HIGH | FAN_LEVEL_MASK


def bits(v: int) -> str:
    return format(v, "08b")


def hwmon_read() -> dict:
    h = find_hwmon()
    out: dict = {}
    if not h:
        return out
    for f in ("pwm1", "pwm2", "fan1_input", "fan2_input",
              "temp1_input", "temp2_input"):
        try:
            with open(os.path.join(h, f)) as fh:
                out[f] = int(fh.read().strip())
        except (OSError, ValueError):
            out[f] = None
    return out


def report(ec: EC) -> None:
    ctrl = ec.read(REG_MANUAL_FAN_CTRL)
    print(f"  0x0751 MANUAL_FAN_CTRL = 0x{ctrl:02X}  {bits(ctrl)}")
    print(f"      FAN_LEVEL (2:0) = {ctrl & FAN_LEVEL_MASK}")
    print(f"      TURBO b4 = {bool(ctrl & FAN_MODE_TURBO)}   "
          f"HIGH b5 = {bool(ctrl & FAN_MODE_HIGH)}")
    print(f"      BOOST b6 = {bool(ctrl & FAN_MODE_BOOST)}   "
          f"USER  b7 = {bool(ctrl & FAN_MODE_USER)}")
    print(f"  0x075B PWM_1 = {ec.read(REG_PWM_1)} of 200")
    print(f"  0x075C PWM_2 = {ec.read(REG_PWM_2)} of 200")
    sw = ec.read(REG_SWITCH_STATUS)
    print(f"  0x0768 SWITCH_STATUS = 0x{sw:02X}  FAN_BOOST_STATUS b2 = {bool(sw & 0b100)}")

    # NOT read by default. Reading 0x0464/0x0465/0x046C/0x046D through ECRR is
    # the one thing this script does that nothing else in the project does, and
    # it correlated with the fans stopping the moment it ran. hydroc.server does
    # MORE EC reads per poll cycle, every 10s all day, with no fan trouble --
    # so read volume was never a good explanation. Reading the EC's own fan
    # measurement registers out from under it is a much better one.
    #
    # hwmon reports the same numbers through the driver's path, which is safe.
    if "--ec-tacho" in sys.argv:
        print("  --ec-tacho: reading the tacho registers directly. This is the")
        print("  operation that has previously stopped the fans -- watch them.")
        for name, (hi, lo) in (("fan1", REG_FAN1_RPM), ("fan2", REG_FAN2_RPM)):
            h, l = ec.read(hi), ec.read(lo)
            print(f"  {name} tacho: 0x{hi:04X}=0x{h:02X} 0x{lo:04X}=0x{l:02X}"
                  f"  -> {(h << 8) | l} rpm")

    hm = hwmon_read()
    c = (hm.get("temp1_input") or 0) / 1000.0
    g = (hm.get("temp2_input") or 0) / 1000.0
    print(f"  hwmon: pwm {hm.get('pwm1')}/{hm.get('pwm2')}  "
          f"rpm {hm.get('fan1_input')}/{hm.get('fan2_input')}  {c:.0f}C/{g:.0f}C")


def main() -> int:
    if os.geteuid() != 0:
        print("needs root: sudo python3 fan_recover.py")
        return 1

    ec = EC()
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    print("=== before ===")
    report(ec)
    if "--ec-tacho" not in sys.argv:
        print("  (tacho registers not read -- pass --ec-tacho only if you need to")
        print("   tell 'fans stopped' from 'driver readback stuck', and only with")
        print("   cooling headroom to spare)")

    if "--report" in sys.argv:
        return 0

    ctrl = ec.read(REG_MANUAL_FAN_CTRL)
    target = ctrl & ~MANUAL_BITS
    print(f"\nclearing manual bits: 0x{ctrl:02X} -> 0x{target:02X}")
    try:
        ec.write_verify(REG_MANUAL_FAN_CTRL, target)
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"  write failed: {e}")

    time.sleep(4)
    print("\n=== after ===")
    report(ec)

    hm = hwmon_read()
    if (hm.get("fan1_input") or 0) > 0:
        print("\n  Fans are reporting again.")
    else:
        print("\n  Still 0 rpm from hwmon. POWER CYCLE: shut down fully (not")
        print("  reboot) and start again; the EC reloads factory defaults.")
        print("  Clearing FAN_MODE_USER does NOT hand control back to the EC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
