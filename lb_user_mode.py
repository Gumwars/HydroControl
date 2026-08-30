#!/usr/bin/env python3
"""
lb_user_mode.py — Static colour via the REAL ITE protocol, ported to the 8233.

The 8291 keyboard protocol (ite8291r3 lib) is:
    08 02 XX ...        SET_EFFECT  (XX = effect id; 0x33 = user mode = static)
    09 02 XX            SET_BRIGHTNESS
    14 00 idx R G B     SET_PALETTE_COLOR
    16 00 row           SET_ROW_INDEX
    <64-byte data>      row colour buffer on the interrupt OUT / OUTPUT report
    1A 00 01 04 00 00 00 01   commit

Earlier lightbar probes misread 0x16 as an effect selector (it is row index)
and never tried effect 0x33 + a colour-buffer write. That is what this does:
for every row index 0..6, select user mode and write a solid RED buffer.

The 8233 OUTPUT report is 64 bytes = [0x00, B*21, G*21, R*21] (1+3*21).

Observe the bar and answer:
    c = still colour-cycling     s = static / solid colour (ANY colour)
    o = off / dark               ? = something else (describe)
    q = quit

Run: sudo python3 lb_user_mode.py
Logs to lb_user_mode_log.json
"""

import array
import fcntl
import glob
import json
import os
import time

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
LOG = "/home/gumwars/HydroControl/lb_user_mode_log.json"

N_LEDS = 21  # matches the keyboard's 21 columns; 1 + 3*21 = 64 bytes

CODES = {
    "c": "still colour-cycling",
    "s": "STATIC / solid colour",
    "o": "off / dark",
}

RED = (0xFF, 0x00, 0x00)


def find_lightbar() -> str:
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            blob = open(os.path.join(p, "device", "uevent")).read().upper()
        except OSError:
            continue
        if "0000048D:00007001" in blob:
            return "/dev/" + os.path.basename(p)
    raise SystemExit("ITE 8233 lightbar (048d:7001) not found")


def feature(dev: str, pkt8: bytes) -> str:
    report = bytes([0x00]) + bytes(pkt8[:8]).ljust(8, b"\x00")
    try:
        fd = os.open(dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE_9, array.array("B", report), True)
        finally:
            os.close(fd)
        return "ok"
    except OSError as e:
        return f"FAILED: {e}"


def send_output(dev: str, payload64: bytes) -> str:
    """Write a 64-byte OUTPUT report. Device has no report IDs, so no prefix."""
    buf = bytes(payload64[:64]).ljust(64, b"\x00")
    fd = os.open(dev, os.O_RDWR)
    try:
        n = os.write(fd, buf)
        return f"ok ({n} bytes)"
    except OSError as e:
        return f"FAILED: {e}"
    finally:
        os.close(fd)


def enable(dev: str) -> None:
    feature(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    feature(dev, LB_COMMIT)


def row_buffer(rgb, n=N_LEDS) -> bytes:
    """[0x00, B*n, G*n, R*n] -- the 8291 row layout, 64 bytes for n=21."""
    r, g, b = rgb
    return bytes([0x00]) + bytes([b]) * n + bytes([g]) * n + bytes([r]) * n


def static_red(dev: str, row: int, commit: bool, brightness: int) -> str:
    """User mode -> row index -> solid RED buffer. Returns status string."""
    feature(dev, bytes([0x08, 0x02, 0x33, 0x00, brightness, 0x00, 0x00, 0x00]))
    feature(dev, bytes([0x16, 0x00, row, 0, 0, 0, 0, 0]))
    st = send_output(dev, row_buffer(RED))
    if commit:
        feature(dev, LB_COMMIT)
    return st


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_user_mode.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    enable(dev)
    print("Enabled (09 02 32) -- you should see the colour cycle.\n")
    print("For each row index: user-mode(0x33) + 64-byte RED buffer.")
    print("  s = STATIC colour (hit)   c = still cycling   o = off/dark   q = quit\n")

    results = []
    for row in range(0, 7):
        for commit in (True, False):
            for brightness in (50, 25):
                label = f"row{row}_commit{int(commit)}_bri{brightness}"
                st = static_red(dev, row, commit, brightness)
                time.sleep(1.2)
                try:
                    obs = input(f"  {label}  [{st}]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    obs = "aborted"
                if obs in ("q", "aborted"):
                    results.append({"label": label, "result": obs, "status": st})
                    break
                results.append({
                    "label": label,
                    "row": row,
                    "commit": commit,
                    "brightness": brightness,
                    "status": st,
                    "result": CODES.get(obs, obs or "no change"),
                })
                if obs == "s":
                    print("\n  *** HIT: static colour on this row. Stopping. ***")
                    enable(dev)
                    dump(results)
                    return
            else:
                continue
            break
        else:
            continue
        break

    enable(dev)  # restore known-good state
    dump(results)


def dump(results: list) -> None:
    existing = []
    if os.path.exists(LOG):
        try:
            existing = json.load(open(LOG))
        except (OSError, ValueError):
            pass
    existing.extend(results)
    with open(LOG, "w") as fh:
        json.dump(existing, fh, indent=2)

    print("\n" + "=" * 60)
    for r in results:
        mark = " <-- HIT" if "STATIC" in r["result"] else ""
        print(f"  {r['label']:24s} {r['result']}{mark}")
    print("=" * 60)
    print(f"\nLogged to {LOG}")

    hits = [r for r in results if "STATIC" in r["result"]]
    if hits:
        print(f"\nWorking: {', '.join(h['label'] for h in hits)}")
    else:
        print("\nNo static hit yet. Next options:")
        print("  - vary N_LEDS (bar may have <21 LEDs; try 7/9/14)")
        print("  - try the data write BEFORE the row index")
        print("  - decompile SystrayComponent.exe / Windows capture for truth")


if __name__ == "__main__":
    main()