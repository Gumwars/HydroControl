#!/usr/bin/env python3
"""
Determine how the HYDROC-16 chin bar is actually driven.

Hypothesis under test: the ITE 8233 (048d:7001) is the master power/brightness
gate, and the EC lightbar registers (via uniwill-laptop's LED class) supply the
colour. If so, neither layer alone can produce a static colour -- which matches
every symptom seen so far.

Run: sudo python3 chinbar_test.py
"""

import array
import fcntl
import glob
import os
import sys

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])

LED = "/sys/class/leds/uniwill:multicolor:status"
PLAT = "/sys/bus/platform/devices/INOU0000:00"


def find_lightbar() -> str:
    """Locate the ITE 8233 hidraw node by VID/PID rather than trusting hidraw1."""
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = os.path.join(path, "device", "uevent")
        try:
            with open(uevent) as fh:
                blob = fh.read()
        except OSError:
            continue
        # HID_ID=0003:0000048D:00007001
        if "0000048D:00007001" in blob.upper():
            return "/dev/" + os.path.basename(path)
    raise SystemExit("ITE 8233 lightbar (048d:7001) not found")


def lb_send(dev: str, pkt8: bytes) -> None:
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")
    fd = os.open(dev, os.O_RDWR)
    try:
        fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
        fcntl.ioctl(fd, HIDIOCSFEATURE_9,
                    array.array("B", bytes([0x00]) + LB_COMMIT), True)
    finally:
        os.close(fd)


def write(path: str, value: str) -> None:
    try:
        with open(path, "w") as fh:
            fh.write(value)
    except OSError as e:
        print(f"    ! write {path} = {value!r} failed: {e}")


def read(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return "?"


def ec_colour(r: int, g: int, b: int) -> None:
    """Set EC lightbar colour. Toggles brightness to defeat the regmap cache."""
    write(f"{LED}/brightness", "0")
    write(f"{LED}/multi_intensity", f"{r} {g} {b}")
    write(f"{LED}/brightness", "200")


def ask(prompt: str) -> str:
    try:
        return input(f"\n  >>> {prompt}\n  >>> observation: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\naborted")


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 chinbar_test.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    print(f"EC LED class:      {LED}")
    print(f"rainbow_animation: {read(PLAT + '/rainbow_animation')}")

    results = {}

    print("\n[1] ITE 8233 alone -- brightness up, EC rainbow OFF")
    write(f"{PLAT}/rainbow_animation", "0")
    lb_send(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    results["1_ite_only"] = ask("Is the bar lit? colour-cycling, static, or dark?")

    print("\n[2] ITE 8233 on + EC colour = RED")
    ec_colour(255, 0, 0)
    results["2_ite_on_ec_red"] = ask("Did it become static RED, keep cycling, or go dark?")

    print("\n[3] ITE 8233 on + EC colour = GREEN")
    ec_colour(0, 255, 0)
    results["3_ite_on_ec_green"] = ask("Static GREEN? (confirms EC drives colour)")

    print("\n[4] ITE 8233 OFF + EC colour still GREEN")
    lb_send(dev, bytes([0x09, 0x02, 0, 0, 0, 0, 0, 0]))
    results["4_ite_off_ec_green"] = ask("Dark? (confirms ITE 8233 is the master gate)")

    print("\n[5] Restore: ITE 8233 on at 50, EC rainbow ON")
    lb_send(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    write(f"{PLAT}/rainbow_animation", "1")
    results["5_restored"] = ask("Back to the original rainbow?")

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for step, obs in results.items():
        print(f"  {step:22s} {obs}")

    print("""
Interpretation:
  [2]/[3] static colour  -> layered: ITE 8233 gates power, EC sets colour.
                            Full RGB chin bar is available. Use both.
  [2]/[3] keeps cycling  -> ITE 8233 owns the effect; EC regs are vestigial.
                            Drop LIGHTBAR from the descriptor; RE the 8233
                            protocol for static colour.
  [1] dark throughout    -> something else gates the bar entirely.
""")


if __name__ == "__main__":
    main()
