#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
compat_probe.py — is this laptop close enough to a HYDROC-16 to be worth trying?

`install.sh` refuses to run on anything but an Eluktronics HYDROC-16 G1, and it
should: EC register layouts differ between chassis, and the same address that
sets a power limit here could mean something else entirely on another board.
That guard is not decoration.

But "refuses to install" is not the same as "cannot work". Other Uniwill/Tongfang
machines share the EC and the ACPI accessors, and the driver's own DMI table
already covers TUXEDO, Schenker, Machenike and AiStone boards. This gathers the
evidence needed to decide, and gathers it *safely*.

WHAT THIS DOES NOT DO
---------------------
It never writes. Not one byte, to any register, ever. It also skips the fan
tachometer registers, which are the one read known to stall this EC's fan
control loop (DESIGN.md §4.2). Reads are paced at the interval the driver uses.

USAGE
-----
On a KNOWN-GOOD HYDROC-16, make a reference:

    sudo python3 compat_probe.py --save hydroc16.json

On the candidate machine, compare against it:

    sudo python3 compat_probe.py --compare hydroc16.json

Needs only `acpi_call` — not the uniwill-laptop module, and not an install:

    sudo modprobe acpi_call
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable      # noqa: E402

# Identity and capability registers only. No tachos (0x0464/5, 0x046C/D) and
# nothing in a range we have not already characterised.
IDENTITY = {
    0x0740: "PROJECT_ID",
    0x078E: "FAN_CTRL caps (b3 charge profiles, b6 HAS_UW_FAN_CTRL)",
    0x0742: "SUPPORT_5",
    0x0765: "SUPPORT_1",
    0x0766: "SUPPORT_2",
}

LAYOUT = {
    0x0727: "custom-profile latch (b6) / double-PL4 (b7)",
    0x0751: "MANUAL_FAN_CTRL",
    0x075B: "PWM_1",
    0x075C: "PWM_2",
    0x0768: "SWITCH_STATUS",
    0x0783: "PL1_SETTING",
    0x0784: "PL2_SETTING",
    0x0785: "PL4_SETTING",
    0x07A5: "OEM_3",
    0x07A6: "OEM_4 (charging profile b5:4)",
    0x046A: "PL1_LIVE",
    0x046B: "PL2_LIVE",
    0x046E: "PL3_LIVE",
    0x046F: "PL4_LIVE",
}

ROMID_RANGE = range(0x0770, 0x077E)


def dmi() -> dict:
    out = {}
    for key, f in (("vendor", "sys_vendor"), ("board", "board_name"),
                   ("product", "product_name"), ("sku", "product_sku"),
                   ("bios", "bios_version")):
        try:
            with open(f"/sys/class/dmi/id/{f}") as fh:
                out[key] = fh.read().strip()
        except OSError:
            out[key] = None
    return out


def usb_ids() -> list[str]:
    try:
        r = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=10)
        return sorted({l.split()[5] for l in r.stdout.splitlines()
                       if len(l.split()) > 5 and l.split()[5].startswith("048d:")})
    except Exception:
        return []


def collect() -> dict:
    info: dict = {"dmi": dmi(), "kernel": os.uname().release,
                  "usb_048d": usb_ids()}

    ok, why = EC.available()
    if not ok:
        info["ec"] = {"reachable": False, "error": why}
        return info

    ec = EC()
    try:
        probe = ec.read(0x0740)
    except ECUnavailable as e:
        # No \_SB.INOU.ECRR in this DSDT, most likely. That is the single most
        # important compatibility fact there is.
        info["ec"] = {"reachable": False,
                      "error": f"ACPI accessor did not answer: {e}"}
        return info

    regs: dict = {}
    for addr in list(IDENTITY) + list(LAYOUT):
        try:
            regs[f"0x{addr:04X}"] = ec.read(addr)
        except ECUnavailable:
            regs[f"0x{addr:04X}"] = None

    rom = ""
    try:
        rom = "".join(chr(ec.read(a)) for a in ROMID_RANGE)
        rom = "".join(c if 32 <= ord(c) < 127 else "." for c in rom).strip()
    except ECUnavailable:
        rom = "?"

    info["ec"] = {"reachable": True, "project_id": probe, "rom_id": rom,
                  "registers": regs}

    h = None
    for p in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(p, "name")) as fh:
                if fh.read().strip() == "uniwill":
                    h = p
        except OSError:
            pass
    info["uniwill_hwmon"] = h
    info["driver_bound"] = os.path.isdir("/sys/bus/platform/devices/INOU0000:00")
    return info


def describe(info: dict) -> None:
    d = info["dmi"]
    print(f"  vendor : {d['vendor']}")
    print(f"  board  : {d['board']}")
    print(f"  product: {d['product']}   sku: {d['sku']}")
    print(f"  bios   : {d['bios']}")
    print(f"  kernel : {info['kernel']}")
    print(f"  USB 048d devices: {', '.join(info['usb_048d']) or 'none'}")
    print(f"  uniwill hwmon   : {info.get('uniwill_hwmon') or 'not present'}")
    print(f"  INOU0000:00 bound: {info.get('driver_bound')}")

    ec = info["ec"]
    if not ec["reachable"]:
        print(f"\n  EC: NOT REACHABLE -- {ec['error']}")
        return
    print(f"\n  EC reachable. PROJECT_ID = 0x{ec['project_id']:02X}"
          f"   ROM ID = {ec['rom_id']!r}")
    for addr, label in list(IDENTITY.items()) + list(LAYOUT.items()):
        v = ec["registers"].get(f"0x{addr:04X}")
        shown = "unreadable" if v is None else f"0x{v:02X}  {format(v, '08b')}"
        print(f"    0x{addr:04X}  {shown:<22} {label}")


def compare(ref: dict, cur: dict) -> None:
    print("\n=== comparison with the reference ===")
    r, c = ref.get("ec", {}), cur.get("ec", {})
    if not r.get("reachable") or not c.get("reachable"):
        print("  One side has no EC access; nothing to compare.")
        return

    same_project = r["project_id"] == c["project_id"]
    print(f"  PROJECT_ID   reference 0x{r['project_id']:02X}   "
          f"this 0x{c['project_id']:02X}   "
          f"{'MATCH' if same_project else 'DIFFERENT'}")
    print(f"  ROM ID       reference {r['rom_id']!r}   this {c['rom_id']!r}")

    caps_same = True
    print("\n  capability registers:")
    for addr in IDENTITY:
        k = f"0x{addr:04X}"
        a, b = r["registers"].get(k), c["registers"].get(k)
        same = a == b
        caps_same &= same
        print(f"    {k}  ref 0x{a:02X}  this 0x{b:02X}  "
              f"{'same' if same else 'DIFFERS'}" if a is not None and b is not None
              else f"    {k}  unreadable on one side")

    print("\n  layout registers (values will differ; presence is the point):")
    unreadable = []
    for addr in LAYOUT:
        k = f"0x{addr:04X}"
        if c["registers"].get(k) is None:
            unreadable.append(k)
    print(f"    unreadable here: {', '.join(unreadable) if unreadable else 'none'}")

    print("\n=== verdict ===")
    if same_project and caps_same:
        print("  Same PROJECT_ID and identical capability bytes. This is very")
        print("  likely the same EC layout. Worth adding the board to the DMI")
        print("  allowlist and testing carefully, starting with read-only.")
    elif same_project:
        print("  Same PROJECT_ID but the capability bytes differ, so the feature")
        print("  set is not identical. Related, not the same. Do not assume any")
        print("  register means what it means on the HYDROC-16.")
    else:
        print("  Different PROJECT_ID. Treat every register as unknown. The")
        print("  ACPI accessors may still work, but the map does not carry over.")
    print("\n  Nothing here justifies bypassing the installer's DMI guard on its")
    print("  own. It tells you whether investigating further is worthwhile.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="FILE", help="write this machine as a reference")
    ap.add_argument("--compare", metavar="FILE", help="compare against a reference")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("needs root to read the EC: sudo python3 compat_probe.py")
        return 1

    print("=== this machine ===")
    info = collect()
    describe(info)

    if args.save:
        try:
            with open(args.save, "w") as fh:
                json.dump(info, fh, indent=2)
            print(f"\nwrote {args.save}")
        except OSError as e:
            print(f"\ncould not write {args.save}: {e}")

    if args.compare:
        try:
            with open(args.compare) as fh:
                ref = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"\ncould not read {args.compare}: {e}")
            return 1
        compare(ref, info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
