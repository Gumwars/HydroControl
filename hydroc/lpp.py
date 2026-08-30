# SPDX-License-Identifier: MIT
"""
hydroc.lpp — protocol for the Eluktronics LPP (Liquid Propulsion Package).

The LPP is an external liquid-cooling dock, and it is the one surface in this
project that is not part of the laptop: no EC register reaches it, no sysfs
node describes it. It speaks Bluetooth LE, advertises as `CoolingSystem`, and
carries 8-byte frames over the Nordic UART Service.

    TX (write)   6e400002-b5a3-f393-e0a9-e50e24dcca9e
    RX (notify)  6e400003-b5a3-f393-e0a9-e50e24dcca9e

Every frame is 8 bytes, `FE <cmd> ... EF`:

    FE 1B 01 <speed>  00 00 00 EF     fan speed, 0-100 decimal
    FE 1C 01 3C <mode> 00 00 EF       pump mode
    FE 1E 01 00 B8 FF 00 EF           init, purpose unknown
    FE 33 00 00 00 00 00 EF           status poll / keepalive

Protocol credit: the frame layout, service UUIDs and pump-mode encoding were
established by LibreLPP (https://github.com/Sebastian-Alexis/LibreLPP, MIT,
Copyright (c) 2024 sra). This module is an independent implementation against
those facts rather than a copy of that code, but the knowledge is theirs and
this project would not have had it otherwise.

This module is pure: it builds and parses frames and touches no transport, so
it can be exercised without a dock present. The BLE connection lives in
`hydroc.lppd`.
"""

from __future__ import annotations

# Control Center advertises the full name as "CoolingSystem LCT21001" --
# LCT21001 looks like the pump model. Matched as a substring so both the short
# and full forms work.
DEVICE_NAME = "CoolingSystem"
DEVICE_NAME_FULL = "CoolingSystem LCT21001"

NUS_TX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

FRAME_START = 0xFE
FRAME_END = 0xEF
FRAME_LEN = 8

CMD_FAN = 0x1B
CMD_PUMP = 0x1C
CMD_INIT_UNKNOWN = 0x1E
CMD_STATUS = 0x33

FAN_MIN, FAN_MAX = 0, 100

# The pump codes are NOT in speed order -- 0 is High, not Low. Reading them as
# an ordered scale puts the dock into the wrong mode with no error, since the
# device accepts any byte here. Presented low-to-high for the UI.
PUMP_MODES = {
    "low":    2,
    "medium": 3,
    "high":   0,
    "max":    1,
}
PUMP_MODES_REV = {v: k for k, v in PUMP_MODES.items()}
PUMP_ORDER = ["low", "medium", "high", "max"]


def _frame(cmd: int, *payload: int) -> bytes:
    """FE <cmd> <5 payload bytes> EF, zero-padded."""
    body = list(payload)[:5] + [0] * (5 - len(payload))
    return bytes([FRAME_START, cmd, *body, FRAME_END])


def fan_frame(speed: int) -> bytes:
    """Fan duty, 0-100 percent."""
    speed = int(speed)
    if not FAN_MIN <= speed <= FAN_MAX:
        raise ValueError(f"fan speed {speed} out of range ({FAN_MIN}-{FAN_MAX})")
    return _frame(CMD_FAN, 0x01, speed)


def pump_frame(mode: str | int) -> bytes:
    """Pump mode, by name ('low'..'max') or by raw device code."""
    if isinstance(mode, str):
        if mode not in PUMP_MODES:
            raise ValueError(f"unknown pump mode {mode!r} "
                             f"(want one of {', '.join(PUMP_ORDER)})")
        code = PUMP_MODES[mode]
    else:
        code = int(mode)
        if code not in PUMP_MODES_REV:
            raise ValueError(f"unknown pump code {code}")
    return _frame(CMD_PUMP, 0x01, 0x3C, code)


def status_frame() -> bytes:
    """Status poll. Also serves as the keepalive."""
    return _frame(CMD_STATUS)


# The dock needs this before it will accept anything else, and it needs it
# twice -- the first pass is unreliable on a cold connection. The trailing
# b"sw" is not a framed command and is sent verbatim.
INIT_SEQUENCE: tuple[bytes, ...] = (
    _frame(CMD_PUMP, 0x01, 0x3C, PUMP_MODES["medium"]),
    _frame(CMD_FAN, 0x01, 0x3C),
    _frame(CMD_INIT_UNKNOWN, 0x01, 0x00, 0xB8, 0xFF),
    status_frame(),
)
INIT_TRAILER = b"sw"


def describe(frame: bytes) -> str:
    """Human-readable form of an outgoing frame, for logs."""
    if len(frame) != FRAME_LEN or frame[0] != FRAME_START:
        return frame.hex(" ")
    cmd = frame[1]
    if cmd == CMD_FAN:
        return f"fan {frame[3]}%"
    if cmd == CMD_PUMP:
        return f"pump {PUMP_MODES_REV.get(frame[4], frame[4])}"
    if cmd == CMD_STATUS:
        return "status"
    return frame.hex(" ")
