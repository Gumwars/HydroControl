# SPDX-License-Identifier: MIT
"""
hydroc.ec — Embedded controller access via the firmware's own ACPI methods.

The DSDT exposes the EC as MMIO at 0xFE410000, wrapped by \\_SB.INOU.ECRR (read)
and \\_SB.INOU.ECRW (write), serialised behind the ACPI mutex UWOL. Going
through those is materially safer than poking /dev/mem, which bypasses the lock
and can race the EC's own firmware.

Two windows matter, both reached through the same accessors:

    0x07xx   settings   -- the write side
    0x04xx   ECXP       -- the EC's live values; writes here are accepted and
                           silently discarded

That asymmetry is the single most expensive lesson from reverse engineering
this machine: ECRW reports success for writes that do nothing. Every write in
this module is therefore read back and verified.
"""

from __future__ import annotations

import os
import threading
import time

CALL = "/proc/acpi/call"
ECRR = r"\_SB.INOU.ECRR"
ECRW = r"\_SB.INOU.ECRW"

# --- settings window (writable) ---
REG_AP_OEM         = 0x0741     # bit 0 = ENABLE_MANUAL_CTRL, the master switch
REG_CUSTOM_PROFILE = 0x0727     # bit 6 arms custom TDP/fan control
# TDP settings are a contiguous triple from 0x0783 -- tuxedo-drivers indexes
# them 0/1/2 off that base. PL3 is not addressable here.
REG_TDP_BASE       = 0x0783
# Minimum gap between EC accesses. uniwill-acpi.c uses UNIWILL_EC_DELAY_US =
# 6000 and sleeps usleep_range(6000, 12000) after every ECRR/ECRW, because the
# OEM software does. Going faster stops the fans -- see EC._call.
EC_DELAY_S = 0.006

REG_PL1_SETTING    = 0x0783     # watts, 0 = firmware default
REG_PL2_SETTING    = 0x0784
REG_PL4_SETTING    = 0x0785     # NOTE: half-scale when double-PL4 is set
REG_FAN_CTRL       = 0x078E     # b3 charging-profile capable, b6 fan control
REG_OEM_4          = 0x07A6     # bits 5:4 charging profile
REG_CHARGE_CTRL    = 0x07B9     # bits 0:6 charge threshold

# --- ECXP live window (read-only in practice) ---
REG_PL1_LIVE       = 0x046A
REG_PL2_LIVE       = 0x046B
REG_PL3_LIVE       = 0x046E
REG_PL4_LIVE       = 0x046F
REG_TURBO          = 0x0466     # bit 0
REG_CYCLE_LO       = 0x04A6     # real cycle count; sysfs cycle_count reads 0
REG_CYCLE_HI       = 0x04A7

BIT_CUSTOM_PROFILE = 1 << 6
BIT_DOUBLE_PL4     = 1 << 7     # 0x0727 b7 -- PL4 is stored at half scale

# uniwill-acpi.c sets this in uniwill_ec_init() and clears it again from a devm
# cleanup action when the module unloads. It is the EC's "host software is in
# control" master switch, and it sits ABOVE the custom-profile latch: with it
# clear, 0x0727 bit 6 still reads armed, power-limit writes are still accepted,
# and the EC quietly ignores all of it and runs its own defaults.
#
# That combination -- everything reporting healthy while nothing takes effect --
# cost an evening and produced a handoff blaming the wrong register.
BIT_ENABLE_MANUAL_CTRL = 1 << 0

TDP_INDEX = {"pl1": 0, "pl2": 1, "pl4": 2}
BIT_HAS_CHARGE_PROFILE = 1 << 3
BIT_HAS_FAN_CTRL = 1 << 6


class ECUnavailable(RuntimeError):
    """acpi_call is missing, or we are not root."""


class ECWriteRejected(RuntimeError):
    """The write was accepted by ECRW but did not take effect."""


class EC:
    """Thin, verified accessor for the embedded controller.

    /proc/acpi/call is a single global file: you write the expression, then
    read the result. Two threads doing that concurrently interleave and each
    reads the other's answer. The server handles requests on threads, so every
    access is serialised here -- the ACPI UWOL mutex protects the EC from the
    firmware's side, but nothing protects this file from ours.
    """

    _lock = threading.RLock()          # class-level: one EC, one lock
    _last_access = 0.0                 # class-level: the EC is one device

    def __init__(self, path: str = CALL):
        self.path = path

    # -- availability -----------------------------------------------------

    @staticmethod
    def available() -> tuple[bool, str]:
        if os.geteuid() != 0:
            return False, "requires root"
        if not os.path.exists(CALL):
            return False, "acpi_call not loaded (modprobe acpi_call)"
        return True, ""

    def _call(self, expr: str) -> str:
        with self._lock:
            # The EC needs settling time between accesses. uniwill-acpi.c:
            #
            #   /* The OEM software always sleeps up to 6 ms after reading/
            #      writing EC registers, so we emulate this behaviour for
            #      maximum compatibility. */
            #   #define UNIWILL_EC_DELAY_US 6000
            #
            # and applies usleep_range() after every ECRR and ECRW. We did not,
            # and back-to-back reads STOPPED THE FANS: the EC services our ACPI
            # calls on the same firmware loop that runs its fan control, and
            # hammering it starves that loop. `fan_recover.py --report` does ten
            # reads and killed the fans with no write involved at all.
            #
            # This is also the likeliest explanation for the intermittent
            # write_verify rejections on 0x07A5 that did not reproduce, and for
            # the garbled replies ECUnavailable exists to absorb.
            #
            # Not optional, and not a probe-only concern: read_state() reads a
            # dozen registers back to back, once per UI poll.
            elapsed = time.monotonic() - EC._last_access
            if elapsed < EC_DELAY_S:
                time.sleep(EC_DELAY_S - elapsed)
            try:
                with open(self.path, "w") as fh:
                    fh.write(expr)
                with open(self.path) as fh:
                    return fh.read().strip().rstrip("\x00")
            except OSError as e:
                raise ECUnavailable(f"{self.path}: {e}") from e
            finally:
                EC._last_access = time.monotonic()

    # -- primitives -------------------------------------------------------

    def read(self, addr: int) -> int:
        raw = self._call(f"{ECRR} 0x{addr:X}")
        if raw.startswith("Error"):
            raise ECUnavailable(f"read 0x{addr:04X}: {raw}")
        try:
            return int(raw, 16) & 0xFF
        except ValueError as e:
            raise ECUnavailable(
                f"read 0x{addr:04X}: unparseable reply {raw!r}") from e

    def write(self, addr: int, value: int) -> None:
        raw = self._call(f"{ECRW} 0x{addr:X} 0x{value & 0xFF:X}")
        if raw.startswith("Error"):
            raise ECUnavailable(f"write 0x{addr:04X}: {raw}")

    def write_verify(self, addr: int, value: int, retries: int = 2) -> None:
        """Write, then confirm by reading back.

        Necessary because ECRW returns success for writes the EC ignores --
        notably anything aimed at the 0x04xx live window, and anything at all
        if the custom-profile latch is not armed.
        """
        value &= 0xFF
        with self._lock:
          for attempt in range(retries + 1):
            self.write(addr, value)
            time.sleep(0.05)
            got = self.read(addr)
            if got == value:
                return
          raise ECWriteRejected(
            f"0x{addr:04X}: wrote 0x{value:02X}, reads back 0x{got:02X}")

    def update_bits(self, addr: int, mask: int, value: int) -> None:
        with self._lock:                      # read-modify-write is atomic
            cur = self.read(addr)
            self.write_verify(addr, (cur & ~mask) | (value & mask))

    # -- capability -------------------------------------------------------

    def has_charging_profile(self) -> bool:
        return bool(self.read(REG_FAN_CTRL) & BIT_HAS_CHARGE_PROFILE)

    def has_fan_control(self) -> bool:
        return bool(self.read(REG_FAN_CTRL) & BIT_HAS_FAN_CTRL)

    # -- custom profile latch ---------------------------------------------

    def custom_profile_enabled(self) -> bool:
        return bool(self.read(REG_CUSTOM_PROFILE) & BIT_CUSTOM_PROFILE)

    def set_custom_profile(self, enable: bool) -> None:
        """Arm/disarm custom TDP mode.

        Some Uniwill devices need the bit cleared first for the set to apply;
        tuxedo-drivers does the same. Harmless where it is not needed. The
        laptop's profile LED turns white when this is on.
        """
        with self._lock:
          cur = self.read(REG_CUSTOM_PROFILE)
          if enable:
            self.write(REG_CUSTOM_PROFILE, cur & ~BIT_CUSTOM_PROFILE)
            time.sleep(0.05)
            self.write_verify(REG_CUSTOM_PROFILE, cur | BIT_CUSTOM_PROFILE)
          else:
            self.write_verify(REG_CUSTOM_PROFILE, cur & ~BIT_CUSTOM_PROFILE)

    # -- cpu power limits --------------------------------------------------

    def manual_control_enabled(self) -> bool:
        """Is the EC accepting host control at all? (0x0741 bit 0)

        Deliberately only a *reader*. If this is false while uniwill-laptop is
        bound, something has gone wrong that setting the bit behind the
        driver's back would hide rather than fix -- the driver owns this flag,
        and the honest remedy is to reload it.
        """
        return bool(self.read(REG_AP_OEM) & BIT_ENABLE_MANUAL_CTRL)

    def has_double_pl4(self) -> bool:
        """PL4 stored at half scale. tuxedo-drivers reads 0x0727 bit 7."""
        return bool(self.read(REG_CUSTOM_PROFILE) & BIT_DOUBLE_PL4)

    def quantize_power_limit(self, which: str, watts: int) -> int:
        """The value the hardware will actually hold for a requested wattage.

        Half-scale PL4 storage means the register can only express even watts:
        125 W is written as 62 and reads back as 124. Asking for an odd value
        therefore produces a setting the hardware can never report, which is
        indistinguishable from drift and cannot be cleared by re-applying.
        Callers must record the quantized value as intent, not the request.
        """
        if which == "pl4" and watts and self.has_double_pl4():
            return (int(watts) // 2) * 2
        return int(watts)

    def get_power_limits(self) -> dict:
        with self._lock:
            dbl = self.has_double_pl4()
            pl4_raw = self.read(REG_PL4_SETTING)
            return {
                "pl1_setting": self.read(REG_PL1_SETTING),
                "pl2_setting": self.read(REG_PL2_SETTING),
                "pl4_setting": pl4_raw * 2 if dbl else pl4_raw,
                "pl4_raw": pl4_raw,
                "double_pl4": dbl,
                "pl1_live": self.read(REG_PL1_LIVE),
                "pl2_live": self.read(REG_PL2_LIVE),
                "pl3_live": self.read(REG_PL3_LIVE),
                # The live mirror stores PL4 at the SAME half scale as the
                # setting register, and this doubled only the setting -- so the
                # UI reported "live 60 W" beside a 120 W setting, and 125 W for
                # a firmware default that is really 250 W. Measured: with the
                # latch armed at a 120 W setting, live reads 60; with it off,
                # live reads 125 against a 250 W ceiling. Both halve.
                "pl4_live": (lambda v: v * 2 if dbl else v)(self.read(REG_PL4_LIVE)),
                "pl4_live_raw": self.read(REG_PL4_LIVE),
            }

    def set_power_limit(self, which: str, watts: int) -> None:
        """Set one of pl1 / pl2 / pl4. 0 means firmware default.

        Requires the custom-profile latch (0x0727 bit 6); without it the write
        is accepted and silently ignored. Hardware.apply() arms it first.

        PL4 is stored at half scale on machines where 0x0727 bit 7 is set, so
        the register holds watts/2 -- reading it raw reports half the truth.
        """
        idx = TDP_INDEX.get(which)
        if idx is None:
            raise ValueError(f"unknown power limit {which!r}")
        ceiling = 250 if which == "pl4" else 125
        if watts != 0 and not 15 <= watts <= ceiling:
            raise ValueError(f"{which} {watts}W out of range (0, or 15-{ceiling})")

        with self._lock:
            value = watts
            if idx == 2 and self.has_double_pl4():
                value = watts // 2
            self.write_verify(REG_TDP_BASE + idx, value)

    # -- battery -----------------------------------------------------------

    def get_charge_cycles(self) -> int:
        """True cycle count. sysfs cycle_count reports 0 on this machine."""
        return self.read(REG_CYCLE_LO) | (self.read(REG_CYCLE_HI) << 8)
