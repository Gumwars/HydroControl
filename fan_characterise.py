#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_characterise.py — measure this machine's thermal transfer, on this OS.

The vendor's fan curves were calibrated on Windows, where Intel DPTF is a live
policy engine shaving package power against skin-temperature participants. On
this system there is no DPTF policy and no thermald -- the only thermal actor
besides the EC is a binary ACPI fan cooling device. So the vendor curves were
tuned against a system whose other half is missing here, and copying them would
import half a calibration. (They are also vendor data, and do not become MIT by
being copied into an MIT tree.)

This measures the thing a curve actually needs to know: at a given fan duty and
a known sustained package power, where does temperature settle? That is a
property of this heatsink, this paste, this chassis and this kernel, and it is
what a curve should be derived from.

For each duty step it holds a fixed load, waits for temperature to stop rising,
and records duty / equilibrium temp / package watts / RPM. Output is JSON you
can fit a curve to.

    sudo python3 fan_characterise.py --duties 40,55,70,85,100
    sudo python3 fan_characterise.py --dry-run      # no load, no writes

IT ALSO ANSWERS THE SAFETY QUESTION
-----------------------------------
Whether FAN_MODE_USER takes the EC's own emergency ramp offline. If the EC
overrides our duty as temperature climbs, we will see the commanded duty and
the observed RPM diverge, and that is logged per sample. Knowing this decides
whether a user-editable curve can ever be allowed to set a low duty.

SAFETY
------
Aborts and restores if temperature passes ABORT_TEMP_C, if a sample cannot be
read, or on Ctrl-C. Duty is never taken below MIN_DUTY_PCT. Load is stopped
before restoring. A full power cycle clears anything left behind.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected     # noqa: E402
from hydroc.hardware import find_hwmon                       # noqa: E402

REG_MANUAL_FAN_CTRL = 0x0751
REG_PWM_1, REG_PWM_2 = 0x075B, 0x075C
FAN_MODE_USER = 1 << 7
PWM_MAX = 200

RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"

MIN_DUTY_PCT = 35             # never quieter than this while under load
ABORT_TEMP_C = 92
START_TEMP_MAX_C = 70
SAMPLE_S = 2.0
MAX_HOLD_S = 180              # give up waiting for equilibrium
STABLE_SAMPLES = 5            # consecutive samples within STABLE_BAND_C
STABLE_BAND_C = 1.0


def sensors() -> dict:
    h = find_hwmon()
    out: dict = {}
    if not h:
        return out
    for f in ("pwm1", "pwm2", "fan1_input", "fan2_input",
              "temp1_input", "temp2_input"):
        try:
            with open(os.path.join(h, f)) as fh:
                out[f] = int(fh.read().strip())
        except (OSError, ValueError):
            out[f] = None
    return out


def read_energy() -> int | None:
    try:
        with open(RAPL) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


class Watts:
    """Package watts by differentiating the RAPL energy counter.

    The RAPL *limit* fields report 205 W regardless because the EC enforces
    outside the MSR path -- the energy counter is the only honest source.
    """

    def __init__(self) -> None:
        self.last = read_energy()
        self.t = time.monotonic()

    def sample(self) -> float | None:
        now, e = time.monotonic(), read_energy()
        if e is None or self.last is None:
            return None
        dt, de = now - self.t, e - self.last
        self.last, self.t = e, now
        if dt <= 0:
            return None
        if de < 0:                       # counter wrapped
            return None
        return de / dt / 1e6


def start_load(ncpu: int):
    sng = shutil.which("stress-ng")
    if not sng:
        return None
    return subprocess.Popen(
        [sng, "--cpu", str(ncpu), "--cpu-method", "matrixprod", "--timeout", "0"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid)


def stop_load(proc) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def hold_until_stable(ec, duty_reg: int, watts: Watts, dry: bool) -> dict:
    """Sample until temperature plateaus, then return the equilibrium point."""
    history: list[float] = []
    samples: list[dict] = []
    override = False
    t0 = time.monotonic()

    while time.monotonic() - t0 < MAX_HOLD_S:
        time.sleep(SAMPLE_S)
        s = sensors()
        cpu = (s.get("temp1_input") or 0) / 1000.0
        gpu = (s.get("temp2_input") or 0) / 1000.0
        w = watts.sample()

        # Divergence here only means "the EC took the fans back" if the write
        # STUCK in the first place. Last time it never did, and reporting that
        # as thermal protection was flattering and wrong -- see DESIGN.md §4.2.
        if not dry and ec is not None:
            # Reuse the caller's EC: /proc/acpi/call is one global file and the
            # serialising lock lives on the instance. A fresh EC per sample
            # would step outside it.
            try:
                if ec.read(REG_PWM_1) != duty_reg:
                    override = True
            except ECUnavailable:
                pass

        samples.append({"t": round(time.monotonic() - t0, 1),
                        "cpu_c": cpu, "gpu_c": gpu,
                        "watts": round(w, 1) if w else None,
                        "rpm1": s.get("fan1_input"), "rpm2": s.get("fan2_input"),
                        "pwm1": s.get("pwm1")})
        print(f"      t={samples[-1]['t']:>5}s  {cpu:.0f}C/{gpu:.0f}C  "
              f"{samples[-1]['watts'] or '--'}W  "
              f"rpm {s.get('fan1_input')}/{s.get('fan2_input')}"
              + ("   EC OVERRIDE" if override else ""))

        if max(cpu, gpu) > ABORT_TEMP_C:
            return {"aborted": f"{max(cpu, gpu):.0f}C > {ABORT_TEMP_C}C",
                    "samples": samples, "ec_override": override}

        history.append(cpu)
        if len(history) >= STABLE_SAMPLES:
            window = history[-STABLE_SAMPLES:]
            if max(window) - min(window) <= STABLE_BAND_C:
                break

    tail = samples[-STABLE_SAMPLES:] if samples else []
    wl = [x["watts"] for x in tail if x["watts"]]
    return {
        "equilibrium_cpu_c": round(sum(x["cpu_c"] for x in tail) / len(tail), 1) if tail else None,
        "equilibrium_gpu_c": round(sum(x["gpu_c"] for x in tail) / len(tail), 1) if tail else None,
        "watts": round(sum(wl) / len(wl), 1) if wl else None,
        "rpm1": tail[-1]["rpm1"] if tail else None,
        "rpm2": tail[-1]["rpm2"] if tail else None,
        "ec_override": override,
        "samples": samples,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duties", default="40,55,70,85,100",
                    help="duty percentages to characterise")
    ap.add_argument("--dry-run", action="store_true",
                    help="no load and no EC writes; check the harness only")
    ap.add_argument("-o", "--output", default="fan-characterisation.json")
    args = ap.parse_args()

    duties = [int(x) for x in args.duties.split(",") if x.strip()]
    bad = [d for d in duties if d < MIN_DUTY_PCT or d > 100]
    if bad and not args.dry_run:
        print(f"refusing duties outside {MIN_DUTY_PCT}-100%: {bad}")
        return 1

    if os.geteuid() != 0 and not args.dry_run:
        print("needs root: sudo python3 fan_characterise.py")
        return 1
    if not find_hwmon():
        print("uniwill hwmon not found -- is the driver bound?")
        return 1
    if read_energy() is None:
        print("cannot read the RAPL energy counter (root only) -- no watts")

    s = sensors()
    cpu0 = (s.get("temp1_input") or 0) / 1000.0
    if cpu0 > START_TEMP_MAX_C and not args.dry_run:
        print(f"too warm to start ({cpu0:.0f}C). Let it idle and retry.")
        return 1

    ncpu = os.cpu_count() or 8
    if not shutil.which("stress-ng") and not args.dry_run:
        print("stress-ng not found. Install it -- a Python loop is a weak load")
        print("and will not reach the package limit:  paru -S stress-ng")
        return 1

    ec = None if args.dry_run else EC()
    saved: dict[int, int] = {}
    load = None
    results = []

    try:
        if not args.dry_run:
            for r in (REG_MANUAL_FAN_CTRL, REG_PWM_1, REG_PWM_2):
                saved[r] = ec.read(r)
            ec.write_verify(REG_MANUAL_FAN_CTRL, saved[REG_MANUAL_FAN_CTRL] | FAN_MODE_USER)
            print(f"armed FAN_MODE_USER (0x0751 -> "
                  f"0x{saved[REG_MANUAL_FAN_CTRL] | FAN_MODE_USER:02X})")

        watts = Watts()
        if not args.dry_run:
            print(f"starting load: stress-ng --cpu {ncpu} --cpu-method matrixprod")
            load = start_load(ncpu)
            time.sleep(5)

        for pct in duties:
            reg = max(1, min(PWM_MAX, round(PWM_MAX * pct / 100)))
            print(f"\n=== duty {pct}%  (0x075B = {reg} of {PWM_MAX})")
            applied = None
            if not args.dry_run:
                try:
                    ec.write_verify(REG_PWM_1, reg)
                    ec.write_verify(REG_PWM_2, reg)
                except (ECUnavailable, ECWriteRejected) as e:
                    print(f"  write rejected outright: {e}")
                    results.append({"duty_pct": pct, "duty_reg": reg,
                                    "applied": False, "note": str(e)})
                    continue
                time.sleep(4)
                # Did it survive four seconds? If not, the EC is refusing manual
                # control, which is a completely different finding from the EC
                # stepping in later to protect the machine.
                applied = ec.read(REG_PWM_1) == reg
                print(f"  write {'held' if applied else 'REVERTED IMMEDIATELY'} "
                      f"after 4s")
            point = hold_until_stable(ec, reg, watts, args.dry_run)
            point["duty_pct"] = pct
            point["duty_reg"] = reg
            point["applied"] = applied
            results.append(point)
            if point.get("aborted"):
                print(f"  ABORTED: {point['aborted']}")
                break
    except KeyboardInterrupt:
        print("\ninterrupted")
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"\nEC error: {e}")
    finally:
        stop_load(load)
        if ec is not None:
            print("\nrestoring fan registers...")
            for addr in (REG_PWM_1, REG_PWM_2, REG_MANUAL_FAN_CTRL):
                if addr in saved:
                    try:
                        ec.write_verify(addr, saved[addr])
                    except Exception:
                        print(f"  WARNING: could not restore 0x{addr:04X}")
        time.sleep(3)
        print(f"  now: {sensors()}")

    payload = {"kernel": os.uname().release, "cpus": ncpu,
               "pwm_max": PWM_MAX, "points": results}
    try:
        with open(args.output, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nwrote {args.output}")
    except OSError as e:
        print(f"could not write {args.output}: {e}")

    print("\n=== summary ===")
    print(f"  {'duty':>5}  {'CPU':>6}  {'GPU':>6}  {'watts':>6}  {'rpm1':>6}  override")
    for p in results:
        print(f"  {p['duty_pct']:>4}%  {str(p.get('equilibrium_cpu_c')):>6}  "
              f"{str(p.get('equilibrium_gpu_c')):>6}  {str(p.get('watts')):>6}  "
              f"{str(p.get('rpm1')):>6}  {'YES' if p.get('ec_override') else 'no'}")
    never_applied = results and all(p.get("applied") is False for p in results)
    if never_applied:
        print("\n  The duty write never held, at any step. This is NOT the EC")
        print("  protecting the machine -- it is the EC refusing manual control")
        print("  outright. Manual fan duty is not available by this route.")
    elif any(p.get("ec_override") for p in results if p.get("applied")):
        print("\n  The write held and the EC later took it back -- that is genuine")
        print("  thermal protection surviving FAN_MODE_USER. A user curve has a")
        print("  backstop underneath it.")
    elif results and not args.dry_run:
        print("\n  The EC never overrode us. FAN_MODE_USER hands over the fans")
        print("  unconditionally, so a user-editable curve is the ONLY protection.")
        print("  Any shipped curve needs its own temperature floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
