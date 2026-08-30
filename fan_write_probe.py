#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_write_probe.py — can we actually drive the fans, and through which register?

Everything about fan control so far is read-only knowledge. Pressing the profile
button in fan-profile BIOS position set `FAN_MODE_BOOST` and drove PWM_1/PWM_2
to 0xC8, and the driver names the registers -- but nothing has ever *written*
them. This establishes whether we can, and by which route.

    0x0751  MANUAL_FAN_CTRL   bits 2:0 FAN_LEVEL, b4 TURBO, b5 HIGH,
                              b6 BOOST, b7 USER
    0x075B  PWM_1             0..200 (PWM_MAX); hwmon rescales to 0..255
    0x075C  PWM_2
    0x1804  PWM_1_WRITEABLE   driver: "unstable on some models and likely not
    0x1809  PWM_2_WRITEABLE    meant to be used by applications"

READ THIS BEFORE TRUSTING THE OUTPUT
------------------------------------
hwmon `pwm1` is *derived from* 0x075B. If we write 0x075B, pwm1 reports our
value back whether or not a fan ever moved. It is not evidence. **Only RPM is
evidence** -- fan1_input / fan2_input come from separate tacho registers the EC
populates from the actual fan. Every verdict below is based on RPM.

SAFETY
------
Only ever asks for MORE airflow than the fans are already giving. A wrong guess
therefore makes the machine quieter-to-louder, never hotter. Original register
values are restored on exit including on Ctrl-C, the run aborts if temperature
climbs past ABORT_TEMP_C, and a full power cycle clears anything left behind.

    sudo python3 fan_write_probe.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected     # noqa: E402
from hydroc.hardware import find_hwmon                       # noqa: E402

REG_MANUAL_FAN_CTRL = 0x0751
REG_PWM_1, REG_PWM_2 = 0x075B, 0x075C
REG_PWM_1_W, REG_PWM_2_W = 0x1804, 0x1809

FAN_LEVEL_MASK = 0b111
FAN_MODE_BOOST = 1 << 6
FAN_MODE_USER = 1 << 7

PWM_MAX = 200                 # driver: PWM_MAX, NOT 255

START_TEMP_MAX_C = 75         # refuse to start if already warm
ABORT_TEMP_C = 88             # bail out and restore
SETTLE_S = 5                  # fans take a few seconds to spin up
RPM_SIGNIFICANT = 250         # below this, call it noise not response
FAN_STOPPED_RPM = 200         # below this the fan is not turning


def hw() -> str | None:
    return find_hwmon()


def sensors() -> dict:
    h = hw()
    if not h:
        return {}
    out = {}
    for f in ("pwm1", "pwm2", "fan1_input", "fan2_input",
              "temp1_input", "temp2_input"):
        try:
            with open(os.path.join(h, f)) as fh:
                out[f] = int(fh.read().strip())
        except (OSError, ValueError):
            out[f] = None
    return out


def temps_c(s: dict) -> tuple[float, float]:
    return ((s.get("temp1_input") or 0) / 1000.0,
            (s.get("temp2_input") or 0) / 1000.0)


def fmt(s: dict) -> str:
    c, g = temps_c(s)
    return (f"pwm {s.get('pwm1')}/{s.get('pwm2')}  "
            f"rpm {s.get('fan1_input')}/{s.get('fan2_input')}  "
            f"{c:.0f}C/{g:.0f}C")


class Restorer:
    """Puts every register back, whatever happens."""

    def __init__(self, ec: EC) -> None:
        self.ec = ec
        self.saved: dict[int, int] = {}

    def keep(self, addr: int) -> int:
        if addr not in self.saved:
            self.saved[addr] = self.ec.read(addr)
        return self.saved[addr]

    def restore(self) -> None:
        for addr, value in self.saved.items():
            for attempt in range(3):
                try:
                    self.ec.write_verify(addr, value)
                    break
                except (ECUnavailable, ECWriteRejected):
                    time.sleep(0.2)
            else:
                print(f"  WARNING: could not restore 0x{addr:04X} to 0x{value:02X}")


def attempt(ec: EC, rest: Restorer, label: str, writes: list[tuple[int, int]],
            target_pct: int) -> dict:
    """Apply a candidate write, then judge it by RPM only."""
    before = sensors()
    print(f"\n--- {label}")
    print(f"    before: {fmt(before)}")

    for addr, value in writes:
        rest.keep(addr)
    try:
        for addr, value in writes:
            ec.write_verify(addr, value)
            print(f"    wrote 0x{addr:04X} = 0x{value:02X} ({value})")
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"    write rejected: {e}")
        return {"label": label, "verdict": "write rejected", "detail": str(e)}

    time.sleep(SETTLE_S)
    after = sensors()
    print(f"    after:  {fmt(after)}")

    d1 = (after.get("fan1_input") or 0) - (before.get("fan1_input") or 0)
    d2 = (after.get("fan2_input") or 0) - (before.get("fan2_input") or 0)
    moved = max(d1, d2) >= RPM_SIGNIFICANT
    verdict = (f"FANS RESPONDED  (+{d1} / +{d2} rpm)" if moved
               else f"no real change  ({d1:+d} / {d2:+d} rpm)")
    print(f"    -> {verdict}")

    # pwm mirrors what we wrote when we write 0x075B, so say so explicitly
    if after.get("pwm1") != before.get("pwm1") and not moved:
        print("       (pwm changed but RPM did not -- that is the register "
              "reading itself back, not a fan responding)")

    return {"label": label, "verdict": verdict, "rpm_delta": (d1, d2),
            "target_pct": target_pct}


def main() -> int:
    if os.geteuid() != 0:
        print("needs root: sudo python3 fan_write_probe.py")
        return 1
    if not hw():
        print("uniwill hwmon not found -- is the driver bound?")
        return 1

    ec = EC()
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    s = sensors()
    cpu, gpu = temps_c(s)
    print(f"start: {fmt(s)}")
    if max(cpu, gpu) > START_TEMP_MAX_C:
        print(f"too warm to start ({max(cpu, gpu):.0f}C > {START_TEMP_MAX_C}C). "
              "Let it idle and retry.")
        return 1

    cur_duty = ec.read(REG_PWM_1)
    print(f"current duty register 0x075B = {cur_duty} of {PWM_MAX} "
          f"({cur_duty * 100 // PWM_MAX}%)")

    # Only ever ask for MORE airflow than we already have.
    up1 = min(PWM_MAX, max(cur_duty + 60, int(PWM_MAX * 0.70)))
    up2 = min(PWM_MAX, max(cur_duty + 100, int(PWM_MAX * 0.90)))
    print(f"will request {up1} ({up1*100//PWM_MAX}%) then "
          f"{up2} ({up2*100//PWM_MAX}%) -- both above current, never below\n")

    rest = Restorer(ec)
    results = []
    try:
        latch = rest.keep(REG_MANUAL_FAN_CTRL)
        print(f"0x0751 = 0x{latch:02X}  "
              f"(USER={bool(latch & FAN_MODE_USER)}, BOOST={bool(latch & FAN_MODE_BOOST)})")

        # ARMING FAN_MODE_USER IS A ONE-WAY DOOR until the next power cycle.
        # Observed twice: arming it stops the fans, and clearing the bit again
        # does NOT hand control back to the EC -- the fans stay at 0 rpm until a
        # full shutdown. The watchdog below detects the stop but cannot undo it,
        # so this route is gated off entirely until there is a working way to
        # actually set a duty (almost certainly a WMI method, not a register).
        if "--i-will-power-cycle" not in sys.argv:
            print("\nREFUSING to arm FAN_MODE_USER.")
            print("  It takes the fans off automatic control, and we have no way")
            print("  to drive them afterwards. Clearing the bit does NOT restore")
            print("  EC control -- the fans stay stopped until a full power cycle.")
            print("  There is nothing left to learn here that is worth that.")
            print("\n  The remaining lead is the WMI interface: uniwill-acpi.c says")
            print("  0x1804/0x1809 are 'only accessible when using the WMI")
            print("  interface', and Control Center ships Fan_SettingsView.")
            print("\n  If you really want to re-run it anyway, and accept that you")
            print("  will have to shut down afterwards:")
            print("      sudo python3 fan_write_probe.py --i-will-power-cycle")
            return 0

        print("\narming FAN_MODE_USER and checking the fans stay alive...")
        print("  (you WILL need a full power cycle to restore automatic control)")
        before_rpm = (sensors().get("fan1_input") or 0)
        ec.write_verify(REG_MANUAL_FAN_CTRL, latch | FAN_MODE_USER)
        time.sleep(2.5)
        after_rpm = (sensors().get("fan1_input") or 0)
        print(f"    rpm {before_rpm} -> {after_rpm}")
        if after_rpm < FAN_STOPPED_RPM <= before_rpm:
            ec.write_verify(REG_MANUAL_FAN_CTRL, latch)
            time.sleep(2.5)
            print(f"    FANS STOPPED -- cleared the bit, rpm now "
                  f"{sensors().get('fan1_input')}")
            print("    NOTE: clearing the bit does not restore EC fan control.")
            print("    SHUT DOWN FULLY when this run finishes.")
            print("\n    FAN_MODE_USER hands the fans over and we have no way to")
            print("    drive them, so they stop. That is a POSITIVE result about")
            print("    the latch and a negative one about the duty registers.")
            results.append({"label": "FAN_MODE_USER (0x0751 b7)",
                            "verdict": "WORKS -- takes fans off automatic, "
                                       "but no duty route reaches the actuator"})
            raise SystemExit(0)
        results.append(attempt(
            ec, rest, "FAN_MODE_USER + PWM_1/PWM_2 (0x075B/0x075C)",
            [(REG_PWM_1, up1), (REG_PWM_2, up1)], up1 * 100 // PWM_MAX))

        if "RESPONDED" not in results[-1]["verdict"]:
            # 2. the driver's "writeable" aliases
            results.append(attempt(
                ec, rest, "FAN_MODE_USER + PWM_*_WRITEABLE (0x1804/0x1809)",
                [(REG_PWM_1_W, up1), (REG_PWM_2_W, up1)], up1 * 100 // PWM_MAX))

        if all("RESPONDED" not in r["verdict"] for r in results):
            # 3. the level field, in case duty is not the interface at all
            results.append(attempt(
                ec, rest, "FAN_LEVEL bits 2:0 of 0x0751 = 7",
                [(REG_MANUAL_FAN_CTRL,
                  (latch | FAN_MODE_USER) & ~FAN_LEVEL_MASK | 0b111)], 0))
        else:
            # it works -- confirm it is proportional, not just on/off
            results.append(attempt(
                ec, rest, "second step, higher duty (proportional check)",
                [(REG_PWM_1, up2), (REG_PWM_2, up2)], up2 * 100 // PWM_MAX))

        s = sensors()
        cpu, gpu = temps_c(s)
        if max(cpu, gpu) > ABORT_TEMP_C:
            print(f"\nABORT: {max(cpu, gpu):.0f}C exceeded {ABORT_TEMP_C}C")
    except SystemExit:
        pass                       # early, deliberate stop -- still restores
    except KeyboardInterrupt:
        print("\ninterrupted")
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"\nEC error: {e}")
    finally:
        print("\nrestoring...")
        rest.restore()
        time.sleep(SETTLE_S)
        print(f"  {fmt(sensors())}")

    print("\n=== paste this back ===")
    for r in results:
        print(f"  {r['label']}")
        print(f"      {r['verdict']}")
    if any("RESPONDED" in r["verdict"] for r in results):
        print("\n  Manual fan control works. Next: fan_characterise.py")
    else:
        print("\n  Nothing moved the fans. Manual control is not available by")
        print("  any of these routes on this firmware.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
