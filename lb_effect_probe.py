#!/usr/bin/env python3
"""
Stage A: find a lightbar effect ID that stops the firmware colour-cycle.

Rationale
---------
Control Center's saved state (RGBKeyboard.reg) models the chin bar as 7 colour
blocks plus a separate `save_effect` selector. Every colour probe so far was
sent while the firmware colour-cycle was running, which an active animation
will simply ignore.

On the sibling ITE 8291 keyboard, command family 0x16 is the effect selector
(IDs ~0x00-0x15). This sweeps that family on the 8233 lightbar looking for an
effect that halts the cycle -- a static/user-controlled mode. Once found,
Stage B can write colour into it.

Answer each prompt with a single letter:
    c = still colour-cycling      s = static / solid colour
    o = off / dark               ?  = something else (then describe)
    q = quit

Run: sudo python3 lb_effect_probe.py
Logs to lb_effect_probe_log.json
"""

import array
import fcntl
import glob
import json
import os
import time

HIDIOCSFEATURE_9 = 0xC0094806
LB_COMMIT = bytes([0x1A, 0x00, 0x01, 0x04, 0x00, 0x00, 0x00, 0x01])
LOG = "/home/gumwars/HydroControl/lb_effect_probe_log.json"

CODES = {
    "c": "still colour-cycling",
    "s": "STATIC / solid colour",
    "o": "off / dark",
}


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


def enable(dev: str) -> None:
    feature(dev, bytes([0x09, 0x02, 50, 0, 0, 0, 0, 0]))
    feature(dev, LB_COMMIT)


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("must run as root (sudo python3 lb_effect_probe.py)")

    dev = find_lightbar()
    print(f"ITE 8233 lightbar: {dev}")
    print("Enabling bar (09 02 32) -- you should see the usual colour cycle.\n")
    enable(dev)
    time.sleep(1.5)

    print("Sweeping effect selector family 0x16, IDs 0x00-0x15.")
    print("Looking for ANY id that stops the cycle on a solid colour.\n")
    print("  c = cycling   s = static   o = off   ? = other   q = quit\n")

    results = []
    for eff in range(0x00, 0x16):
        status = feature(dev, bytes([0x16, eff, 0, 0, 0, 0, 0, 0]))
        feature(dev, LB_COMMIT)
        time.sleep(1.2)

        try:
            obs = input(f"  effect 0x{eff:02X} [{status}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            break

        if obs == "q":
            break

        note = CODES.get(obs, obs or "no change")
        if obs == "?":
            try:
                note = input("      describe: ").strip() or "other"
            except (EOFError, KeyboardInterrupt):
                break

        results.append({
            "effect_id": f"0x{eff:02X}",
            "packet": f"16 {eff:02X} 00 00 00 00 00 00",
            "write_status": status,
            "result": note,
        })

    # Restore the known-good cycling state.
    enable(dev)

    existing = []
    if os.path.exists(LOG):
        try:
            existing = json.load(open(LOG))
        except (OSError, ValueError):
            pass
    existing.extend(results)
    with open(LOG, "w") as fh:
        json.dump(existing, fh, indent=2)

    print("\n" + "=" * 56)
    hits = [r for r in results if "STATIC" in r["result"] or "off" not in r["result"]
            and "cycling" not in r["result"] and "no change" not in r["result"]]
    for r in results:
        mark = " <-- HIT" if "STATIC" in r["result"] else ""
        print(f"  {r['effect_id']}  {r['result']}{mark}")
    print("=" * 56)
    print(f"\nLogged to {LOG}")

    statics = [r["effect_id"] for r in results if "STATIC" in r["result"]]
    if statics:
        print(f"\nStatic-capable effect id(s): {', '.join(statics)}")
        print("Stage B: re-run colour probes with that effect selected first.")
    else:
        print("\nNo effect id produced a static colour. The 0x16 family may not")
        print("be the lightbar's effect selector -- next step is a USB capture")
        print("on Windows to get the real command bytes.")


if __name__ == "__main__":
    main()
