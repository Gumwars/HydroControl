#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
efivar_capture.py — snapshot every EFI variable, and diff two snapshots.

For finding where a firmware setting actually lives. The GPU mux on this chassis
has no ACPI path -- no MXDS/MXMX/GMUX, no NVIDIA NBCI/NVOP _DSM -- so the mode is
almost certainly a firmware variable applied at POST. UniWillVariable is the
obvious candidate (it carries PROJECT_ID 0x19), but "obvious candidate" is how
0x0984 got read as a hardware interlock when it was unmapped space. So this
captures ALL of them and lets the diff say which one moved.

Read-only by construction: it opens files for reading and never writes to
/sys/firmware/efi/efivars. It cannot change firmware state.

    sudo python3 efivar_capture.py before.json
    # ... reboot, change GPU mode in the BIOS, boot back ...
    sudo python3 efivar_capture.py after.json
    python3 efivar_capture.py --diff before.json after.json

WRITING these is a different matter entirely and is NOT what this tool does.
Everything else in this project is volatile EC RAM where a power cycle restores
factory defaults; that backstop does not exist here. A bad write to a vendor
setup variable can leave a machine that will not POST, and recovery is a CMOS
reset rather than a power cycle.
"""

import argparse
import json
import os
import sys

EFIVARS = "/sys/firmware/efi/efivars"


def capture() -> dict:
    out = {}
    for name in sorted(os.listdir(EFIVARS)):
        path = os.path.join(EFIVARS, name)
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as e:
            out[name] = {"error": str(e)}
            continue
        # First four bytes are the EFI attribute word, not data.
        out[name] = {"attrs": int.from_bytes(raw[:4], "little") if len(raw) >= 4 else None,
                     "size": max(0, len(raw) - 4),
                     "hex": raw[4:].hex()}
    return out


def _fmt(a: str, b: str) -> list[str]:
    """Byte offsets that differ, with both values -- the whole point."""
    ab, bb = bytes.fromhex(a or ""), bytes.fromhex(b or "")
    lines = []
    for i in range(max(len(ab), len(bb))):
        x = ab[i] if i < len(ab) else None
        y = bb[i] if i < len(bb) else None
        if x != y:
            lines.append(f"      offset 0x{i:04X}: "
                         f"{'--' if x is None else f'0x{x:02X}'} -> "
                         f"{'--' if y is None else f'0x{y:02X}'}")
    return lines


def diff(before: dict, after: dict) -> int:
    changed = 0
    for name in sorted(set(before) | set(after)):
        a, b = before.get(name), after.get(name)
        if a is None:
            print(f"  + {name}  (new)")
            changed += 1
        elif b is None:
            print(f"  - {name}  (gone)")
            changed += 1
        elif a.get("hex") != b.get("hex"):
            lines = _fmt(a.get("hex", ""), b.get("hex", ""))
            print(f"  ~ {name}  ({len(lines)} byte(s) differ)")
            for ln in lines[:24]:
                print(ln)
            if len(lines) > 24:
                print(f"      ... {len(lines)-24} more")
            changed += 1
    if not changed:
        print("  no variable changed.")
        print("  If the BIOS setting really was applied, the mode is not stored")
        print("  in an EFI variable at all -- which would be a real finding, and")
        print("  would point at the EC or a hidden setup store instead.")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="write a snapshot here")
    ap.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()

    if args.diff:
        with open(args.diff[0]) as f1, open(args.diff[1]) as f2:
            return 0 if diff(json.load(f1), json.load(f2)) >= 0 else 1

    if not args.path:
        ap.print_help()
        return 64
    if not os.path.isdir(EFIVARS):
        raise SystemExit(f"{EFIVARS} not present -- is this a UEFI boot?")
    if os.geteuid() != 0:
        raise SystemExit("must run as root to read most variables")

    snap = capture()
    with open(args.path, "w") as fh:
        json.dump(snap, fh, indent=1)
    unreadable = sum(1 for v in snap.values() if "error" in v)
    print(f"captured {len(snap)} variables to {args.path}"
          + (f" ({unreadable} unreadable)" if unreadable else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
