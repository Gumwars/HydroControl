#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
What drives the CPU's TCC offset, and what survives sleep, power changes,
mode changes and power cycles. See TCC-OFFSET-TESTS.md for the runs.

Two places hold a TCC offset:

    EC 0x0786          what Control Center writes: b7 enable, b6:0 degrees,
                       0 = firmware default (ec.REG_TCC_OFFSET)
    the CPU            MSR_TEMPERATURE_TARGET 29:24, the offset in force,
                       read through the kernel's "TCC Offset" cooling device

The question is how they relate: does the EC put 0x0786 into the CPU, does it
put its own per-mode default (0x07D8-0x07DA) there when 0x0786 is 0, and does
it overwrite a value written to the CPU directly?

    sudo python3 tcc_survival_probe.py snap "before suspend"
    sudo python3 tcc_survival_probe.py watch --minutes 10
    sudo python3 tcc_survival_probe.py set-ec 10       # one EC write, then follow
    sudo python3 tcc_survival_probe.py set-ec off
    sudo python3 tcc_survival_probe.py set-msr 10      # one CPU write, then follow

Every write is a single write. Never hold a value with a loop: sustained
re-writes to the charge registers latched a state that took an EC reflash to
clear (DESIGN.md 4.1b). EC reads are 6 ms paced by hydroc.ec, and a snapshot
is six reads, so even `watch` at its 5 s default is a light load.

Rows go to tcc-survival.csv (or -o), one per snapshot, so a run is a record.
"""

from __future__ import annotations

import argparse
import csv
import os
import signal
import sys
import threading
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc import batterylight, nativemode, tcc  # noqa: E402
from hydroc.ec import (EC, REG_TCC_DEFAULTS, REG_TCC_OFFSET,  # noqa: E402
                       TCC_ENABLE, TCC_OFFSET_MAX)

FIELDS = ["time", "label", "applied", "tjmax", "ec_0786", "ec_enable",
          "ec_offset", "default_balanced", "default_office", "default_beast",
          "mode_0751", "mode", "custom_latch", "ac"]


def snapshot(ec: EC, label: str) -> dict:
    raw = ec.read(REG_TCC_OFFSET)
    nm = nativemode.current(ec)
    row = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "label": label,
        "applied": tcc.applied(),
        "tjmax": tcc.tjmax(),
        "ec_0786": f"0x{raw:02X}",
        "ec_enable": int(bool(raw & TCC_ENABLE)),
        "ec_offset": raw & 0x7F,
        "mode_0751": f"0x{nm['raw']:02X}",
        "mode": nm["mode"],
        "custom_latch": int(bool(nm["custom_latched"])),
        "ac": {True: 1, False: 0, None: ""}[batterylight.on_external_power()],
    }
    for m, a in REG_TCC_DEFAULTS.items():
        row[f"default_{m}"] = ec.read(a)
    return row


# Ctrl+C sets this instead of raising, so a stop lands between snapshots --
# never between an acpi_call request and its answer -- and the run still ends
# with a final row rather than a traceback.
STOP = threading.Event()
TTY = sys.stderr.isatty()


def status(text: str) -> None:
    """A transient countdown line; rows printed later overwrite it."""
    if TTY:
        print("\r\033[K" + text, end="", file=sys.stderr, flush=True)


def show(row: dict) -> None:
    status("")
    ec_part = (f"0x0786 {row['ec_0786']} (" + (f"enabled, -{row['ec_offset']}"
               if row["ec_enable"] else "firmware") + ")")
    print(f"{row['time']}  {row['label']:<22} CPU -{row['applied']} °C   {ec_part}   "
          f"defaults b/o/B {row['default_balanced']}/{row['default_office']}/"
          f"{row['default_beast']}   mode {row['mode']} {row['mode_0751']}"
          f"  latch {row['custom_latch']}  ac {row['ac']}", flush=True)


class Log:
    def __init__(self, path: str) -> None:
        new = not os.path.exists(path)
        self._fh = open(path, "a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=FIELDS)
        if new:
            self._w.writeheader()

    def add(self, row: dict) -> None:
        self._w.writerow(row)
        self._fh.flush()
        show(row)


def follow(ec: EC, log: Log, label: str, seconds: float, interval: float) -> None:
    """Snapshot at `interval`, logging only rows where something changed.

    Quiet by design between changes, so a countdown shows it is still going.
    """
    keys = ("applied", "ec_0786", "mode_0751", "custom_latch", "ac")
    last = None
    end = time.monotonic() + seconds
    print(f"following for {seconds:.0f} s, a row only when something changes "
          f"(Ctrl+C stops early)", file=sys.stderr, flush=True)
    while time.monotonic() < end:
        row = snapshot(ec, label)
        sig = tuple(row[k] for k in keys)
        if sig != last:
            log.add(row)
            last = sig
        status(f"  {end - time.monotonic():4.0f} s left, CPU -{row['applied']} °C, "
               f"0x0786 {row['ec_0786']}")
        if STOP.wait(interval):
            log.add(snapshot(ec, label + " (stopped)"))
            return
    log.add(snapshot(ec, label + " (end)"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-o", "--output", default="tcc-survival.csv")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap", help="one snapshot")
    s.add_argument("label")
    w = sub.add_parser("watch", help="log every change")
    w.add_argument("--minutes", type=float, default=10)
    w.add_argument("--interval", type=float, default=5)
    w.add_argument("--label", default="watch")
    e = sub.add_parser("set-ec", help="one write to 0x0786, then follow 60 s")
    e.add_argument("value", help=f"0-{TCC_OFFSET_MAX}, or 'off' for firmware default")
    m = sub.add_parser("set-msr", help="one write to the CPU via the kernel, then follow 60 s")
    m.add_argument("value", type=int)
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    ok, why = EC.available()
    if not ok:
        raise SystemExit(f"EC unavailable: {why}")
    ec = EC()
    log = Log(args.output)
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    if args.cmd == "snap":
        log.add(snapshot(ec, args.label))
    elif args.cmd == "watch":
        follow(ec, log, args.label, args.minutes * 60, args.interval)
    elif args.cmd == "set-ec":
        log.add(snapshot(ec, "before set-ec"))
        if STOP.is_set():
            return 130
        if args.value == "off":
            ec.set_tcc_offset(False)
            label = "after set-ec off"
        else:
            ec.set_tcc_offset(True, int(args.value))
            label = f"after set-ec {int(args.value)}"
        follow(ec, log, label, 60, 1)
    elif args.cmd == "set-msr":
        d = tcc.device()
        if not d:
            raise SystemExit("no TCC Offset cooling device (intel_tcc_cooling not loaded)")
        if not 0 <= args.value <= (tcc.maximum() or 0):
            raise SystemExit(f"value out of range 0-{tcc.maximum()}")
        log.add(snapshot(ec, "before set-msr"))
        if STOP.is_set():
            return 130
        with open(os.path.join(d, "cur_state"), "w") as fh:
            fh.write(str(args.value))
        follow(ec, log, f"after set-msr {args.value}", 60, 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
