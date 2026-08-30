#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
perfmode_write_probe.py — drive the performance mode ourselves and watch the LED.

The button is only an indication: it raises WMI 0xB0, uniwill-laptop turns that
into KEY_F14, and nothing else happens because the OEM service is what applies
the mode. Confirmed -- four presses arrived as key events while every EC
register stood still and the LED stayed blue.

So the mode is something the HOST writes. The best candidate is `EC_ADDR_OEM_3`
(`0x07A5`), which uniwill-acpi.c defines and then never uses:

    bits 1:0   POWER_LED_MASK   LEFT=0, BOTH=1, NONE=2   (which LED, not colour)
    bit  2     FAN_QUIET
    bit  4     OVERBOOST
    bit  7     HIGH_POWER

Three behaviour bits for three modes, plus Custom already accounted for by the
`0x0727` bit 6 TDP latch. That is a composite encoding across two registers --
the same shape as the trigger/status pairs elsewhere in this EC.

This writes each candidate combination and asks what the LED does. Every write
is masked, so POWER_LED_MASK and any unknown bits are preserved, and the
original value is restored on exit including on Ctrl-C.

    sudo python3 perfmode_write_probe.py

Safety: these are volatile 0x07xx settings -- a full power cycle restores
factory defaults, and nothing here can be permanently wedged. FAN_QUIET may
reduce fan speed, so do not run this under load.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import (EC, ECUnavailable, ECWriteRejected,        # noqa: E402
                       REG_PL1_LIVE, REG_PL2_LIVE, REG_PL3_LIVE, REG_PL4_LIVE,
                       REG_PL1_SETTING, REG_PL2_SETTING, REG_PL4_SETTING,
                       REG_TURBO)   # noqa

REG_OEM_3 = 0x07A5
REG_OEM_4 = 0x07A6
REG_CUSTOM_PROFILE = 0x0727

POWER_LED_MASK = 0b11
FAN_QUIET = 1 << 2
OVERBOOST = 1 << 4
HIGH_POWER = 1 << 7
MODE_MASK = FAN_QUIET | OVERBOOST | HIGH_POWER          # 0x94

CANDIDATES = [
    ("all mode bits clear", 0),
    ("FAN_QUIET", FAN_QUIET),
    ("OVERBOOST", OVERBOOST),
    ("HIGH_POWER", HIGH_POWER),
    ("OVERBOOST + HIGH_POWER", OVERBOOST | HIGH_POWER),
    ("FAN_QUIET + HIGH_POWER", FAN_QUIET | HIGH_POWER),
]

SETTLE = 1.5


def bits(v: int) -> str:
    return format(v, "08b")


def fans() -> str:
    from hydroc.hardware import find_hwmon
    h = find_hwmon()
    if not h:
        return "n/a"
    out = []
    for f in ("pwm1", "fan1_input"):
        try:
            with open(os.path.join(h, f)) as fh:
                out.append(f"{f}={fh.read().strip()}")
        except OSError:
            pass
    return " ".join(out) or "n/a"


def limits(ec) -> str:
    """What the EC is actually enforcing, from the ECXP live window.

    The 0x078x SETTING registers hold what the *user* asked for, where 0 means
    "firmware default" -- so a mode that changes the meaning of the default
    leaves them at 0 and looks like nothing happened. The 0x046x live mirror is
    the enforced envelope. Same trap as reading RAPL's limit fields, one level
    down.
    """
    try:
        live = [ec.read(r) for r in (REG_PL1_LIVE, REG_PL2_LIVE,
                                     REG_PL3_LIVE, REG_PL4_LIVE)]
        setp = [ec.read(r) for r in (REG_PL1_SETTING, REG_PL2_SETTING,
                                     REG_PL4_SETTING)]
        turbo = ec.read(REG_TURBO) & 1
        return ("live " + "/".join(str(v) for v in live)
                + "  set " + "/".join(str(v) for v in setp)
                + f"  turbo {turbo}")
    except ECUnavailable as e:
        return f"error: {e}"


def ask(prompt: str) -> str:
    try:
        return input(f"    {prompt} ").strip()
    except (EOFError, KeyboardInterrupt):
        raise KeyboardInterrupt


def control_test(ec) -> int:
    """Does the live window respond to anything at all?

    A null result is only meaningful from an instrument with a needle. The
    custom-profile latch plus a PL1 setpoint is a change we know works --
    verified to the watt against the RAPL energy counter. If live PL1 tracks
    it, the live registers are a valid readout and the 0x07A5 null stands. If
    live PL1 never moves, the readout is a constant and proves nothing.
    """
    from hydroc.ec import BIT_CUSTOM_PROFILE

    latch0 = ec.read(REG_CUSTOM_PROFILE)
    pl1_0 = ec.read(REG_PL1_SETTING)
    print(f"before: latch 0x{latch0:02X}, PL1 setpoint {pl1_0}, {limits(ec)}\n")

    try:
        print("arming custom-profile latch and setting PL1 = 45 W ...")
        ec.write_verify(REG_CUSTOM_PROFILE, latch0 | BIT_CUSTOM_PROFILE)
        ec.write_verify(REG_PL1_SETTING, 45)
        time.sleep(1.0)
        print(f"  {limits(ec)}")

        print("now PL1 = 90 W ...")
        ec.write_verify(REG_PL1_SETTING, 90)
        time.sleep(1.0)
        print(f"  {limits(ec)}")

        # Confirm the PL4 live scale. If live PL4 is half-scale like the setting
        # register, a 200 W request (stored as 100) should read 100 raw.
        pl4_0 = ec.read(REG_PL4_SETTING)
        print("\nPL4 scale check: requesting 200 W (stored as 100 if half-scale) ...")
        ec.write_verify(REG_PL4_SETTING, 100)
        time.sleep(1.0)
        raw = ec.read(REG_PL4_LIVE)
        print(f"  live PL4 raw = {raw}")
        print(f"  -> {'HALF-scale confirmed (raw 100 = 200 W)' if raw == 100 else 'NOT half-scale -- raw ' + str(raw)}")
        ec.write_verify(REG_PL4_SETTING, pl4_0)
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"  control write failed: {e}")
    finally:
        try:
            ec.write_verify(REG_PL1_SETTING, pl1_0)
            ec.write_verify(REG_CUSTOM_PROFILE, latch0)
            print(f"\nrestored: latch 0x{latch0:02X}, PL1 setpoint {pl1_0}")
            print(f"  {limits(ec)}")
        except Exception as e:
            print(f"\nCOULD NOT RESTORE ({e}) -- power cycle to clear")

    print("\n=== paste this back ===")
    print("  If live PL1 tracked 45 then 90, the live window is a real readout")
    print("  and 0x07A5 genuinely does nothing. If it stayed 75, the readout is")
    print("  inert and the earlier null proves nothing either way.")
    return 0


def main() -> int:
    if os.geteuid() != 0:
        print("needs root: sudo python3 perfmode_write_probe.py")
        return 1

    ec = EC()
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    if "--control" in sys.argv:
        return control_test(ec)

    original = ec.read(REG_OEM_3)
    oem4 = ec.read(REG_OEM_4)
    latch = ec.read(REG_CUSTOM_PROFILE)

    print(f"0x07A5 (OEM_3)   = 0x{original:02X}  {bits(original)}")
    print(f"     POWER_LED   = {original & POWER_LED_MASK}")
    print(f"     FAN_QUIET   = {bool(original & FAN_QUIET)}")
    print(f"     OVERBOOST   = {bool(original & OVERBOOST)}")
    print(f"     HIGH_POWER  = {bool(original & HIGH_POWER)}")
    print(f"0x07A6 (OEM_4)   = 0x{oem4:02X}  {bits(oem4)}")
    print(f"0x0727           = 0x{latch:02X}  {bits(latch)}")
    print(f"     custom-profile latch (bit 6) = {bool(latch & (1 << 6))}")
    print(f"fans: {fans()}\n")

    # The docs claim the profile LED goes white when the TDP latch is armed. If
    # the latch is on and the LED is blue, that claim is wrong and worth knowing
    # before we trust the LED as a readout of anything.
    if latch & (1 << 6):
        print("NOTE: the custom-profile latch is ARMED. DESIGN.md says the LED should")
        print("      be WHITE. If it is blue, that claim is wrong.\n")

    results = []
    try:
        print(f"limits: {limits(ec)}")
        led0 = ask("What colour is the profile LED right now?")
        results.append(("baseline", f"0x{original:02X}", led0,
                        fans() + "  " + limits(ec)))

        for label, value in CANDIDATES:
            target = (original & ~MODE_MASK) | value
            print(f"\n--- {label}   -> 0x07A5 = 0x{target:02X}  {bits(target)}")
            try:
                ec.write_verify(REG_OEM_3, target)
            except (ECUnavailable, ECWriteRejected) as e:
                # The readback matters more than the rejection: what the EC put
                # there instead tells us what it substituted, and that is the
                # shape of the rule it is enforcing.
                print(f"    write rejected: {e}")
                detail = str(e).split(": ", 1)[-1]
                results.append((label, f"0x{target:02X}", f"REJECTED ({detail})", ""))
                continue
            time.sleep(SETTLE)
            lim = limits(ec)
            print(f"    limits: {lim}")
            led = ask("LED colour now? (blue/green/purple/white/unchanged)")
            f = fans()
            print(f"    fans: {f}")
            results.append((label, f"0x{target:02X}", led, f + "  " + lim))
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        try:
            ec.write_verify(REG_OEM_3, original)
            print(f"\nrestored 0x07A5 = 0x{original:02X}")
        except Exception as e:
            print(f"\nCOULD NOT RESTORE 0x07A5 ({e}) -- power cycle to clear")

    print("\n=== paste this back ===")
    width = max(len(r[0]) for r in results) if results else 10
    for label, val, led, f in results:
        print(f"  {label:<{width}}  {val}  LED: {led or '?'}   {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
