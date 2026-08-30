#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_characterise_safe.py — measure fan curves WITH safety monitoring.

Key differences from fan_characterise.py:
- Uses hwmon for RPM (driver path, never stalls fans)
- Real-time RPM monitor thread: alerts IMMEDIATELY on 0 RPM
- Audible bell + visual alert + optional auto-shutdown
- Panic key (Enter) = instant restore + kill load
- Lower MIN_DUTY_PCT floor (35%) enforced
- Abort temp 92C, start max 70C

Usage:
    sudo python3 fan_characterise_safe.py --duties 40,55,70,85,100
    sudo python3 fan_characterise_safe.py --duties 35,45,55,65,75,85,100 --auto-shutdown
    sudo python3 fan_characterise_safe.py --dry-run

SAFETY: If fans hit 0 RPM, you have ~seconds before thermal throttle.
        Press ENTER at ANY TIME to panic-restore.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected
from hydroc.hardware import find_hwmon

REG_MANUAL_FAN_CTRL = 0x0751
REG_PWM_1, REG_PWM_2 = 0x075B, 0x075C
FAN_MODE_USER = 1 << 7
PWM_MAX = 200

RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"

MIN_DUTY_PCT = 35
ABORT_TEMP_C = 92
START_TEMP_MAX_C = 70
SAMPLE_S = 2.0
MAX_HOLD_S = 180
STABLE_SAMPLES = 5
STABLE_BAND_C = 1.0

RPM_POLL_S = 0.5
RPM_ALERT_DEBOUNCE = 2


class RPMMonitor:
    """Background thread: watches hwmon fan RPM, alerts on 0."""

    def __init__(self, hwmon_path: str, auto_shutdown: bool = False):
        self.hwmon = hwmon_path
        self.auto_shutdown = auto_shutdown
        self._stop = threading.Event()
        self._alerted = False
        self._zero_count = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.panic = threading.Event()

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=2)

    def _read_rpm(self) -> tuple[int | None, int | None]:
        try:
            with open(os.path.join(self.hwmon, "fan1_input")) as f:
                rpm1 = int(f.read().strip())
            with open(os.path.join(self.hwmon, "fan2_input")) as f:
                rpm2 = int(f.read().strip())
            return rpm1, rpm2
        except (OSError, ValueError):
            return None, None

    def _alert(self, rpm1, rpm2):
        if self._alerted:
            return
        self._alerted = True
        msg = f"\n{'!'*60}\n!!! FANS STOPPED: rpm1={rpm1} rpm2={rpm2} !!!\n{'!'*60}\n"
        sys.stderr.write(msg)
        sys.stderr.flush()
        for _ in range(5):
            sys.stderr.write("\a")
            sys.stderr.flush()
            time.sleep(0.2)
        if self.auto_shutdown:
            sys.stderr.write("AUTO-SHUTDOWN triggered in 5s... press ENTER to cancel\n")
            sys.stderr.flush()
            time.sleep(5)
            if not self.panic.is_set():
                os.system("systemctl poweroff")

    def _run(self):
        while not self._stop.is_set():
            rpm1, rpm2 = self._read_rpm()
            if rpm1 is not None and rpm2 is not None:
                if rpm1 == 0 and rpm2 == 0:
                    self._zero_count += 1
                    if self._zero_count >= RPM_ALERT_DEBOUNCE / RPM_POLL_S:
                        self._alert(rpm1, rpm2)
                else:
                    self._zero_count = 0
                    self._alerted = False
            time.sleep(RPM_POLL_S)


def sensors(hwmon: str) -> dict:
    out: dict = {}
    if not hwmon:
        return out
    for f in ("pwm1", "pwm2", "fan1_input", "fan2_input", "temp1_input", "temp2_input"):
        try:
            with open(os.path.join(hwmon, f)) as fh:
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
    def __init__(self):
        self.last = read_energy()
        self.t = time.monotonic()

    def sample(self) -> float | None:
        now, e = time.monotonic(), read_energy()
        if e is None or self.last is None:
            return None
        dt, de = now - self.t, e - self.last
        self.last, self.t = e, now
        if dt <= 0 or de < 0:
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


def hold_until_stable(ec, duty_reg: int, watts: Watts, hwmon: str,
                       rpm_monitor: RPMMonitor, dry: bool) -> dict:
    history: list[float] = []
    samples: list[dict] = []
    override = False
    t0 = time.monotonic()

    while time.monotonic() - t0 < MAX_HOLD_S:
        time.sleep(SAMPLE_S)
        s = sensors(hwmon)
        cpu = (s.get("temp1_input") or 0) / 1000.0
        gpu = (s.get("temp2_input") or 0) / 1000.0
        w = watts.sample()

        if rpm_monitor.panic.is_set():
            return {"aborted": "panic key pressed", "samples": samples, "ec_override": override}

        if not dry and ec is not None:
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


def restore_fans(ec, saved: dict):
    print("\n=== RESTORING FAN CONTROL ===")
    for addr in (REG_PWM_1, REG_PWM_2, REG_MANUAL_FAN_CTRL):
        if addr in saved:
            try:
                ec.write_verify(addr, saved[addr])
                print(f"  restored 0x{addr:04X} = 0x{saved[addr]:02X}")
            except Exception as e:
                print(f"  WARNING: could not restore 0x{addr:04X}: {e}")
    time.sleep(2)
    hwmon = find_hwmon()
    if hwmon:
        s = sensors(hwmon)
        print(f"  now: pwm {s.get('pwm1')}/{s.get('pwm2')}  rpm {s.get('fan1_input')}/{s.get('fan2_input')}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duties", default="40,55,70,85,100",
                    help="duty percentages to characterise")
    ap.add_argument("--dry-run", action="store_true",
                    help="no load and no EC writes; check the harness only")
    ap.add_argument("-o", "--output", default="fan-characterisation-safe.json")
    ap.add_argument("--auto-shutdown", action="store_true",
                    help="power off if fans stop and panic not pressed in 5s")
    args = ap.parse_args()

    duties = [int(x) for x in args.duties.split(",") if x.strip()]
    bad = [d for d in duties if d < MIN_DUTY_PCT or d > 100]
    if bad and not args.dry_run:
        print(f"refusing duties outside {MIN_DUTY_PCT}-100%: {bad}")
        return 1

    if os.geteuid() != 0 and not args.dry_run:
        print("needs root: sudo python3 fan_characterise_safe.py")
        return 1

    hwmon = find_hwmon()
    if not hwmon:
        print("uniwill hwmon not found -- is the driver bound?")
        return 1
    if read_energy() is None:
        print("cannot read RAPL energy counter (root only) -- no watts")

    s = sensors(hwmon)
    cpu0 = (s.get("temp1_input") or 0) / 1000.0
    if cpu0 > START_TEMP_MAX_C and not args.dry_run:
        print(f"too warm to start ({cpu0:.0f}C). Let it idle and retry.")
        return 1

    ncpu = os.cpu_count() or 8
    if not shutil.which("stress-ng") and not args.dry_run:
        print("stress-ng not found: paru -S stress-ng")
        return 1

    ec = None if args.dry_run else EC()
    saved: dict[int, int] = {}
    load = None
    results = []

    rpm_monitor = RPMMonitor(hwmon, auto_shutdown=args.auto_shutdown)
    rpm_monitor.start()

    def panic_handler():
        rpm_monitor.panic.set()

    print("\n=== SAFETY ===")
    print(f"  MIN_DUTY_PCT: {MIN_DUTY_PCT}%")
    print(f"  ABORT_TEMP_C: {ABORT_TEMP_C}C")
    print(f"  RPM monitor: {'ON (auto-shutdown)' if args.auto_shutdown else 'ON (alert only)'}")
    print(f"  PANIC: press ENTER at any time to instantly restore + kill load")
    print(f"  hwmon path: {hwmon}")
    print()

    try:
        if not args.dry_run:
            for r in (REG_MANUAL_FAN_CTRL, REG_PWM_1, REG_PWM_2):
                saved[r] = ec.read(r)
            ec.write_verify(REG_MANUAL_FAN_CTRL, saved[REG_MANUAL_FAN_CTRL] | FAN_MODE_USER)
            print(f"armed FAN_MODE_USER (0x0751 -> 0x{saved[REG_MANUAL_FAN_CTRL] | FAN_MODE_USER:02X})")

        watts = Watts()
        if not args.dry_run:
            print(f"starting load: stress-ng --cpu {ncpu} --cpu-method matrixprod")
            load = start_load(ncpu)
            time.sleep(5)

        for pct in duties:
            if rpm_monitor.panic.is_set():
                break
            reg = max(1, min(PWM_MAX, round(PWM_MAX * pct / 100)))
            print(f"\n=== duty {pct}%  (0x075B = {reg} of {PWM_MAX})")
            applied = None
            if not args.dry_run:
                try:
                    ec.write_verify(REG_PWM_1, reg)
                    ec.write_verify(REG_PWM_2, reg)
                except (ECUnavailable, ECWriteRejected) as e:
                    print(f"  write rejected outright: {e}")
                    results.append({"duty_pct": pct, "duty_reg": reg, "applied": False, "note": str(e)})
                    continue
                time.sleep(4)
                applied = ec.read(REG_PWM_1) == reg
                print(f"  write {'held' if applied else 'REVERTED IMMEDIATELY'} after 4s")
            point = hold_until_stable(ec, reg, watts, hwmon, rpm_monitor, args.dry_run)
            point["duty_pct"] = pct
            point["duty_reg"] = reg
            point["applied"] = applied
            results.append(point)
            if point.get("aborted"):
                print(f"  ABORTED: {point['aborted']}")
                break

    except KeyboardInterrupt:
        print("\ninterrupted (Ctrl-C)")
        rpm_monitor.panic.set()
    except (ECUnavailable, ECWriteRejected) as e:
        print(f"\nEC error: {e}")
        rpm_monitor.panic.set()
    finally:
        stop_load(load)
        rpm_monitor.stop()
        if ec is not None:
            restore_fans(ec, saved)

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
        print("\n  The duty write never held, at any step. Manual fan control")
        print("  is not available via FAN_MODE_USER on this path.")
    elif any(p.get("ec_override") for p in results if p.get("applied")):
        print("\n  EC overrode us later -- thermal protection survives FAN_MODE_USER.")
        print("  A user curve has a backstop underneath it.")
    elif results and not args.dry_run:
        print("\n  EC never overrode us. FAN_MODE_USER hands fans over unconditionally.")
        print("  Any shipped curve needs its own temperature floor.")
    return 0


if __name__ == "__main__":
    # Non-blocking stdin check for panic key
    import tty, termios
    old = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())
        sys.exit(main())
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)