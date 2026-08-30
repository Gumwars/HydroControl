#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Measure sustained CPU package power under load, to test whether the EC's
PL1L limit is actually enforced.

Why not just read RAPL's limit fields
-------------------------------------
constraint_*_power_limit_uw reports the MSR values (205 W here). The EC may
enforce its own envelope through power delivery / PROCHOT without touching
those MSRs -- in which case the limit is invisible except as actual watts
drawn under sustained load.

This samples /sys/class/powercap/intel-rapl:0/energy_uj, which is a real
energy counter, and differentiates it to get true package watts.

Method:
  1. baseline idle power for a few seconds
  2. saturate all cores
  3. report power at 5s (turbo/PL2 window) and sustained at the end (PL1)

Usage:
    python3 powerwatch.py              # 45s load
    python3 powerwatch.py -d 90        # longer, for slow PL1 windows
"""

import argparse
import multiprocessing as mp
import os
import time

RAPL = "/sys/class/powercap/intel-rapl:0"
ENERGY = f"{RAPL}/energy_uj"


def energy():
    with open(ENERGY) as fh:
        return int(fh.read())


def max_energy():
    try:
        with open(f"{RAPL}/max_energy_range_uj") as fh:
            return int(fh.read())
    except OSError:
        return 1 << 32


def power_over(seconds, wrap):
    """Average package watts over `seconds`."""
    e0, t0 = energy(), time.monotonic()
    time.sleep(seconds)
    e1, t1 = energy(), time.monotonic()
    de = e1 - e0
    if de < 0:
        de += wrap          # counter wrapped
    return de / 1e6 / (t1 - t0)


def burn(stop):
    """Fallback only. Python integer loops are a WEAK load -- they leave the
    vector units idle and will not approach the package power limit. stress-ng
    with a matrix/FP method is used when available."""
    x = 0
    while not stop.is_set():
        x = (x * x + 1) % 2147483647


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-d", "--duration", type=int, default=45,
                    help="load duration in seconds (default 45)")
    args = ap.parse_args()

    if not os.path.exists(ENERGY):
        raise SystemExit(f"{ENERGY} not readable -- run as root?")

    wrap = max_energy()
    ncpu = os.cpu_count() or 8

    print(f"idle baseline ({ncpu} cpus)...")
    idle = power_over(4, wrap)
    print(f"  idle: {idle:.1f} W\n")

    import shutil, subprocess
    sng = shutil.which("stress-ng")
    proc, stop, workers = None, None, []

    if sng:
        # matrixprod is FP/AVX-heavy -- far more power-dense than integer loops.
        print(f"load: stress-ng --cpu {ncpu} --cpu-method matrixprod")
        proc = subprocess.Popen(
            [sng, "--cpu", str(ncpu), "--cpu-method", "matrixprod",
             "--timeout", f"{args.duration + 15}s", "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print("load: python workers (WEAK -- install stress-ng for a valid test)")
        stop = mp.Event()
        workers = [mp.Process(target=burn, args=(stop,)) for _ in range(ncpu)]
        for w in workers:
            w.start()

    try:
        print(f"loading all cores for {args.duration}s...")
        time.sleep(2)                      # let it ramp
        burst = power_over(5, wrap)
        print(f"  burst  (first ~7s, PL2 window): {burst:.1f} W")

        mid = max(5, args.duration - 20)
        time.sleep(mid)
        sustained = power_over(10, wrap)
        print(f"  sustained (after {mid+7}s, PL1): {sustained:.1f} W")
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if stop:
            stop.set()
            for w in workers:
                w.join(timeout=3)
                if w.is_alive():
                    w.terminate()

    print("\ninterpretation:")
    print("  sustained ~= PL1L value  -> the EC limit IS enforced")
    print("  sustained >> PL1L        -> EC limit not applied (or needs the")
    print("                              custom-profile latch plus a fresh write)")


if __name__ == "__main__":
    main()
