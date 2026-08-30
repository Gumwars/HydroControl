# SPDX-License-Identifier: MIT
"""
hydroc.hardware — the persistent settings surface.

Two rules drive the design:

1. Prefer driver sysfs over raw EC access. Where uniwill-laptop exposes an
   attribute (charge_types, ctgp_offset, the platform toggles), use it -- it
   validates, it is stable, and it does not need acpi_call. Reserve EC writes
   for what has no driver path: the custom-profile latch and CPU power limits.

2. Read hardware, never trust stored preferences. Charging profile, the
   custom-profile latch and the power limits all live in volatile EC state and
   revert to factory defaults on every power cycle. A UI that renders a saved
   preference as live state will confidently lie after every reboot.

apply() therefore computes a diff between desired and actual, applies only what
differs, orders the latch before the power limits, and verifies every write.
"""

from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass, field

from .ec import EC, ECUnavailable, ECWriteRejected

PLATFORM = "/sys/bus/platform/devices/INOU0000:00"
RAPL = "/sys/class/powercap/intel-rapl:0"
BAT = "/sys/class/power_supply/BAT0"

# Control Center's names -> the kernel's charge_types tokens.
# Confirmed against tuxedo-drivers: EC 0 = high capacity, 1 = balanced,
# 2 = stationary. The kernel token names are misleading -- Long_Life is
# "Balanced", NOT "Long Haul".
CHARGE_PROFILES = {
    "stationary": "Trickle",
    "balanced": "Long_Life",
    "long_haul": "Standard",
}
CHARGE_PROFILES_REV = {v: k for k, v in CHARGE_PROFILES.items()}

TOGGLES = ("fn_lock", "super_key_enable", "touchpad_toggle_enable",
           "ac_auto_boot", "usb_powershare_high")

# The driver rejects both of these being on at once.
EXCLUSIVE = ("ac_auto_boot", "usb_powershare_high")


@dataclass
class Change:
    setting: str
    was: object
    now: object
    ok: bool = True
    error: str = ""

    def __str__(self) -> str:
        arrow = f"{self.was!r} -> {self.now!r}"
        return (f"{self.setting}: {arrow}" if self.ok
                else f"{self.setting}: {arrow} FAILED ({self.error})")


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _write(path: str, value: str) -> str:
    """Returns '' on success, else an error string.

    sysfs refuses file creation, so opening a *missing* attribute for write
    fails with EACCES, not ENOENT -- which reads as "Permission denied" and
    sends you hunting for a privilege problem that does not exist. Check
    existence first so the message names the real cause.
    """
    if not os.path.exists(path):
        return f"attribute missing: {path}"
    try:
        with open(path, "w") as fh:
            fh.write(value)
        return ""
    except PermissionError:
        return f"permission denied (need root): {path}"
    except OSError as e:
        return str(e)


def find_hwmon() -> str | None:
    """hwmon index is assigned at probe order -- resolve by name, never index."""
    for d in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _read(os.path.join(d, "name")) == "uniwill":
            return d
    return None


class Hardware:
    def __init__(self):
        self.ec = EC()
        self._hwmon = find_hwmon()
        self._energy = None          # (microjoules, monotonic seconds)

    # -- availability ------------------------------------------------------

    @staticmethod
    def driver_bound() -> bool:
        """True only when uniwill-laptop is actually bound.

        The INOU0000:00 platform directory is created by ACPI enumeration and
        exists whether or not any driver claims it -- so its presence proves
        nothing. The `driver` symlink is the real test.
        """
        return os.path.islink(os.path.join(PLATFORM, "driver"))

    def status(self) -> dict:
        ec_ok, ec_why = EC.available()
        return {
            "driver_loaded": self.driver_bound(),
            "hwmon": self._hwmon is not None,
            "battery": os.path.isdir(BAT),
            "ec_available": ec_ok,
            "ec_error": ec_why,
        }

    # -- telemetry (read-only, safe without root) --------------------------

    def package_watts(self) -> float | None:
        """True package draw, by differentiating the RAPL energy counter.

        Deliberately not constraint_*_power_limit_uw: those report the MSR
        values (205 W here) while the EC enforces its own envelope outside the
        MSR path, so the limit fields are actively misleading. The energy
        counter is the only honest source.
        """
        raw = _read(f"{RAPL}/energy_uj")
        if raw is None:
            return None
        try:
            uj, now = int(raw), time.monotonic()
        except ValueError:
            return None

        prev, self._energy = self._energy, (uj, now)
        if prev is None:
            return None
        d_uj, d_t = uj - prev[0], now - prev[1]
        if d_t <= 0:
            return None
        if d_uj < 0:                                  # counter wrapped
            wrap = _read(f"{RAPL}/max_energy_range_uj")
            try:
                d_uj += int(wrap)
            except (TypeError, ValueError):
                return None
        return round(d_uj / 1e6 / d_t, 1)

    def telemetry(self) -> dict:
        out: dict = {}
        out["package_w"] = self.package_watts()
        h = self._hwmon or find_hwmon()
        if h:
            self._hwmon = h
            for key, f, scale in (("cpu_temp_c", "temp1_input", 1000),
                                  ("gpu_temp_c", "temp2_input", 1000),
                                  # driver labels these Main/Secondary; on this
                                  # chassis fan 1 cools the CPU and fan 2 the GPU
                                  ("fan1_rpm", "fan1_input", 1),
                                  ("fan2_rpm", "fan2_input", 1),
                                  ("pwm1", "pwm1", 1),
                                  ("pwm2", "pwm2", 1)):
                v = _read(os.path.join(h, f))
                out[key] = (int(v) / scale if scale > 1 else int(v)) if v else None

        for key, f, scale in (("capacity_pct", "capacity", 1),
                              ("charge_now_mah", "charge_now", 1000),
                              ("charge_full_design_mah", "charge_full_design", 1000),
                              ("current_ma", "current_now", 1000),
                              ("voltage_mv", "voltage_now", 1000)):
            v = _read(os.path.join(BAT, f))
            out[key] = int(v) // scale if v else None
        out["status"] = _read(os.path.join(BAT, "status"))
        out["ac_online"] = _read("/sys/class/power_supply/AC0/online") == "1"
        if out.get("voltage_mv"):
            out["volts_per_cell"] = round(out["voltage_mv"] / 1000 / 4, 3)
        return out

    # -- persistent settings: read actual state ----------------------------

    def read_state(self) -> dict:
        """Actual hardware state. This -- not a config file -- is the truth."""
        state: dict = {}

        raw = _read(os.path.join(BAT, "charge_types"))
        if raw:
            active = None
            for tok in raw.split():
                if tok.startswith("["):
                    active = tok.strip("[]")
            state["charge_profile"] = CHARGE_PROFILES_REV.get(active, active)

        v = _read(os.path.join(BAT, "charge_control_end_threshold"))
        state["charge_threshold"] = int(v) if v else None

        for t in TOGGLES:
            v = _read(os.path.join(PLATFORM, t))
            state[t] = (v == "1") if v is not None else None

        v = _read(os.path.join(PLATFORM, "ctgp_offset"))
        state["gpu_ctgp_offset"] = int(v) if v else None

        ec_ok, _ = EC.available()
        if ec_ok:
            try:
                state["custom_profile"] = self.ec.custom_profile_enabled()
                pl = self.ec.get_power_limits()
                state["cpu_pl1"] = pl["pl1_setting"]
                state["cpu_pl2"] = pl["pl2_setting"]
                state["cpu_pl4"] = pl["pl4_setting"]
                state["double_pl4"] = pl["double_pl4"]
                state["cpu_pl1_live"] = pl["pl1_live"]
                state["cpu_pl2_live"] = pl["pl2_live"]
                state["cpu_pl4_live"] = pl["pl4_live"]
                state["charge_cycles"] = self.ec.get_charge_cycles()
            except Exception:
                # EC is best-effort: a failure here must degrade the reading,
                # never take down the caller's request thread.
                pass
        return state

    # -- apply -------------------------------------------------------------

    def normalize(self, desired: dict) -> dict:
        """Snap a settings dict to values the hardware can actually hold.

        Intent that the hardware cannot express is permanent phantom drift:
        read_state() reports what the register holds, drift() compares it
        against the request, and no amount of re-applying makes them agree.
        PL4 is the case that bites -- half-scale storage means odd watts round
        down -- so normalize before writing, before comparing, and before
        persisting. Same failure mode as a renamed key (see LEGACY_KEYS).
        """
        out = dict(desired)
        if out.get("cpu_pl4") is not None:
            try:
                out["cpu_pl4"] = self.ec.quantize_power_limit(
                    "pl4", int(out["cpu_pl4"]))
            except (ECUnavailable, ECWriteRejected, ValueError):
                pass          # EC unreadable: leave intent untouched
        return out

    def apply(self, desired: dict, dry_run: bool = False) -> list[Change]:
        """Reconcile hardware to `desired`. Only differences are written."""
        changes: list[Change] = []
        desired = self.normalize(desired)
        actual = self.read_state()

        # Everything except the EC-backed settings needs uniwill-laptop bound.
        # Reporting one clear cause beats eight downstream symptoms.
        driver = self.driver_bound()
        if not driver:
            blocked = [k for k in (["charge_profile", "charge_threshold",
                                    "gpu_ctgp_offset"] + list(TOGGLES))
                       if k in desired and desired[k] is not None]
            if blocked:
                changes.append(Change(
                    "uniwill-laptop", "not bound", "required", ok=False,
                    error=f"module not loaded -- {len(blocked)} setting(s) skipped: "
                          + ", ".join(blocked)))
                desired = {k: v for k, v in desired.items() if k not in blocked}

        def differs(key):
            return key in desired and desired[key] is not None \
                and desired[key] != actual.get(key)

        # Guard the mutually exclusive pair before touching either.
        if all(desired.get(k) for k in EXCLUSIVE):
            changes.append(Change("ac_auto_boot/usb_powershare", None, None,
                                  ok=False,
                                  error="mutually exclusive; refusing both"))
            desired = dict(desired)
            for k in EXCLUSIVE:
                desired.pop(k, None)

        # 1. Custom-profile latch FIRST -- power-limit writes are accepted and
        #    silently ignored while it is disarmed.
        if differs("custom_profile"):
            ch = Change("custom_profile", actual.get("custom_profile"),
                        desired["custom_profile"])
            if not dry_run:
                try:
                    self.ec.set_custom_profile(bool(desired["custom_profile"]))
                except (ECUnavailable, ECWriteRejected) as e:
                    ch.ok, ch.error = False, str(e)
            changes.append(ch)

        # 2. CPU power limits, now that the latch is set. PL1 (sustained),
        #    PL2 (boost) and PL4 (peak) are independent knobs.
        desired = dict(desired)
        if "cpu_power_limit" in desired and "cpu_pl1" not in desired:
            desired["cpu_pl1"] = desired.pop("cpu_power_limit")   # legacy key

        for key, which in (("cpu_pl1", "pl1"), ("cpu_pl2", "pl2"), ("cpu_pl4", "pl4")):
            if not differs(key):
                continue
            ch = Change(key, actual.get(key), desired[key])
            if not dry_run:
                try:
                    self.ec.set_power_limit(which, int(desired[key]))
                except (ECUnavailable, ECWriteRejected, ValueError) as e:
                    ch.ok, ch.error = False, str(e)
            changes.append(ch)

        # 3. Battery, via sysfs.
        if differs("charge_profile"):
            token = CHARGE_PROFILES.get(desired["charge_profile"])
            ch = Change("charge_profile", actual.get("charge_profile"),
                        desired["charge_profile"])
            if token is None:
                ch.ok, ch.error = False, f"unknown profile {desired['charge_profile']!r}"
            elif not dry_run:
                err = _write(os.path.join(BAT, "charge_types"), token)
                if err:
                    ch.ok, ch.error = False, err
            changes.append(ch)

        # The driver only exposes this when the descriptor claims
        # BATTERY_CHARGE_LIMIT, and on this chassis it deliberately does not:
        # the EC stores a threshold and never enforces it (DESIGN.md §3.2). A
        # profile written before that change still carries the key, so skip it
        # rather than reporting a failed write on every boot. Absent hardware
        # is not an error.
        if differs("charge_threshold") and not os.path.exists(
                os.path.join(BAT, "charge_control_end_threshold")):
            pass
        elif differs("charge_threshold"):
            ch = Change("charge_threshold", actual.get("charge_threshold"),
                        desired["charge_threshold"])
            if not dry_run:
                err = _write(os.path.join(BAT, "charge_control_end_threshold"),
                             str(int(desired["charge_threshold"])))
                if err:
                    ch.ok, ch.error = False, err
            changes.append(ch)

        # 4. Platform toggles and GPU offset.
        for t in TOGGLES:
            if differs(t):
                ch = Change(t, actual.get(t), desired[t])
                if not dry_run:
                    err = _write(os.path.join(PLATFORM, t),
                                 "1" if desired[t] else "0")
                    if err:
                        ch.ok, ch.error = False, err
                changes.append(ch)

        if differs("gpu_ctgp_offset"):
            ch = Change("gpu_ctgp_offset", actual.get("gpu_ctgp_offset"),
                        desired["gpu_ctgp_offset"])
            if not dry_run:
                err = _write(os.path.join(PLATFORM, "ctgp_offset"),
                             str(int(desired["gpu_ctgp_offset"])))
                if err:
                    ch.ok, ch.error = False, err
            changes.append(ch)

        return changes

    def drift(self, desired: dict) -> dict:
        """Settings where hardware disagrees with the saved profile.

        After a power cycle this is everything volatile -- which is exactly
        what the UI needs to surface rather than hide.
        """
        actual = self.read_state()
        desired = self.normalize(desired)
        # Only compare settings the hardware actually reports. A stale key left
        # by a rename would otherwise show as drift forever, since no write can
        # ever make it match.
        return {k: {"desired": v, "actual": actual.get(k)}
                for k, v in desired.items()
                if v is not None and k in actual and actual[k] != v}
