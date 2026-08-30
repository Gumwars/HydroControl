#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_split_tables_probe.py — does SPLIT_TABLES give the GPU fan its own curve?

Universal fan control works (DESIGN §3.6), but in that run the GPU sat at a
constant 32 C while pwm2 tracked pwm1 step for step: fan 2 followed the CPU
table. 0x07C5 bit 7 (SPLIT_TABLES) was clear. So either the fans are ganged, or
that bit is what separates them -- and a UI with separate CPU and GPU curves is
only honest if it is the latter.

The discriminator is to make the two tables command OBVIOUSLY different duties at
the temperatures the machine actually has right now:

    CPU table  -> 40% at any temperature above 25 C
    GPU table  -> 75% at any temperature above 25 C

Both are flat, so neither depends on where the temperature happens to sit.

    split works    ->  pwm1 settles near 40%, pwm2 near 75%   (they DIVERGE)
    split does not ->  pwm1 and pwm2 both settle near 40%     (CPU table drives both)

Nothing here depends on a threshold being crossed, which is what made the last
run's reading unambiguous and is worth repeating.

SAFETY. Both curves are well above the stock idle duty, so the failure direction
is loud rather than hot. Every one of the 96 table bytes is snapshotted and
restored, as are 0x07C5 and 0x07C6. Aborts on 0 rpm or temperature. Never touches
0x0751 (FAN_MODE_USER is a one-way door). Fan speeds come from hwmon, never the
tacho registers.

    sudo python3 fan_split_tables_probe.py
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
REG_UNIVERSAL_FAN_CTRL = 0x07C5
REG_AP_OEM_6 = 0x07C6
SPLIT_TABLES = 0x80
ENABLE_UNIVERSAL = 0x04

TABLES = {                       # base -> label
    0x0F00: "cpu_down_t", 0x0F10: "cpu_up_t", 0x0F20: "cpu_duty",
    0x0F30: "gpu_down_t", 0x0F40: "gpu_up_t", 0x0F50: "gpu_duty",
}
LEN = 16
CPU_PCT, GPU_PCT = 40, 75        # deliberately far apart


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


def hwmon_to_pct(p):
    return round(p * 200 / 255) / 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=20.0)
    ap.add_argument("--abort-temp", type=int, default=85)
    args = ap.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    ok, why = EC.available()
    if not ok:
        raise SystemExit(f"EC unavailable: {why}")
    ec = EC()

    ap_oem, ufc = ec.read(REG_AP_OEM), ec.read(REG_UNIVERSAL_FAN_CTRL)
    oem6, manual = ec.read(REG_AP_OEM_6), ec.read(REG_MANUAL_FAN_CTRL)
    h0 = hwmon()
    if not ap_oem & 1:
        raise SystemExit("0x0741 bit 0 CLEAR -- result would be void (§4.1)")
    if oem6 & ENABLE_UNIVERSAL:
        raise SystemExit("universal fan control already enabled; refusing")
    if manual & 0x80:
        raise SystemExit("FAN_MODE_USER armed -- power cycle first")
    if h0 is None or (not h0["fan1"] and not h0["fan2"]):
        raise SystemExit("no hwmon, or both fans already at 0 rpm")

    run = {"started": datetime.now().isoformat(timespec="seconds"),
           "pre": {"0x07C5": ufc, "0x07C6": oem6, **h0},
           "cpu_pct": CPU_PCT, "gpu_pct": GPU_PCT, "samples": []}
    print(f"before:  cpu {h0['cpu']}C gpu {h0['gpu']}C  "
          f"pwm {h0['pwm1']}/{h0['pwm2']} ({hwmon_to_pct(h0['pwm1'])}%)")
    print(f"writing flat tables: CPU {CPU_PCT}%, GPU {GPU_PCT}%\n")

    baseline = {b: [ec.read(b + i) for i in range(LEN)] for b in TABLES}

    def write_tables():
        for base, label in TABLES.items():
            for i in range(LEN):
                if label.endswith("duty"):
                    v = (CPU_PCT if label.startswith("cpu") else GPU_PCT) * 2
                elif label.endswith("up_t"):
                    v = 25          # satisfied at any realistic temperature
                else:
                    v = 20          # DownT below UpT
                ec.write_verify(base + i, v)

    def restore():
        for base, vals in baseline.items():
            for i, v in enumerate(vals):
                try:
                    ec.write_verify(base + i, v)
                except (ECWriteRejected, ECUnavailable):
                    pass

    aborted = None
    try:
        write_tables()
        ec.write_verify(REG_UNIVERSAL_FAN_CTRL, ufc | SPLIT_TABLES)
        got_split = ec.read(REG_UNIVERSAL_FAN_CTRL)
        run["split_bit_took"] = bool(got_split & SPLIT_TABLES)
        print(f"0x07C5 = 0x{got_split:02X}  SPLIT_TABLES "
              f"{'SET' if got_split & SPLIT_TABLES else 'WOULD NOT TAKE'}")
        if not got_split & SPLIT_TABLES:
            print("  the bit itself does not hold -- nothing further to test")
        else:
            ec.write_verify(REG_AP_OEM_6, oem6 | ENABLE_UNIVERSAL)
            print(f"enabled universal control, observing {args.hold:g}s ...\n")
            t0 = time.monotonic()
            while time.monotonic() - t0 < args.hold:
                h = hwmon()
                if h:
                    h["t"] = round(time.monotonic() - t0, 1)
                    run["samples"].append(h)
                    print(f"  t+{h['t']:>5}s  cpu {h['cpu']:>3}C gpu {h['gpu']:>3}C  "
                          f"pwm1 {h['pwm1']:>3} ({hwmon_to_pct(h['pwm1']):>5}%)  "
                          f"pwm2 {h['pwm2']:>3} ({hwmon_to_pct(h['pwm2']):>5}%)",
                          flush=True)
                    if h["fan1"] == 0 and h["fan2"] == 0:
                        aborted = "BOTH FANS 0 RPM"
                        break
                    if h["cpu"] >= args.abort_temp:
                        aborted = f"CPU {h['cpu']}C"
                        break
                time.sleep(2.0)
    except KeyboardInterrupt:
        aborted = "interrupted"
    except (ECWriteRejected, ECUnavailable) as e:
        aborted = f"EC error: {e}"
    finally:
        print(f"\nrestoring ...{'  (ABORT: ' + aborted + ')' if aborted else ''}")
        for reg, val in ((REG_AP_OEM_6, oem6), (REG_UNIVERSAL_FAN_CTRL, ufc)):
            try:
                ec.write_verify(reg, val)
            except (ECWriteRejected, ECUnavailable):
                pass
        restore()
        time.sleep(2.0)
        h1 = hwmon()
        run["post"] = {"0x07C5": ec.read(REG_UNIVERSAL_FAN_CTRL),
                       "0x07C6": ec.read(REG_AP_OEM_6),
                       "aborted": aborted, **(h1 or {})}
        tables_ok = all(ec.read(b + i) == baseline[b][i]
                        for b in TABLES for i in (0, LEN - 1))
        run["post"]["tables_restored_spotcheck"] = tables_ok
        print(f"  0x07C5=0x{run['post']['0x07C5']:02X} "
              f"0x07C6=0x{run['post']['0x07C6']:02X}  "
              f"tables restored: {tables_ok}")
        if h1:
            print(f"after:   pwm {h1['pwm1']}/{h1['pwm2']}  "
                  f"fans {h1['fan1']}/{h1['fan2']} rpm")
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           f"fan-split-{datetime.now():%Y%m%d-%H%M%S}.json")
        json.dump(run, open(out, "w"), indent=2)
        print(f"\nartifact: {out}")

    print("\nInterpretation:")
    if not run.get("split_bit_took"):
        print("  SPLIT_TABLES would not hold. One curve for both fans.")
    elif not run["samples"]:
        print("  no samples.")
    else:
        last = run["samples"][-3:]
        p1 = sum(hwmon_to_pct(s["pwm1"]) for s in last) / len(last)
        p2 = sum(hwmon_to_pct(s["pwm2"]) for s in last) / len(last)
        print(f"  settled at pwm1 {p1:.1f}%  pwm2 {p2:.1f}%  "
              f"(wrote CPU {CPU_PCT}%, GPU {GPU_PCT}%)")
        if abs(p1 - p2) >= 10:
            print("  The fans DIVERGED -- SPLIT_TABLES gives the GPU fan its own")
            print("  table. Separate CPU and GPU curves in the UI are real.")
        else:
            print("  The fans did NOT diverge; both followed one table. Ship a")
            print("  single curve and say plainly that it drives both fans.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
