#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
profile_button_probe.py — find where the performance mode lives in the EC.

The physical profile button beside the power key cycles the machine's
performance mode and drives the LED next to it. Windows exposes the same thing
as Office / Gaming / Turbo. We do not know how it is encoded.

The standing hypothesis (DESIGN.md open question 3) is the low bits of
`0x0727` -- the same register whose bit 6 arms custom TDP and whose bit 7
selects half-scale PL4. tuxedo-drivers has `PROFILE_POWERSAVE=1`,
`ENTHUSIAST=2`, `OVERBOOST=3` for other Uniwill machines.

This watches a spread of candidate registers and prints only what CHANGES when
you press the button. Nothing is written, so this cannot put the machine into a
state a power cycle will not clear.

    sudo python3 profile_button_probe.py --label fan-profile

There is a BIOS toggle that switches the button between changing PERFORMANCE
MODES and changing FAN PROFILES, so the same button writes different things
depending on a setting this software cannot see. Run this once in each BIOS
position and pass --label so the two outputs cannot be confused; the fan-profile
run is not a detour, it addresses the other open question (fan curves).

Press the profile button a few times, cycling all the way round. Ctrl-C to stop
and print the summary.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable      # noqa: E402

# 0x0727 is the prime suspect, and 0x07A5 is the other one: ec_dump.py already
# labels it "OEM_3 <-- FAN_QUIET/OVERBOOST/HIGH_POWER", which is both candidate
# meanings of the button in one register. The rest are neighbours plus the
# fan/TDP block, since a mode change probably rewrites more than one byte.
WATCH = [0x07A6,                                      # <-- CONFIRMED: the mode lives here
         0x0727, 0x0726, 0x0728, 0x0729, 0x072A,
         0x07A5, 0x07A4, 0x07A7,
         0x0783, 0x0784, 0x0785,                      # PL1 / PL2 / PL4
         0x0751, 0x075B, 0x075C, 0x0768,              # fan ctrl / pwm / switch status
         0x078E, 0x0730]

POLL = 0.25

# Per-register bit meanings, from uniwill-acpi.c. A bare bitmask diff is not
# readable enough to spot that a "performance mode" press just moved a bit the
# driver believes belongs to the charging profile.
REG_NOTES = {
    0x07A6: {0: "performance mode, low bit (UNNAMED in the driver)",
             1: "OVERBOOST_DYN_TEMP_OFF",
             4: "charging profile (bits 5:4)",
             5: "charging profile (bits 5:4)",
             6: "TOUCHPAD_TOGGLE_OFF"},
    0x0727: {6: "custom-profile latch (TDP writes need this)",
             7: "double-PL4 (half-scale storage)"},
    0x0751: {4: "FAN_MODE_TURBO", 5: "FAN_MODE_HIGH",
             6: "FAN_MODE_BOOST", 7: "FAN_MODE_USER"},
    0x0768: {0: "SUPER_KEY_LOCK_STATUS", 1: "LIGHTBAR_STATUS",
             2: "FAN_BOOST_STATUS"},
}


def bits(v: int) -> str:
    return format(v, "08b")


def decode(addr: int, changed: int) -> str:
    notes = REG_NOTES.get(addr, {})
    hits = [notes[b] for b in range(8) if changed >> b & 1 and b in notes]
    return ", ".join(dict.fromkeys(hits))


# --- scan mode ----------------------------------------------------------
# A null result from 18 hand-picked registers means the guess was wrong, not
# that nothing moved. These two windows are the settings space and the live
# readback mirror; scanning both beats guessing again.
SCAN_RANGES = [(0x0700, 0x07FF), (0x0400, 0x04FF)]

FAN_FILES = ("pwm1", "pwm2", "fan1_input", "fan2_input", "temp1_input", "temp2_input")


def fan_snapshot() -> dict:
    """Fan duty and RPM straight from hwmon.

    The EC may never surface a two-state adaptive/full fan setting as a
    register, but it cannot hide the fans themselves. If these move when the
    button is pressed, the setting is real even when no register shows it.
    """
    from hydroc.hardware import find_hwmon
    h = find_hwmon()
    if not h:
        return {}
    out = {}
    for f in FAN_FILES:
        try:
            with open(os.path.join(h, f)) as fh:
                out[f] = int(fh.read().strip())
        except (OSError, ValueError):
            out[f] = None
    return out


def read_block(ec) -> dict:
    out = {}
    for lo, hi in SCAN_RANGES:
        for addr in range(lo, hi + 1):
            try:
                out[addr] = ec.read(addr)
            except ECUnavailable:
                out[addr] = None
    return out


def scan_mode(ec, label: str) -> int:
    print(f"scanning 0x0700-0x07FF and 0x0400-0x04FF  [BIOS button mode: {label}]\n")

    print("calibrating noise (temperatures and fan counters move on their own)...")
    passes = []
    for i in range(4):
        passes.append(read_block(ec))
        print(f"  pass {i + 1}/4")
        time.sleep(1.0)

    noisy = {a for a in passes[0]
             if len({p.get(a) for p in passes}) > 1}
    baseline = passes[-1]
    print(f"  {len(noisy)} address(es) change on their own -- these will be ignored\n")

    fans_before = fan_snapshot()
    print("fans before:", fans_before or "hwmon unavailable")

    print("\nPress the profile button ONCE now, then press Enter here.")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        return 0

    after = read_block(ec)
    fans_after = fan_snapshot()

    changed = {a: (baseline[a], after[a]) for a in baseline
               if a not in noisy and baseline.get(a) != after.get(a)
               and baseline.get(a) is not None and after.get(a) is not None}

    print(f"\n=== paste this back ===  [scan, BIOS button mode: {label}]")
    print("fans before:", fans_before or "n/a")
    print("fans after: ", fans_after or "n/a")
    moved = [k for k in fans_before
             if fans_before.get(k) is not None and fans_after.get(k) is not None
             and fans_before[k] != fans_after[k]]
    print("fan values that moved:", ", ".join(moved) if moved else "none")

    if changed:
        print(f"\n{len(changed)} register(s) changed:")
        for a in sorted(changed):
            b, n = changed[a]
            print(f"  0x{a:04X}: 0x{b:02X} -> 0x{n:02X}   {bits(b)} -> {bits(n)}"
                  f"   bits: {bits(b ^ n)}")
    else:
        print("\nNo stable register changed anywhere in either window.")
        if moved:
            print("But the fans DID move -- so the button works and the EC simply")
            print("does not expose this setting through ECRR at all.")
        else:
            print("The fans did not move either. Either the press was not registered,")
            print("or this BIOS position genuinely does nothing observable.")
    return 0


def main() -> int:
    if os.geteuid() != 0:
        print("needs root for /proc/acpi/call: sudo python3 profile_button_probe.py")
        return 1

    label = "unlabelled"
    if "--label" in sys.argv:
        i = sys.argv.index("--label")
        if i + 1 < len(sys.argv):
            label = sys.argv[i + 1]
    if label == "unlabelled":
        print("note: pass --label fan-profile or --label performance-mode so this")
        print("      run can be told apart from the other BIOS position.\n")

    ec = EC()
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    if "--scan" in sys.argv:
        return scan_mode(ec, label)

    try:
        base = {r: ec.read(r) for r in WATCH}
    except ECUnavailable as e:
        print(f"EC read failed: {e}")
        return 1

    print("baseline:")
    for r in WATCH:
        print(f"  0x{r:04X} = 0x{base[r]:02X}  {bits(base[r])}")
    print("\nPress the profile button and cycle through EVERY mode, pausing on each,")
    print("then keep going until it wraps back to where it started.")
    print("Note the LED colour beside the power button at each step -- that is the")
    print("only way to label the values Office / Balanced / Beast correctly rather")
    print("than assuming the order.")
    print("Ctrl-C when you have been all the way round.\n")

    seen: dict[int, list[int]] = {r: [base[r]] for r in WATCH}
    try:
        while True:
            time.sleep(POLL)
            try:
                now = {r: ec.read(r) for r in WATCH}
            except ECUnavailable:
                continue                     # a garbled reply is not fatal here
            for r in WATCH:
                if now[r] != seen[r][-1]:
                    prev = seen[r][-1]
                    seen[r].append(now[r])
                    changed = prev ^ now[r]
                    note = decode(r, changed)
                    print(f"  0x{r:04X}: 0x{prev:02X} -> 0x{now[r]:02X}   "
                          f"{bits(prev)} -> {bits(now[r])}   "
                          f"bits changed: {bits(changed)}"
                          + (f"   [{note}]" if note else ""))
    except KeyboardInterrupt:
        pass

    print(f"\n=== paste this back ===  [BIOS button mode: {label}]")
    quiet = []
    for r in WATCH:
        values = seen[r]
        if len(values) == 1:
            quiet.append(f"0x{r:04X}")
            continue
        seq = " -> ".join(f"0x{v:02X}" for v in values)
        distinct = sorted({v for v in values})
        print(f"  0x{r:04X}  {seq}")
        print(f"          distinct values: {[f'0x{v:02X}' for v in distinct]}")
    if quiet:
        print(f"  unchanged: {', '.join(quiet)}")
    if all(len(v) == 1 for v in seen.values()):
        print("\n  Nothing moved. Either the button does not touch these registers,")
        print("  or the mode lives in the 0x04xx live-readback window instead.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
