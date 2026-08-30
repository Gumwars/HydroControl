#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_universal_enable_probe.py — does ENABLE_UNIVERSAL_FAN_CTRL make the EC use
our tables? And is it reversible?

This is the experiment DESIGN.md §8.1 has been waiting on. The previous run is
void: it happened while 0x0741 bit 0 was clear, so the EC ignored everything.

Established first, so this probe asks exactly one question:

  * 0x0F00-0x0F5F is mapped and was empty; all six tables take ECRW writes and
    hold them (fan_table_write_probe.py)
  * a full curve is written and verified, and the fans did NOT change while
    0x07C6 bit 2 stayed clear -- so the tables are genuinely inert until the
    feature is enabled
  * the written curve commands a HIGHER duty than stock at every temperature and
    never commands zero

REVERSIBILITY IS UNKNOWN. FAN_MODE_USER (0x0751 bit 7) is a one-way door:
arming it stops the fans and clearing it does not hand control back -- only a
power cycle does. Nobody has established whether 0x07C6 bit 2 behaves the same
way. This probe therefore treats it as one-way, measures whether clearing it
actually restores firmware control, and records the answer.

The curve being aggressive is what makes that survivable: if the bit sticks, the
machine is stuck LOUD, not stuck hot. A power cycle clears it either way --
everything here is volatile EC RAM and no code path in this project writes flash.

Safety: aborts and clears the bit immediately if either fan reads 0 rpm, or if
the CPU passes --abort-temp. Fan speeds come from hwmon, never from the tacho
registers through ECRR (§4.2).

    sudo python3 fan_universal_enable_probe.py
    sudo python3 fan_universal_enable_probe.py --hold 60
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected      # noqa: E402

REG_AP_OEM = 0x0741
REG_MANUAL_FAN_CTRL = 0x0751
REG_PWM_1, REG_PWM_2 = 0x075B, 0x075C
REG_UNIVERSAL_FAN_CTRL = 0x07C5
REG_AP_OEM_6 = 0x07C6
ENABLE_UNIVERSAL = 0x04

CURVE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fan-curve-hydroc16.json")


def hwmon():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if open(os.path.join(d, "name")).read().strip() != "uniwill":
                continue
            g = lambda f: int(open(os.path.join(d, f)).read().strip())  # noqa: E731
            return {"fan1": g("fan1_input"), "fan2": g("fan2_input"),
                    "pwm1": g("pwm1"), "pwm2": g("pwm2"),
                    "cpu": g("temp1_input") // 1000,
                    "gpu": g("temp2_input") // 1000}
        except OSError:
            continue
    return None


def expected_duty(cpu_c):
    try:
        pts = json.load(open(CURVE))["CPU"]
    except OSError:
        return None
    duty = 0
    for p in pts:
        if cpu_c >= p["UpT"]:
            duty = p["Duty"]
    return duty


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=30.0,
                    help="seconds to observe with the feature enabled")
    ap.add_argument("--abort-temp", type=int, default=85)
    ap.add_argument("--start-max-temp", type=int, default=70,
                    help="refuse to start above this")
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    ok, why = EC.available()
    if not ok:
        raise SystemExit(f"EC unavailable: {why}")

    ec = EC()
    run = {"started": datetime.now().isoformat(timespec="seconds"),
           "hold_s": args.hold, "samples": []}

    ap_oem = ec.read(REG_AP_OEM)
    oem6 = ec.read(REG_AP_OEM_6)
    manual = ec.read(REG_MANUAL_FAN_CTRL)
    h0 = hwmon()

    if not ap_oem & 1:
        raise SystemExit("0x0741 bit 0 CLEAR -- any result would be void (§4.1)")
    if oem6 & ENABLE_UNIVERSAL:
        raise SystemExit("ENABLE_UNIVERSAL_FAN_CTRL is already set; nothing to do")
    if manual & 0x80:
        raise SystemExit("FAN_MODE_USER armed -- power cycle before probing")
    if h0 is None:
        raise SystemExit("no uniwill hwmon -- refusing to run blind")
    if h0["cpu"] > args.start_max_temp:
        raise SystemExit(f"CPU {h0['cpu']}C is above --start-max-temp "
                         f"{args.start_max_temp}; let it cool first")
    if not h0["fan1"] and not h0["fan2"]:
        raise SystemExit("both fans read 0 rpm before we start -- fix that first")

    run["pre"] = {"0x0741": ap_oem, "0x07C6": oem6, "0x0751": manual, **h0}
    want = expected_duty(h0["cpu"])
    print(f"before:  cpu {h0['cpu']}C  fans {h0['fan1']}/{h0['fan2']} rpm  "
          f"pwm {h0['pwm1']}/{h0['pwm2']}")
    print(f"the written curve commands {want}% at {h0['cpu']}C; "
          f"stock is currently {round(h0['pwm1'] / 255 * 100)}%\n")

    print(f"setting 0x07C6 bit 2 (ENABLE_UNIVERSAL_FAN_CTRL) for {args.hold:g}s ...")
    aborted = None
    try:
        ec.write_verify(REG_AP_OEM_6, oem6 | ENABLE_UNIVERSAL)
    except (ECWriteRejected, ECUnavailable) as e:
        print(f"  the enable bit itself would not take: {e}")
        run["enable_rejected"] = str(e)
        json.dump(run, open(_artifact(), "w"), indent=2)
        return 1

    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < args.hold:
            h = hwmon()
            if h is None:
                continue
            h["t"] = round(time.monotonic() - t0, 1)
            run["samples"].append(h)
            print(f"  t+{h['t']:>5}s  cpu {h['cpu']:>3}C  gpu {h['gpu']:>3}C  "
                  f"fans {h['fan1']:>5}/{h['fan2']:>5}  pwm {h['pwm1']:>3}/{h['pwm2']:>3}",
                  flush=True)
            if h["fan1"] == 0 and h["fan2"] == 0:
                aborted = "BOTH FANS AT 0 RPM"
                break
            if h["cpu"] >= args.abort_temp:
                aborted = f"CPU reached {h['cpu']}C"
                break
            time.sleep(2.0)
    except KeyboardInterrupt:
        aborted = "interrupted"
    finally:
        # Always attempt to put it back, and record whether that worked --
        # that is the reversibility answer, and it is the part nobody knows.
        print(f"\nclearing 0x07C6 bit 2 ...{'  (ABORT: ' + aborted + ')' if aborted else ''}")
        try:
            ec.write_verify(REG_AP_OEM_6, oem6 & ~ENABLE_UNIVERSAL)
            cleared = True
        except (ECWriteRejected, ECUnavailable) as e:
            cleared = False
            print(f"  clear REJECTED: {e}")
        time.sleep(2.0)
        after = ec.read(REG_AP_OEM_6)
        h1 = hwmon()
        run["post"] = {"0x07C6": after, "cleared_ok": cleared,
                       "aborted": aborted, **(h1 or {})}
        print(f"  0x07C6 now 0x{after:02X}  "
              f"(bit 2 {'STILL SET' if after & ENABLE_UNIVERSAL else 'clear'})")
        if h1:
            print(f"after:   cpu {h1['cpu']}C  fans {h1['fan1']}/{h1['fan2']} rpm  "
                  f"pwm {h1['pwm1']}/{h1['pwm2']}")
        out = _artifact()
        json.dump(run, open(out, "w"), indent=2)
        print(f"\nartifact: {out}")

        if h1 and not h1["fan1"] and not h1["fan2"]:
            print("\n  !! FANS ARE STOPPED. Run:  sudo python3 fan_recover.py")
            print("     If that does not restore them, POWER CYCLE COMPLETELY --")
            print("     a shutdown, not a reboot. Everything here is volatile.")

    _verdict(run, h0)
    return 0


def _artifact():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"fan-universal-{datetime.now():%Y%m%d-%H%M%S}.json")


def _verdict(run, h0):
    print("\nInterpretation:")
    samples = run.get("samples", [])
    if not samples:
        print("  no samples taken.")
        return
    moved = any(s["pwm1"] != h0["pwm1"] for s in samples)
    if moved:
        print("  pwm1 CHANGED while the feature was enabled -- the EC acted on")
        print("  the tables. Universal fan control works, and a userspace fan")
        print("  curve is reachable from here.")
    else:
        print("  pwm1 never moved. The EC accepted the enable bit and went on")
        print("  running its own curve, with 0x0741 bit 0 confirmed set this")
        print("  time -- so unlike the previous run, this null is trustworthy.")
    if run.get("post", {}).get("0x07C6", 0) & ENABLE_UNIVERSAL:
        print("  The enable bit did NOT clear: treat it as one-way and power")
        print("  cycle to get back to firmware defaults.")
    else:
        print("  The enable bit cleared cleanly -- it is reversible.")


if __name__ == "__main__":
    sys.exit(main())
