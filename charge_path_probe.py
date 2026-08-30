#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
charge_path_probe.py — A/B test of the two charge-threshold write paths.

Background. hydroc-driver/acpi_table_fix.asl (the FIXCGLM overlay) claims
ECRW->MMRW writes to 0x07B9 are silently ignored because MMRW uses a NoLock
OperationRegion, and that the working path is SCHG->WKBC. That premise is
DISPROVEN on this EC -- plain ECRW writes to 0x07B9 land and hold for hours.
Note also that SCHG and GCHG exist nowhere in this machine's DSDT: they are
defined only by the overlay, which is not loaded (stock SSDT12 is 14810 bytes,
the overlay .aml is 338). This probe replicates SCHG's register sequence via
WKBC directly, which is the only way to exercise that path here.

What this probe answers: for each register, which of ECRW / WKBC actually
lands, and whether the value SURVIVES rather than merely reading back once.
write_verify proves a register took a value, not that it kept it (DESIGN.md
§2, §4.1), so every value is read immediately AND again after --settle.

Test A writes pct_a via ECRW; test B writes a DIFFERENT value, pct_b, via
WKBC. They must differ: if both paths write the same number, the second
result is indistinguishable from the first and the test proves nothing. That
flaw voided the WKBC->0x07B9 result of the 2026-08-27 run.

Test B replicates the overlay's SCHG exactly, including its read-modify-write
of 0x07A6 and its habit of forcing the profile to balanced (0x10) whenever the
limit is under 100. --profile therefore applies to test A only.

Restore is itself an experiment: each register is put back via WKBC, verified,
and retried via ECRW if WKBC did not take. Which path succeeded is recorded.
Restore runs in a finally block, so an interrupt still puts the machine back.

All registers are volatile EC RAM -- a power cycle restores factory defaults.
Every run writes a timestamped JSON artifact, so results are evidence rather
than terminal scrollback.

    sudo python3 charge_path_probe.py [--pct-a 80] [--pct-b 75] [--settle 5]
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"
RKBC = r"\_SB.AMW0.RKBC"
WKBC = r"\_SB.AMW0.WKBC"

BAT = "/sys/class/power_supply/BAT0"

# The EC services ACPI calls on the same firmware loop that runs fan control.
# uniwill-acpi.c sleeps 6 ms after every ECRR/ECRW because the OEM software
# does; hydroc/ec.py enforces it class-wide. This probe does not import
# hydroc.ec, so it enforces the same floor itself. DESIGN.md §4.2.
EC_DELAY = 0.006
_last_call = 0.0

# Every write attempt, as (path, addr, intended). An artifact that records only
# what was observed cannot distinguish a write that never happened from one that
# was rejected, so the intent is logged alongside the outcome.
WRITE_LOG = []

REGS = {
    0x07B9: "CHARGE_CTRL (threshold b0-6, b7 REACHED)",
    0x07A6: "OEM_4 (charging profile [5:4])",
    0x0497: "charge-limit-mode flag (bit 5)",
    0x087F: "overlay 'shadow limit'",
    0x04AB: "overlay 'SoC gate'",
    0x07CD: "overlay GCHG readback slot",
}

# RKBC returns AC00, a 40-byte buffer, and fills it with 0xFEFEFEFEFEFEFEFE on
# every failure path (EC busy, ECOK clear, DRDY timeout). Byte 0 of that is a
# perfectly plausible register value, so a failed read is indistinguishable
# from real data unless the whole sentinel is checked.
RKBC_SENTINEL = [0xFE] * 8


def _call(expr):
    """One /proc/acpi/call round trip, never faster than EC_DELAY apart."""
    global _last_call
    gap = time.monotonic() - _last_call
    if gap < EC_DELAY:
        time.sleep(EC_DELAY - gap)
    with open(CALL, "w") as fh:
        fh.write(expr)
    with open(CALL) as fh:
        raw = fh.read().strip().rstrip("\x00")
    _last_call = time.monotonic()
    return raw


def ec_read(addr):
    raw = _call(f"{ECRR} 0x{addr:X}")
    if raw.startswith("Error"):
        return None
    try:
        return int(raw, 16) & 0xFF
    except ValueError:
        return None


def ec_write(addr, val):
    WRITE_LOG.append(("ecrw", addr, val & 0xFF))
    _call(f"{ECRW} 0x{addr:X} 0x{val & 0xFF:X}")


def rkbc_read(addr):
    """Read via the SFR path. Returns the byte, or None on failure.

    WKBC/RKBC take the target address as separate low and high bytes
    (LDAT/HDAT). Those fields are 8 bits wide, so passing the full 16-bit
    address happens to truncate correctly -- but only by accident. Split it
    explicitly.
    """
    raw = _call(f"{RKBC} 0x{addr & 0xFF:X} 0x{(addr >> 8) & 0xFF:X}")
    if raw.startswith("Error"):
        return None
    try:
        if raw.startswith("Buffer"):
            hexpart = raw.split("{", 1)[1].split("}", 1)[0]
            vals = [int(x, 16) for x in hexpart.split(",") if x.strip()]
            if vals[:8] == RKBC_SENTINEL:
                return None                       # EC busy / timeout, not data
            return vals[0] if vals else None      # SA00 = CMDL
        return int(raw, 16) & 0xFF
    except (ValueError, IndexError):
        return None


def wkbc_write(addr, val):
    WRITE_LOG.append(("wkbc", addr, val & 0xFF))
    _call(f"{WKBC} 0x{addr & 0xFF:X} 0x{(addr >> 8) & 0xFF:X} 0x{val & 0xFF:X} 0x0")


def _fmt(v):
    return "----" if v is None else f"0x{v:02X}"


def snapshot(tag, settle=0.0):
    """Read every register, optionally twice, and return a dict.

    The second read is what distinguishes "the write landed" from "the write
    survived" -- the EC can zero a register on its own schedule after an
    immediate readback has already passed.
    """
    now = {addr: {"ecrr": ec_read(addr), "rkbc": rkbc_read(addr)} for addr in REGS}
    later = None
    if settle:
        time.sleep(settle)
        later = {addr: {"ecrr": ec_read(addr), "rkbc": rkbc_read(addr)} for addr in REGS}

    print(f"\n--- {tag} ---")
    for addr, name in REGS.items():
        e, r = now[addr]["ecrr"], now[addr]["rkbc"]
        line = f"  0x{addr:04X} {name:<38} ECRR={_fmt(e)}  RKBC={_fmt(r)}"
        if later is not None:
            e2 = later[addr]["ecrr"]
            line += f"  after {settle:g}s={_fmt(e2)}"
            if e2 != e:
                line += "  <-- DID NOT SURVIVE"
        print(line)

    return {"tag": tag, "immediate": {f"0x{a:04X}": v for a, v in now.items()},
            "after_settle": None if later is None else
            {f"0x{a:04X}": v for a, v in later.items()},
            "settle_s": settle}


def battery_sysfs():
    out = {}
    for f in ("capacity", "charge_control_end_threshold", "status",
              "current_now", "charge_types"):
        try:
            out[f] = open(os.path.join(BAT, f)).read().strip()
        except OSError:
            out[f] = None
    return out


def restore(baseline, settle):
    """Put every register back, WKBC first, ECRW as fallback. Reports which
    path worked -- that is data, not just cleanup."""
    print("\n=== RESTORE ===")
    results = {}
    for addr, want in baseline.items():
        if want is None:
            results[f"0x{addr:04X}"] = {"want": None, "via": None, "ok": None}
            continue

        wkbc_write(addr, want)
        time.sleep(0.3)
        got = ec_read(addr)
        via, ok = "wkbc", got == want

        if not ok:
            ec_write(addr, want)
            time.sleep(0.3)
            got = ec_read(addr)
            via, ok = "ecrw", got == want

        # Landing is not surviving; confirm it is still there after settling.
        if ok and settle:
            time.sleep(settle)
            got = ec_read(addr)
            ok = got == want

        mark = "ok" if ok else "FAILED"
        note = "" if ok else "  <-- REGISTER LEFT CHANGED"
        print(f"  0x{addr:04X} -> {_fmt(want)} via {via:<4} got {_fmt(got)}  {mark}{note}")
        results[f"0x{addr:04X}"] = {"want": want, "got": got, "via": via, "ok": ok}

    bad = [k for k, v in results.items() if v["ok"] is False]
    if bad:
        print(f"\n  !! {len(bad)} register(s) NOT restored: {', '.join(bad)}")
        print("     A full power cycle restores EC defaults.")
    else:
        print("\n  all registers restored to baseline")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pct-a", type=int, default=80,
                    help="threshold written by test A, via ECRW")
    ap.add_argument("--pct-b", type=int, default=75,
                    help="threshold written by test B, via WKBC; must differ "
                         "from --pct-a or test B proves nothing")
    ap.add_argument("--profile", type=lambda x: int(x, 0), default=0x10,
                    help="test A profile byte for 0x07A6 [5:4] "
                         "(0x00=100%%, 0x10=~80%%, 0x20=~60%%)")
    ap.add_argument("--settle", type=float, default=5.0,
                    help="seconds before the survival re-read")
    ap.add_argument("--out", default=None, help="JSON artifact path")
    args = ap.parse_args()

    # Argument validation first: it needs no privilege, so a bad invocation
    # fails the same way for any caller.
    for name, v in (("--pct-a", args.pct_a), ("--pct-b", args.pct_b)):
        if not 1 <= v <= 100:
            raise SystemExit(f"{name} must be 1-100")
    if args.pct_a == args.pct_b:
        raise SystemExit("--pct-a and --pct-b must differ, or test B is "
                         "indistinguishable from test A")
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    if not os.path.exists(CALL):
        raise SystemExit("run: sudo modprobe acpi_call")

    pct_a, pct_b, prof = args.pct_a, args.pct_b, args.profile & 0xFF
    run = {"started": datetime.now().isoformat(timespec="seconds"),
           "pct_a": pct_a, "pct_b": pct_b, "profile": prof,
           "settle_s": args.settle, "battery_before": battery_sysfs(),
           "steps": []}

    print(f"test A (ECRW) pct={pct_a} profile=0x{prof:02X}   "
          f"test B (WKBC) pct={pct_b}   settle={args.settle:g}s")
    print(f"battery: {run['battery_before']['capacity']}% "
          f"{run['battery_before']['status']}, "
          f"sysfs threshold {run['battery_before']['charge_control_end_threshold']}")

    base = snapshot("baseline", args.settle)
    run["steps"].append(base)
    baseline = {a: base["immediate"][f"0x{a:04X}"]["ecrr"] for a in REGS}

    if baseline[0x07B9] is None:
        raise SystemExit("cannot read 0x07B9 -- refusing to write blind")

    # A test value equal to what the register already holds is as blind as
    # the two tests writing the same number: the readback cannot distinguish
    # "the write landed" from "nothing happened".
    for name, v in (("--pct-a", pct_a), ("--pct-b", pct_b)):
        if baseline[0x07B9] == v:
            raise SystemExit(
                f"0x07B9 already reads {v}; {name} must differ from the "
                "current value or the write is indistinguishable from a no-op")

    try:
        # --- Test A: the current path (plain ECRW, what the driver does) ---
        print(f"\n=== TEST A: ECRW  (0x07B9={pct_a}, 0x07A6=0x{prof:02X}) ===")
        WRITE_LOG.clear()
        ec_write(0x07B9, pct_a)
        ec_write(0x07A6, prof)
        time.sleep(0.3)
        step = snapshot("after ECRW", args.settle)
        step["attempted"] = [(p, f"0x{a:04X}", v) for p, a, v in WRITE_LOG]
        run["steps"].append(step)

        # --- Test B: the overlay's SCHG sequence, replicated via WKBC ---
        print(f"\n=== TEST B: WKBC / SCHG sequence  (limit={pct_b}) ===")
        # SCHG read-modify-writes whatever 0x07A6 currently holds and forces
        # balanced (bit 4) for any limit under 100; it does not honour a
        # caller-supplied profile. Replicate that exactly.
        WRITE_LOG.clear()
        cur_a6 = ec_read(0x07A6)
        if cur_a6 is None:
            raise SystemExit("cannot read 0x07A6 -- aborting test B")
        new_a6 = cur_a6 & 0xCF
        if pct_b < 100:
            new_a6 |= 0x10
        wkbc_write(0x07A6, new_a6)
        wkbc_write(0x07B9, pct_b)

        cur_97 = ec_read(0x0497)
        if cur_97 is not None:
            new_97 = (cur_97 | 0x20) if pct_b < 100 else (cur_97 & 0xDF)
            wkbc_write(0x0497, new_97)

        wkbc_write(0x087F, pct_b)
        if pct_b < 100:
            wkbc_write(0x04AB, 0xFF)
        wkbc_write(0x07CD, pct_b)
        time.sleep(0.3)
        step = snapshot("after WKBC", args.settle)
        step["attempted"] = [(p, f"0x{a:04X}", v) for p, a, v in WRITE_LOG]
        run["steps"].append(step)
    finally:
        run["restore"] = restore(baseline, args.settle)
        run["battery_after"] = battery_sysfs()

    # --- Verdict per path, computed rather than eyeballed ---
    def val(step, addr):
        return step["immediate"][f"0x{addr:04X}"]["ecrr"]

    print("\n=== WRITES ATTEMPTED vs OBSERVED ===")
    for step in run["steps"]:
        if not step.get("attempted"):
            continue
        print(f"  {step['tag']}:")
        for path, key, want in step["attempted"]:
            got = step["immediate"][key]["ecrr"]
            later = (step["after_settle"] or {}).get(key, {}).get("ecrr")
            if got != want:
                note = "REJECTED (value unchanged by the write)"
            elif later is not None and later != got:
                note = "took, then the EC overwrote it"
            else:
                note = "took and held"
            print(f"    {path} {key} <- {_fmt(want)}   observed {_fmt(got)}"
                  f"{'' if later is None else ' / ' + _fmt(later)}   {note}")

    verdict = {}
    if len(run["steps"]) >= 2:
        for addr in REGS:
            v = {"baseline": baseline[addr], "after_ecrw": val(run["steps"][1], addr)}
            if len(run["steps"]) >= 3:
                v["after_wkbc"] = val(run["steps"][2], addr)
            verdict[f"0x{addr:04X}"] = v
    run["verdict"] = verdict

    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"charge-path-probe-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(out, "w") as fh:
        json.dump(run, fh, indent=2)
    print(f"\nartifact: {out}")

    print("\nInterpretation:")
    print(f"  0x07B9 == {pct_a} after test A  -> ECRW writes it")
    print(f"  0x07B9 == {pct_b} after test B  -> WKBC writes it")
    print(f"  0x07B9 still {pct_a} after test B -> WKBC does NOT write it,")
    print("    which inverts the FIXCGLM overlay's premise.")
    print("  A value that changes between the immediate and settled read did")
    print("  not survive -- that is the EC rewriting it, not a failed write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
