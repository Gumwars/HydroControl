#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Read EC RAM through the WMI door instead of the MMIO one. READ-ONLY.

Every EC access this project has ever made goes through `\\_SB.INOU.ECRR` /
`ECRW`, which the DSDT implements as MMIO at 0xFE410000. tuxedo-drivers does
not. It calls a WMI method:

    wmi_evaluate_method(ABBC0F6F-8EA1-11D1-00A0-C90629100000, 0, 0x04, ...)

That GUID maps to object id "BC" on the WMI device at `\\_SB.AMW0`, so the
underlying ACPI method is `\\_SB.AMW0.WMBC(instance, method_id, buffer)` -- and
all of those GUIDs are present on this machine.

WHY THIS MATTERS

Both doors set the byte; we have verified that by readback thousands of times.
"The byte is set" and "the EC acted on it" are different claims, and this
project already proved the distinction exists -- charge_path_probe.py was
written precisely to establish that WKBC and ECRW both write 0x07B9. We were
careful about that for the threshold register and never asked the same question
about the charging profile at 0x07A6.

If the EC only re-evaluates charging policy when told through WMI, then every
negative result we have recorded about the charging profiles is untested rather
than negative, because every one of them poked MMIO. A user on the same model,
driving it through tuxedo-drivers, sees the ceiling engage.

WHAT THIS FILE DOES, AND DOES NOT

It reads. The function byte is hard-coded to READ and there is no write path in
this file at all -- not disabled, absent. Establishing that the WMI read agrees
with ECRR across registers whose values we already know is the prerequisite for
trusting anything written through the same door later.

    sudo python3 wmi_ec_probe.py              # compare WMI vs ECRR
    sudo python3 wmi_ec_probe.py --dump 0x0780 0x079F
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
WMBC = r"\_SB.AMW0.WMBC"

WMI_INSTANCE = 0x00
WMI_METHOD_ID = 0x04
FUNCTION_READ = 1                      # 0 is WRITE. Never used here.

# tuxedo builds a 10 x u32 buffer, memsets 40 bytes, then passes
# `sizeof(wmi_arg)` where wmi_arg is a `u32 *` -- so the buffer that actually
# reaches the firmware is 8 bytes, not 40. Whether that is a bug or not, 8 is
# the length the working implementation sends, so it is the length we send.
ARG_LEN = 8

# Registers whose values we already know independently, so agreement or
# disagreement means something. Nothing here is written.
KNOWN = [
    (0x078E, "capability byte -- has read 0xFC across kernels and reboots"),
    (0x07A6, "charging profile, bits 5:4"),
    (0x07B9, "charge threshold + REACHED"),
    (0x07C6, "AP_OEM_6 -- fan enable, ERM, full-24h"),
    (0x0783, "PL1, watts"),
    (0x0784, "PL2, watts"),
    (0x07C5, "universal fan ctrl / SPLIT_TABLES"),
]

EC_DELAY = 0.006
_last = 0.0


def _call(expr: str) -> str:
    """One ACPI evaluation, 6 ms paced like every other EC access here."""
    global _last
    gap = time.monotonic() - _last
    if gap < EC_DELAY:
        time.sleep(EC_DELAY - gap)
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        raw = fh.read().strip().rstrip("\x00")
    _last = time.monotonic()
    return raw


def read_arg(addr: int) -> bytes:
    """The 8 input bytes for a WMI EC read. The only buffer this file builds.

    byte 0  address low
    byte 1  address high
    byte 5  function -- READ, and nothing else is reachable from here
    """
    buf = bytearray(ARG_LEN)
    buf[0] = addr & 0xFF
    buf[1] = (addr >> 8) & 0xFF
    buf[5] = FUNCTION_READ
    return bytes(buf)


def _parse(raw: str):
    """acpi_call renders a buffer as {0x01, 0x02, ...} and an integer as 0x..."""
    raw = raw.strip()
    if raw.startswith("Error"):
        return None, raw
    if raw.startswith("{"):
        try:
            vals = [int(x.strip(), 16) for x in raw.strip("{}").split(",") if x.strip()]
        except ValueError:
            return None, f"unparsable buffer {raw[:40]}"
        return vals, None
    try:
        v = int(raw, 16)
    except ValueError:
        return None, f"unparsable {raw[:40]}"
    return [v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF], None


def wmi_read(addr: int):
    """One byte via WMI. Returns (value, error)."""
    arg = read_arg(addr).hex()
    raw = _call(f"{WMBC} {WMI_INSTANCE:#x} {WMI_METHOD_ID:#x} b{arg}")
    vals, err = _parse(raw)
    if err:
        return None, err
    if not vals:
        return None, "empty response"
    # tuxedo treats 0xfefefefe as the firmware's read-failure marker.
    if len(vals) >= 4 and vals[:4] == [0xFE, 0xFE, 0xFE, 0xFE]:
        return None, "firmware returned 0xFEFEFEFE (read error)"
    return vals[0], None


def ecrr_read(addr: int):
    """The same byte through the door we have always used."""
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        return None, raw
    try:
        return int(raw, 16) & 0xFF, None
    except ValueError:
        return None, f"unparsable {raw[:40]}"


def compare(addrs, repeats: int) -> list[dict]:
    """Read each address both ways, several times.

    Repeats matter. 0x07C6 reads one garbage byte in a few hundred through
    ECRR, and a single disagreement between two doors proves nothing if either
    of them glitches. A door is only trustworthy if it is boringly repeatable.
    """
    out = []
    for addr, note in addrs:
        w, e = [], []
        werr = eerr = None
        for _ in range(repeats):
            v, err = wmi_read(addr)
            w.append(v)
            werr = werr or err
            v, err = ecrr_read(addr)
            e.append(v)
            eerr = eerr or err
        out.append({
            "addr": f"0x{addr:04X}", "note": note,
            "wmi": w, "ecrr": e,
            "wmi_stable": len(set(w)) == 1, "ecrr_stable": len(set(e)) == 1,
            "agree": len(set(w)) == 1 and len(set(e)) == 1 and w[0] == e[0],
            "wmi_error": werr, "ecrr_error": eerr,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Read EC RAM via WMI. Read-only.")
    ap.add_argument("--repeats", type=int, default=5,
                    help="reads per address per door; a single sample proves "
                         "nothing when either door can glitch")
    ap.add_argument("--dump", nargs=2, metavar=("START", "END"),
                    type=lambda s: int(s, 0),
                    help="compare a whole range instead of the known set")
    ap.add_argument("--json", help="write the comparison here")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")

    addrs = ([(a, "") for a in range(args.dump[0], args.dump[1] + 1)]
             if args.dump else KNOWN)

    print(f"WMI  {WMBC} instance {WMI_INSTANCE:#x} method {WMI_METHOD_ID:#x} "
          f"function READ\nECRR {ECRR}\n")
    rows = compare(addrs, args.repeats)

    print(f"{'addr':>8} {'WMI':>6} {'ECRR':>6}  {'':<9} note")
    agree = disagree = failed = 0
    for r in rows:
        w = r["wmi"][0]
        e = r["ecrr"][0]
        if r["wmi_error"] or w is None:
            verdict, failed = "WMI FAILED", failed + 1
        elif not r["wmi_stable"] or not r["ecrr_stable"]:
            verdict = "unstable"
        elif r["agree"]:
            verdict, agree = "agree", agree + 1
        else:
            verdict, disagree = "DISAGREE", disagree + 1
        print(f"{r['addr']:>8} "
              f"{('0x%02X' % w) if w is not None else '--':>6} "
              f"{('0x%02X' % e) if e is not None else '--':>6}  "
              f"{verdict:<9} {r['note']}")

    print(f"\n{agree} agree, {disagree} disagree, {failed} WMI read failures")
    if failed:
        print("  The WMI door did not answer. Nothing should be written through\n"
              "  it until a plain read works.")
    elif disagree:
        print("  The two doors report different values for the same address.\n"
              "  That is a finding in itself -- do not write through either\n"
              "  until it is understood.")
    elif agree:
        print("  The WMI door reads correctly and agrees with ECRR. That is the\n"
              "  prerequisite for trusting a write through it -- not permission\n"
              "  for one. A write probe is a separate file and a separate\n"
              "  decision.")

    if args.json:
        with open(args.json, "w") as fh:
            json.dump({"when": datetime.now().isoformat(timespec="seconds"),
                       "method": WMBC, "rows": rows}, fh, indent=2)
        print(f"\nwrote {args.json}")
    return 0 if agree and not failed and not disagree else 1


if __name__ == "__main__":
    sys.exit(main())
