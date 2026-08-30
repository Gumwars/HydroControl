#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Read/write individual EC registers via \\_SB.INOU.ECRR / ECRW (acpi_call).

The DSDT wraps the EC as MMIO at 0xFE410000 and serialises access behind the
ACPI mutex UWOL, so this is the same path the vendor firmware uses -- unlike
poking /dev/mem, which would bypass that lock.

    sudo python3 ec_poke.py read  0x748
    sudo python3 ec_poke.py write 0x748 0x01
    sudo python3 ec_poke.py chinbar-static FF 00 00
    sudo python3 ec_poke.py chinbar-restore
"""
import os, sys

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

AC_CTRL, AC_R, AC_G, AC_B = 0x0748, 0x0749, 0x074A, 0x074B
BAT_CTRL = 0x07E2
APP_EXISTS, POWER_SAVE, S0_OFF, S3_OFF, WELCOME = 0x01, 0x02, 0x04, 0x08, 0x80


def _call(expr):
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        return fh.read().strip().rstrip("\x00")


def read(addr):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        raise SystemExit(f"read 0x{addr:04X} failed: {raw}")
    return int(raw, 16) & 0xFF


def write(addr, val):
    raw = _call(f"{ECRW} 0x{addr:X} 0x{val:X}")
    if raw.startswith("Error"):
        raise SystemExit(f"write 0x{addr:04X} failed: {raw}")


def decode_ctrl(v):
    bits = [("APP_EXISTS", APP_EXISTS), ("POWER_SAVE", POWER_SAVE),
            ("S0_OFF", S0_OFF), ("S3_OFF", S3_OFF), ("WELCOME", WELCOME)]
    on = [n for n, m in bits if v & m]
    return ", ".join(on) if on else "(none)"


def show():
    c = read(AC_CTRL)
    print(f"  0748 AC_CTRL  = {c:02X}  {decode_ctrl(c)}")
    print(f"  0749 RED      = {read(AC_R):02X}")
    print(f"  074A GREEN    = {read(AC_G):02X}")
    print(f"  074B BLUE     = {read(AC_B):02X}")
    print(f"  07E2 BAT_CTRL = {read(BAT_CTRL):02X}  {decode_ctrl(read(BAT_CTRL))}")


def main():
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    cmd = sys.argv[1]

    if cmd == "read":
        a = int(sys.argv[2], 0)
        print(f"0x{a:04X} = 0x{read(a):02X}")

    elif cmd == "write":
        a, v = int(sys.argv[2], 0), int(sys.argv[3], 0)
        write(a, v)
        print(f"0x{a:04X} <- 0x{v:02X}  (readback 0x{read(a):02X})")

    elif cmd == "chinbar-static":
        r, g, b = (int(x, 16) for x in sys.argv[2:5])
        print("before:"); show()
        # Claim the bar for userspace and stop the firmware welcome animation.
        ctrl = read(AC_CTRL)
        new = (ctrl | APP_EXISTS) & ~(WELCOME | S0_OFF) & 0xFF
        write(AC_CTRL, new)
        write(AC_R, r); write(AC_G, g); write(AC_B, b)
        # Mirror to the battery-mode registers so it survives unplugging.
        write(BAT_CTRL, new)
        print("after:"); show()

    elif cmd == "chinbar-restore":
        ctrl = (read(AC_CTRL) | WELCOME) & ~APP_EXISTS & 0xFF
        write(AC_CTRL, ctrl)
        print("restored:"); show()

    elif cmd == "show":
        show()

    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
