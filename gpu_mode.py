#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
gpu_mode.py — read, and optionally set, the firmware GPU mode.

The mode is one byte mirrored in two EFI variables, applied by the BIOS at POST
(DESIGN.md §7.7). There is no ACPI method and no runtime switch: a reboot is
required however it is set, including from Windows.

    mode        UniWillVariable[0x62]   TpvSetup[0x01]
    igpu                         0x01             0x01
    dgpu                         0x02             0x02
    dynamic                      0x04             0x04

All three values were captured from the firmware's own writes by diffing every
EFI variable across the three BIOS modes. This tool will only ever write one of
them; it never invents a value.

WHY THIS IS DIFFERENT FROM EVERYTHING ELSE HERE. The rule that makes EC
experimentation safe -- everything is volatile, a full power cycle restores
factory defaults -- does NOT apply to EFI variables. They are non-volatile and a
power cycle will not undo a change. Mitigating: there is no checksum over
UniWillVariable (its trailing 0x55 is identical in all three captures, so the one
byte moves alone), and the BIOS menu can always set the mode back.

    sudo python3 gpu_mode.py                    # status, read-only
    sudo python3 gpu_mode.py --self-test        # prove the write path, no change
    sudo python3 gpu_mode.py --set dynamic --dry-run
    sudo python3 gpu_mode.py --set igpu
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime

EFIVARS = "/sys/firmware/efi/efivars"

# name-prefix -> (byte offset into the DATA, expected data length)
VARS = {
    "UniWillVariable-9f33f85c-13ca-4fd1-9c4a-96217722c593": (0x62, 180),
    "TpvSetup-1c3483d5-1e7e-4450-9806-dede002c974b":        (0x01, 11),
}
MODES = {"igpu": 0x01, "dgpu": 0x02, "dynamic": 0x04}
MODE_NAMES = {v: k for k, v in MODES.items()}
DESC = {
    "igpu":    "iGPU only  — dGPU removed; EXTERNAL DISPLAYS STOP WORKING",
    "dgpu":    "dGPU only  — Intel GPU leaves the PCI bus; no PRIME, no Intel VAAPI",
    "dynamic": "Dynamic    — both GPUs; panel on Intel; externals work",
}


class GpuModeError(RuntimeError):
    pass


def _path(name):
    return os.path.join(EFIVARS, name)


def read_var(name):
    """(attrs:int, data:bytes). The first four bytes are the EFI attribute word."""
    with open(_path(name), "rb") as fh:
        raw = fh.read()
    if len(raw) < 5:
        raise GpuModeError(f"{name}: only {len(raw)} bytes")
    return int.from_bytes(raw[:4], "little"), raw[4:]


def write_var(name, attrs, data):
    """Attributes and data must reach the kernel in ONE write; efivarfs rejects
    a partial one. The immutable flag is set by efivarfs on variables it does
    not recognise, so clear it first."""
    path = _path(name)
    subprocess.run(["chattr", "-i", path], capture_output=True)
    buf = attrs.to_bytes(4, "little") + data
    fd = os.open(path, os.O_WRONLY)
    try:
        n = os.write(fd, buf)
    finally:
        os.close(fd)
    if n != len(buf):
        raise GpuModeError(f"{name}: short write ({n}/{len(buf)})")


def current():
    """{varname: (mode_byte, attrs, data)} plus a consistency check."""
    out = {}
    for name, (off, size) in VARS.items():
        if not os.path.exists(_path(name)):
            raise GpuModeError(f"{name} not present -- is this the right machine?")
        attrs, data = read_var(name)
        if len(data) != size:
            raise GpuModeError(f"{name}: expected {size} data bytes, got {len(data)}"
                               " -- refusing to touch a structure I do not recognise")
        out[name] = (data[off], attrs, data)
    return out


def topology():
    """What the kernel can actually see -- the observable consequence."""
    have = {}
    for pci, label in (("0000:00:02.0", "Intel iGPU"), ("0000:01:00.0", "NVIDIA dGPU")):
        have[label] = os.path.exists(f"/sys/bus/pci/devices/{pci}")
    panels = [os.path.basename(p) for p in glob.glob("/sys/class/drm/card*-eDP-*")
              if open(os.path.join(p, "status")).read().strip() == "connected"]
    return have, panels


def show_status():
    cur = current()
    vals = {v[0] for v in cur.values()}
    print(f"{'variable':22} {'offset':>7} {'value':>7}  mode")
    print("-" * 58)
    for name, (byte, _, _) in cur.items():
        off = VARS[name][0]
        print(f"{name.split('-')[0]:22} {'0x%02X'%off:>7} {'0x%02X'%byte:>7}  "
              f"{MODE_NAMES.get(byte, 'UNKNOWN')}")

    print()
    if len(vals) != 1:
        print("  !! THE TWO VARIABLES DISAGREE. Firmware state is inconsistent;")
        print("     set the mode once in the BIOS to resynchronise before writing.")
        return 1
    byte = vals.pop()
    if byte not in MODE_NAMES:
        print(f"  !! value 0x{byte:02X} is not one of the three the firmware writes.")
        print("     Refusing to interpret it. Set the mode in the BIOS.")
        return 1
    print(f"  mode: {DESC[MODE_NAMES[byte]]}")

    have, panels = topology()
    print(f"\n  live: " + ", ".join(f"{k}={'present' if v else 'ABSENT'}"
                                    for k, v in have.items()))
    print(f"        panel on {', '.join(panels) or 'nothing connected'}")
    expect = {"igpu": (True, False), "dgpu": (False, True), "dynamic": (True, True)}
    want = expect[MODE_NAMES[byte]]
    got = (have["Intel iGPU"], have["NVIDIA dGPU"])
    if got != want:
        print("  note: the live topology does not match the stored mode -- a mode")
        print("        change has been written but not yet applied. Reboot.")
    return 0


def self_test():
    """Write each variable's CURRENT bytes back unchanged, then re-read.

    Exercises the whole write path -- immutable flag, attribute word, single
    write, readback -- while changing nothing at all. If this fails, the write
    mechanism is wrong and no real change should be attempted.
    """
    cur = current()
    print("writing each variable's current contents back unchanged ...\n")
    ok = True
    for name, (byte, attrs, data) in cur.items():
        short = name.split("-")[0]
        try:
            write_var(name, attrs, data)
        except (OSError, GpuModeError) as e:
            print(f"  {short:22} WRITE FAILED: {e}")
            ok = False
            continue
        a2, d2 = read_var(name)
        same = (a2 == attrs and d2 == data)
        print(f"  {short:22} {'unchanged, readback identical' if same else 'CHANGED — investigate'}")
        ok &= same
    print("\n" + ("write path works, and nothing was modified."
                  if ok else "write path is NOT sound — do not attempt a real change."))
    return 0 if ok else 1


def set_mode(mode, dry_run):
    if mode not in MODES:
        raise GpuModeError(f"unknown mode {mode!r}; want one of {', '.join(MODES)}")
    target = MODES[mode]
    cur = current()
    vals = {v[0] for v in cur.values()}
    if len(vals) != 1:
        raise GpuModeError("the two variables disagree; resynchronise in the BIOS first")
    now = vals.pop()
    if now not in MODE_NAMES:
        raise GpuModeError(f"current value 0x{now:02X} is not a known mode; "
                           "refusing to overwrite a structure I cannot interpret")
    if now == target:
        print(f"already {mode} (0x{target:02X}); nothing to do.")
        return 0

    backup = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          f"gpu-mode-backup-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(backup, "w") as fh:
        json.dump({n: {"attrs": a, "hex": d.hex()} for n, (_, a, d) in cur.items()},
                  fh, indent=1)

    print(f"{MODE_NAMES[now]} (0x{now:02X})  ->  {mode} (0x{target:02X})")
    print(f"  {DESC[mode]}")
    print(f"  backup: {backup}")
    for name, (_, _, data) in cur.items():
        off = VARS[name][0]
        print(f"  {name.split('-')[0]:22} byte 0x{off:02X}: "
              f"0x{data[off]:02X} -> 0x{target:02X}")
    if dry_run:
        print("\n[dry-run] nothing written.")
        return 0

    for name, (_, attrs, data) in cur.items():
        off = VARS[name][0]
        new = bytearray(data)
        new[off] = target
        write_var(name, attrs, bytes(new))

    after = current()
    bad = [n for n, (b, _, _) in after.items() if b != target]
    if bad:
        print("\n  !! not all variables took the new value: "
              + ", ".join(n.split('-')[0] for n in bad))
        print("     Firmware state may be inconsistent. Set the mode in the BIOS.")
        return 1
    print("\nboth variables now read "
          f"0x{target:02X}. The BIOS applies this at POST -- REBOOT to take effect.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", metavar="MODE", choices=sorted(MODES))
    ap.add_argument("--self-test", action="store_true",
                    help="prove the write path without changing anything")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(EFIVARS):
        raise SystemExit("no efivarfs -- is this a UEFI boot?")
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    try:
        if args.self_test:
            return self_test()
        if args.set:
            return set_mode(args.set, args.dry_run)
        return show_status()
    except GpuModeError as e:
        raise SystemExit(f"refusing: {e}")


if __name__ == "__main__":
    sys.exit(main())
