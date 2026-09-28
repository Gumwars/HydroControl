#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Low EC RAM (0x00-0xFF), the address space this project has never touched.
READ-ONLY.

Everything we have ever read or written goes through `\\_SB.INOU.ECRR/ECRW`
(MMIO at 0xFE410000) or, since yesterday, the WMI method at `\\_SB.AMW0.WMBC`.
Both reach the *extended* EC space -- 0x04xx live readback, 0x07xx settings,
0x0Fxx fan tables. Neither can see below 0xFF.

The standard ACPI EC address space lives there instead, reachable through the
EC command/data ports and exposed by the `ec_sys` module at
/sys/kernel/debug/ec/ec0/io. tuxedo-drivers uses that space as a mailbox --
uw_ec_write_addr_direct() pokes UNIWILL_EC_REG_FLAGS and _LDAT down there to
drive the extended window -- so it is real, it is used, and we have no map of
it at all.

WHY NOW

A user on this model reports that forcing 0x35 with a sustained write loop (a
single write is corrected back within 1-5 s) freezes `current_now` at a
suppressed value while `charge_now` keeps rising at its true rate. If that is
right, 0x35 is close to the machinery that produces a reported current, which
is the same machinery a "charging stopped" display would run through.

WHAT THIS DOES

Surveys. It samples all 256 bytes repeatedly and reports which ones move,
alongside battery state, so a byte that tracks charge current is visible as a
correlation rather than a guess about one address. Watching only 0x35 would
have told us whether 0x35 changed; it would not have told us what else did.

There is no write path in this file. The EC file is opened 'rb', never 'r+b',
and `ec_sys` is loaded without write_support.

    sudo python3 lowec_probe.py --dump
    sudo python3 lowec_probe.py --survey 60
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

EC_IO = "/sys/kernel/debug/ec/ec0/io"
BAT = "/sys/class/power_supply/BAT0"

# The standard ACPI EC space is kernel-serialised, so this is far gentler than
# an ECRR loop over the extended window -- but sustained EC traffic is the
# documented hazard in this project (DESIGN.md 4.2) and one full read is 256
# bytes, so the default cadence stays slow.
DEFAULT_PERIOD = 2.0

# Named by tuxedo-drivers for the extended-window mailbox. Knowing which bytes
# are traffic rather than state keeps them out of the "interesting" list.
KNOWN = {
    0x00: "UNIWILL_EC_REG_FLAGS (mailbox)",
    0x01: "UNIWILL_EC_REG_LDAT (mailbox addr low)",
    0x02: "UNIWILL_EC_REG_HDAT (mailbox addr high)",
    0x03: "UNIWILL_EC_REG_DATA (mailbox data)",
    0x35: "reported by a user as implicated in suppressed current_now",
}


def ensure_ec_sys() -> str | None:
    """Load ec_sys read-only if needed. Returns an error string, or None."""
    if os.path.exists(EC_IO):
        return None
    r = subprocess.run(["modprobe", "ec_sys"], capture_output=True, text=True)
    if r.returncode != 0:
        return (r.stderr or r.stdout).strip() or "modprobe ec_sys failed"
    if not os.path.exists(EC_IO):
        return (f"{EC_IO} still absent. debugfs may not be mounted: "
                "sudo mount -t debugfs none /sys/kernel/debug")
    return None


def write_support_on() -> bool:
    """Is ec_sys loaded with writes enabled? We never write, but a machine
    where anything *could* is worth naming out loud."""
    try:
        with open("/sys/module/ec_sys/parameters/write_support") as fh:
            return fh.read().strip().lower() in ("y", "1", "true")
    except OSError:
        return False


def read_ec() -> bytes | None:
    """All 256 bytes, one read. 'rb' -- there is no write mode in this file."""
    try:
        with open(EC_IO, "rb") as fh:
            data = fh.read(256)
        return data if len(data) == 256 else None
    except OSError:
        return None


def battery() -> dict:
    def val(name):
        try:
            with open(os.path.join(BAT, name)) as fh:
                return fh.read().strip()
        except OSError:
            return None
    cur = val("current_now")
    return {"capacity": val("capacity"), "status": val("status"),
            "current_ma": None if cur is None else round(int(cur) / 1000),
            "charge_now": val("charge_now"), "voltage_now": val("voltage_now")}


def dump(data: bytes) -> None:
    print("      " + " ".join(f"{i:02X}" for i in range(16)))
    for row in range(16):
        base = row * 16
        cells = " ".join(f"{data[base + c]:02X}" for c in range(16))
        print(f"  {base:02X}: {cells}")


def survey(seconds: float, period: float) -> dict:
    """Sample everything repeatedly; report what moved and what it moved with.

    The point is correlation. A byte that steps down as charge current steps
    down is a candidate; a byte that flickers independently is noise or
    mailbox traffic. One address watched alone cannot tell those apart.
    """
    samples = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        data = read_ec()
        if data is None:
            print("  ! EC read failed", flush=True)
            time.sleep(period)
            continue
        b = battery()
        samples.append({"t": datetime.now().isoformat(timespec="seconds"),
                        "ec": list(data), **b})
        print(f"  {samples[-1]['t'][11:]}  {b['capacity']}%  "
              f"{b['current_ma']} mA  0x35={data[0x35]:02X}", flush=True)
        time.sleep(period)

    moved = {}
    for addr in range(256):
        vals = [s["ec"][addr] for s in samples]
        if len(set(vals)) > 1:
            moved[addr] = vals
    return {"samples": samples, "moved": moved}


def report(res: dict) -> None:
    samples, moved = res["samples"], res["moved"]
    if not samples:
        print("\nno samples taken")
        return
    cur = [s["current_ma"] for s in samples if s["current_ma"] is not None]
    print(f"\n{len(samples)} samples. battery current "
          f"{cur[0] if cur else '?'} -> {cur[-1] if cur else '?'} mA, "
          f"capacity {samples[0]['capacity']} -> {samples[-1]['capacity']}%")

    if not moved:
        print("\nNo byte in 0x00-0xFF changed. Either nothing down here tracks\n"
              "charging, or the sampling window was too short to catch it.")
        return

    print(f"\n{len(moved)} addresses changed:")
    for addr, vals in sorted(moved.items()):
        uniq = sorted(set(vals))
        tag = f"  <- {KNOWN[addr]}" if addr in KNOWN else ""
        shown = " ".join(f"{v:02X}" for v in vals[:12])
        print(f"  0x{addr:02X}: {shown}{' ...' if len(vals) > 12 else ''}"
              f"   ({len(uniq)} distinct){tag}")

    # A byte that moves monotonically with current is the thing worth chasing.
    if len(cur) >= 3 and len(set(cur)) > 1:
        print("\nmonotonic with falling charge current (candidates):")
        hits = 0
        for addr, vals in sorted(moved.items()):
            pairs = [(c, v) for c, v in zip(cur, vals)]
            if len(pairs) < 3:
                continue
            down = all(b[1] <= a[1] for a, b in zip(pairs, pairs[1:]))
            up = all(b[1] >= a[1] for a, b in zip(pairs, pairs[1:]))
            if down or up:
                hits += 1
                print(f"  0x{addr:02X}: {'falls' if down else 'rises'} as "
                      f"current falls{'  <- ' + KNOWN[addr] if addr in KNOWN else ''}")
        if not hits:
            print("  none -- every changing byte moves independently of current")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read low EC RAM. Read-only.")
    ap.add_argument("--dump", action="store_true", help="one 256-byte dump")
    ap.add_argument("--survey", type=float, metavar="SECONDS",
                    help="sample repeatedly and report what moved")
    ap.add_argument("--period", type=float, default=DEFAULT_PERIOD)
    ap.add_argument("--json", help="write the survey here")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")

    err = ensure_ec_sys()
    if err:
        raise SystemExit(f"cannot reach low EC RAM: {err}")
    if write_support_on():
        print("  note: ec_sys is loaded with write_support=1. This probe still\n"
              "        only reads, but anything else on the system could write.\n")

    if args.survey:
        print(f"surveying 0x00-0xFF every {args.period}s for {args.survey:.0f}s\n")
        res = survey(args.survey, args.period)
        report(res)
        if args.json:
            with open(args.json, "w") as fh:
                json.dump({"when": datetime.now().isoformat(timespec="seconds"),
                           "moved": {f"0x{a:02X}": v for a, v in res["moved"].items()},
                           "samples": res["samples"]}, fh, indent=2)
            print(f"\nwrote {args.json}")
        return 0

    data = read_ec()
    if data is None:
        raise SystemExit("EC read failed")
    b = battery()
    print(f"battery: {b['capacity']}%  {b['current_ma']} mA  {b['status']}\n")
    dump(data)
    print()
    for addr, note in sorted(KNOWN.items()):
        print(f"  0x{addr:02X} = 0x{data[addr]:02X}   {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
