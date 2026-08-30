#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_table_write_probe.py — are the fan curve tables writable at all?

Step one of re-running the universal-fan-control experiment. The previous run is
void: it happened while 0x0741 bit 0 was clear, so the EC was ignoring
everything (DESIGN.md §4.1). That bit is now confirmed set.

What is established before this probe:

  * 0x0F00-0x0F5F reads as 96 bytes of 0x00, and it is MAPPED, not absent --
    0x0F60 reads 0x84 and 0x0F80 reads 0x02, while genuinely unmapped space on
    this EC reads 0xFF (0x0980, 0x1000, 0x1804). The tables exist and are empty.
  * 0x07C6 bit 2 (ENABLE_UNIVERSAL_FAN_CTRL) is CLEAR, so the tables are inert.
  * The fans nonetheless follow a curve -- they engage near 55 C -- so the EC is
    running an internal default that does not live in these tables.

So this asks one question and no others: does ECRW reach 0x0Fxx and does the
value survive? 0x07xx is writable and 0x04xx silently discards; 0x0Fxx has never
been tested either way. If writes do not stick, the universal-control path is
dead regardless of any enable bit, and the answer is the WMI interface -- which
0x1804 reading 0xFF says is not reachable through ECRR at all.

SAFETY. This probe writes ONLY to the inert table region and restores it. It
never touches 0x07C6 (the enable bit), never touches 0x0751 (FAN_MODE_USER is a
one-way door that stops the fans until a power cycle), and refuses to run if
universal fan control is already enabled -- writing a live curve is a different
and far more dangerous experiment than writing an unused one.

    sudo python3 fan_table_write_probe.py
"""

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected      # noqa: E402

REG_AP_OEM = 0x0741           # bit 0 ENABLE_MANUAL_CTRL
REG_MANUAL_FAN_CTRL = 0x0751  # bit 7 FAN_MODE_USER -- never written here
REG_PWM_1 = 0x075B
REG_PWM_2 = 0x075C
REG_UNIVERSAL_FAN_CTRL = 0x07C5
REG_AP_OEM_6 = 0x07C6         # bit 2 ENABLE_UNIVERSAL_FAN_CTRL -- never written

# One slot in each of the six tables, so a partially-writable region shows up
# rather than being generalised from a single sample.
SLOTS = [
    (0x0F00, "CPU DownT[0]", 0x2A),
    (0x0F10, "CPU UpT[0]",   0x2B),
    (0x0F20, "CPU Duty[0]",  0x2C),
    (0x0F30, "GPU DownT[0]", 0x2D),
    (0x0F40, "GPU UpT[0]",   0x2E),
    (0x0F50, "GPU Duty[0]",  0x2F),
]


def hwmon_fans():
    """RPM from the driver path. Never read the tacho registers via ECRR."""
    import glob
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(os.path.join(d, "name")).read().strip() != "uniwill":
                continue
            return tuple(int(open(os.path.join(d, f)).read().strip())
                         for f in ("fan1_input", "fan2_input"))
        except OSError:
            continue
    return (None, None)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    ok, why = EC.available()
    if not ok:
        raise SystemExit(f"EC unavailable: {why}")

    ec = EC()
    run = {"started": datetime.now().isoformat(timespec="seconds"), "slots": []}

    # ── preconditions ───────────────────────────────────────────────────────
    ap_oem = ec.read(REG_AP_OEM)
    oem6 = ec.read(REG_AP_OEM_6)
    ufc = ec.read(REG_UNIVERSAL_FAN_CTRL)
    manual = ec.read(REG_MANUAL_FAN_CTRL)
    fans_before = hwmon_fans()

    print(f"0x0741 = 0x{ap_oem:02X}   ENABLE_MANUAL_CTRL  = "
          f"{'SET' if ap_oem & 1 else 'CLEAR'}")
    print(f"0x07C6 = 0x{oem6:02X}   ENABLE_UNIVERSAL_FAN = "
          f"{'SET' if oem6 & 0x04 else 'clear'}")
    print(f"0x07C5 = 0x{ufc:02X}   SPLIT_TABLES        = "
          f"{'set' if ufc & 0x80 else 'clear'}")
    print(f"0x0751 = 0x{manual:02X}   FAN_MODE_USER       = "
          f"{'ARMED' if manual & 0x80 else 'clear'}")
    print(f"fans: {fans_before[0]} / {fans_before[1]} rpm\n")

    if not ap_oem & 1:
        raise SystemExit(
            "0x0741 bit 0 is CLEAR -- the EC ignores host writes and any result "
            "here would be void, exactly as the previous run was (§4.1).\n"
            "Reload the driver first: sudo modprobe -r uniwill-laptop && "
            "sudo modprobe uniwill-laptop")
    if oem6 & 0x04:
        raise SystemExit(
            "ENABLE_UNIVERSAL_FAN_CTRL is already SET, so these tables may be "
            "live. Writing a curve the EC is acting on is a different and far "
            "more dangerous experiment than this one. Refusing.")
    if manual & 0x80:
        raise SystemExit(
            "FAN_MODE_USER is armed -- the fans are off automatic control. "
            "Power cycle before probing anything.")

    run["pre"] = {"0x0741": ap_oem, "0x07C6": oem6, "0x07C5": ufc,
                  "0x0751": manual, "fans": fans_before}

    # ── the question ────────────────────────────────────────────────────────
    print(f"{'slot':16} {'base':>5} {'wrote':>6} {'read':>6}  result")
    print("-" * 58)
    writable = 0
    for addr, name, val in SLOTS:
        base = ec.read(addr)
        rec = {"addr": f"0x{addr:04X}", "name": name, "baseline": base,
               "wrote": val}
        try:
            ec.write_verify(addr, val)
            time.sleep(0.05)
            got = ec.read(addr)
            rec["readback"] = got
            rec["held"] = got == val
            if got == val:
                writable += 1
                verdict = "WRITABLE"
            else:
                verdict = f"took then changed -> 0x{got:02X}"
        except (ECWriteRejected, ECUnavailable) as e:
            rec["readback"] = None
            rec["held"] = False
            verdict = f"rejected ({type(e).__name__})"

        print(f"{name:16} {base:>5} {val:>6} "
              f"{rec['readback'] if rec['readback'] is not None else '-':>6}"
              f"  {verdict}")
        run["slots"].append(rec)

    # ── restore, always ─────────────────────────────────────────────────────
    print("\nrestoring baselines...")
    bad = []
    for rec in run["slots"]:
        addr = int(rec["addr"], 16)
        want = rec["baseline"]
        try:
            ec.write_verify(addr, want)
            got = ec.read(addr)
        except (ECWriteRejected, ECUnavailable):
            got = None
        rec["restored"] = got == want
        if got != want:
            bad.append(f"{rec['addr']} wanted 0x{want:02X} got "
                       f"{'--' if got is None else f'0x{got:02X}'}")
    print("  " + ("all restored" if not bad else "NOT RESTORED: " + "; ".join(bad)))

    time.sleep(1.0)
    fans_after = hwmon_fans()
    oem6_after = ec.read(REG_AP_OEM_6)
    run["post"] = {"fans": fans_after, "0x07C6": oem6_after}
    print(f"\nfans after: {fans_after[0]} / {fans_after[1]} rpm")
    print(f"0x07C6 still 0x{oem6_after:02X} (enable bit untouched)")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"fan-table-write-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(out, "w") as fh:
        json.dump(run, fh, indent=2)
    print(f"\nartifact: {out}")

    print("\nInterpretation:")
    if writable == len(SLOTS):
        print("  All six tables take ECRW writes and hold them. The region is")
        print("  writable, so populating a curve is possible and the remaining")
        print("  question is whether ENABLE_UNIVERSAL_FAN_CTRL makes the EC use")
        print("  it. Populate the tables BEFORE setting that bit -- enabling it")
        print("  against an all-zero curve is a fan-stop waiting to happen.")
    elif writable:
        print(f"  Only {writable}/{len(SLOTS)} slots held. A partially writable")
        print("  region is not a curve; do not enable universal control.")
    else:
        print("  Nothing held. ECRW does not reach 0x0Fxx, so the")
        print("  universal-control path is dead from userspace regardless of the")
        print("  enable bit -- and 0x1804 reads 0xFF, so the WMI-only registers")
        print("  are not reachable this way either. The answer is a WMI method.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
