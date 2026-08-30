#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Dump the HYDROC-16 EC register space via the firmware's own ACPI accessors.

How this works
--------------
The DSDT exposes the EC as memory-mapped I/O:

    Method (ECRR, 1) { Local0 = (0xFE410000 + Arg0); Return (MMRW(Local0, 0, 0, 0)) }
    Method (ECRW, 2) { Local0 = (0xFE410000 + Arg0); MMRW(Local0, 1, 0, Arg1) }

MMRW serialises every access behind the ACPI mutex UWOL, so going through
ECRR/ECRW is materially safer than poking 0xFE410000 through /dev/mem, which
would bypass that lock and can race the EC's own firmware.

The acpi_call module lets userspace evaluate those methods directly, which
sidesteps the uniwill-laptop regmap whitelist entirely -- including the
registers the driver deliberately never exposes (PL1 0x0783, PL2 0x0784,
the performance-profile bits at 0x07A5).

Setup:
    paru -S acpi_call-dkms      # or yay -S acpi_call-dkms
    sudo modprobe acpi_call

Usage:
    sudo python3 ec_dump.py                        # dump 0x0700-0x07FF
    sudo python3 ec_dump.py --start 0 --end 0xFFF  # full space
    sudo python3 ec_dump.py -o ec-balanced.txt     # save for diffing

Diff two dumps to see what a Control Center mode actually changed:
    diff -u ec-quiet.txt ec-performance.txt
"""

import argparse
import os
import sys

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"

# Registers the uniwill-laptop driver already has names for, so a dump is
# readable without cross-referencing the source.
KNOWN = {
    # --- ECXP window: SystemMemory at ECMA(0xFE410000) + 0x400, so these are
    # --- reachable through the same ECRR accessor. Offsets parsed from the
    # --- DSDT Field(ECXP) block. This is Control Center's side of the EC,
    # --- distinct from the 0x07xx registers the uniwill driver knows about.
    0x0466: "TURB/PBMN/RTCR/S0IF  <-- bit0 = turbo enable",
    0x046A: "PL1L               <-- CPU power limit 1 (ECXP)",
    0x046B: "PL2L               <-- CPU power limit 2 (ECXP)",
    0x046E: "PL3L               <-- CPU power limit 3 (ECXP)",
    0x046F: "PL4L               <-- CPU power limit 4 (ECXP)",
    0x048A: "LDAT               <-- mailbox data low",
    0x048B: "HDAT               <-- mailbox data high",
    0x048C: "flags  RFLG/WFLG/BFLG/CFLG/DRDY(b7)",
    0x048D: "CMDL               <-- mailbox command low",
    0x048E: "CMDH               <-- mailbox command high",
    0x0743: "CTGP_DB_CTRL",
    0x0744: "CTGP_DB_CTGP_OFFSET",
    0x0745: "CTGP_DB_TPP_OFFSET",
    0x0746: "CTGP_DB_DB_OFFSET",
    0x0748: "LIGHTBAR_AC_CTRL",
    0x0749: "LIGHTBAR_AC_RED",
    0x074A: "LIGHTBAR_AC_GREEN",
    0x074B: "LIGHTBAR_AC_BLUE",
    0x0783: "PL1_SETTING        <-- CPU power limit 1",
    0x0784: "PL2_SETTING        <-- CPU power limit 2",
    0x078E: "FAN_CTRL",
    0x07A3: "BIOS_OEM_3",
    0x07A4: "BIOS_OEM_BYTE",
    0x07A5: "OEM_3              <-- FAN_QUIET/OVERBOOST/HIGH_POWER",
    0x07A6: "OEM_4              <-- charging profile, touchpad toggle",
    0x07B9: "CHARGE_CTRL",
    0x07E2: "LIGHTBAR_BAT_CTRL",
}


def ec_read(addr: int) -> int | None:
    """Evaluate \\_SB.INOU.ECRR(addr) and return the byte, or None on error."""
    try:
        with open(CALL, "w") as fh:
            fh.write(f"{ECRR} 0x{addr:X}")
        with open(CALL) as fh:
            raw = fh.read().strip().rstrip("\x00")
    except OSError as e:
        raise SystemExit(f"cannot use {CALL}: {e}\n"
                         "Is acpi_call loaded?  sudo modprobe acpi_call")

    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump EC registers via ACPI ECRR")
    ap.add_argument("--start", type=lambda s: int(s, 0), default=0x0700)
    ap.add_argument("--end", type=lambda s: int(s, 0), default=0x07FF)
    ap.add_argument("-o", "--output", help="write dump here instead of stdout")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 ec_dump.py)")
    if not os.path.exists(CALL):
        raise SystemExit(f"{CALL} missing -- run: sudo modprobe acpi_call")

    lines = []
    errors = 0

    for base in range(args.start, args.end + 1, 16):
        vals = []
        for off in range(16):
            addr = base + off
            if addr > args.end:
                break
            v = ec_read(addr)
            if v is None:
                errors += 1
                vals.append(None)
            else:
                vals.append(v)

        hexpart = " ".join("--" if v is None else f"{v:02X}" for v in vals)
        ascii_part = "".join(
            "." if v is None or v < 0x20 or v > 0x7E else chr(v) for v in vals
        )
        lines.append(f"{base:04X}  {hexpart:<47}  |{ascii_part}|")

    # Append an annotated section for the registers we care about.
    lines.append("")
    lines.append("--- named registers ---")
    for addr in sorted(KNOWN):
        if args.start <= addr <= args.end:
            v = ec_read(addr)
            shown = "--" if v is None else f"{v:02X}"
            bits = "" if v is None else f"  0b{v:08b}"
            lines.append(f"{addr:04X}  {shown}{bits}  {KNOWN[addr]}")

    out = "\n".join(lines)

    if args.output:
        with open(args.output, "w") as fh:
            fh.write(out + "\n")
        print(f"wrote {args.output}  ({errors} unreadable bytes)")
    else:
        print(out)
        if errors:
            print(f"\n({errors} unreadable bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
