#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
gpu_mode.py -- read or change the firmware GPU mode, from a terminal.

The package has no CLI for this and the UI route is the only other way in,
so this stays. What it no longer does is implement any of it.

WHY IT WAS REWRITTEN

It carried its own copy of the EFI variable handling and its own idea of when
a write is allowed, and the two had drifted:

    gpu_mode.py        if n and not accept_risks:  refuse
    hydroc.gpumode     if not confirm:             refuse, always

So a mode change whose preflight came back clean was written straight to
non-volatile firmware with nothing asked. The package requires confirm=True
whatever preflight says, and its docstring explains why the two gates are
separate: confirm stops an accidental call reaching firmware at all,
acknowledge_risks stops a deliberate one going through when the machine will
plausibly come back without a desktop. Only the second was implemented here,
and only sometimes.

    sudo python3 gpu_mode.py                      # show current mode
    sudo python3 gpu_mode.py --set dynamic        # asks before writing
    sudo python3 gpu_mode.py --set igpu --dry-run
    sudo python3 gpu_mode.py --set igpu --yes --accept-risks

This takes effect at the NEXT BOOT and a power cycle will not undo it. If the
machine comes back with no display, the way out is the BIOS.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.gpumode import (                                     # noqa: E402
    MODES, GpuModeError, available, preflight, set_mode, status)


def show_status() -> int:
    st = status()
    if not st.get("supported"):
        print("this machine has no GPU mode firmware variables")
        return 1
    if st.get("error"):
        print(f"cannot read: {st['error']}")
        return 1
    print(f"mode          {st.get('mode')}")
    print(f"igpu present  {st.get('igpu_present')}")
    print(f"dgpu present  {st.get('dgpu_present')}")
    for mode in sorted(MODES):
        risks = preflight(mode) or []
        mark = "ok" if not risks else f"{len(risks)} risk(s)"
        print(f"  -> {mode:<10} {mark}")
        for r in risks:
            print(f"       {r.get('detail') or r}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", metavar="MODE", choices=sorted(MODES),
                    help=f"one of {', '.join(sorted(MODES))}")
    ap.add_argument("--dry-run", action="store_true",
                    help="show the risks and write nothing")
    ap.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation")
    ap.add_argument("--accept-risks", action="store_true",
                    help="proceed even when preflight found something")
    args = ap.parse_args(argv)

    if os.geteuid() != 0:
        print("needs root: sudo python3 gpu_mode.py ...")
        return 1
    if not available():
        print("this machine has no GPU mode firmware variables")
        return 1
    if not args.set:
        return show_status()

    risks = preflight(args.set) or []
    if risks:
        print(f"\n  !!! {len(risks)} thing(s) could leave this machine "
              f"without a desktop:\n")
        for r in risks:
            print(f"   - {r.get('detail') or r}")
        print()
    if args.dry_run:
        print("[dry-run] nothing written.")
        return 0

    # The gate the package insists on, surfaced here rather than passed
    # through silently. --yes is the non-interactive form; there is no path
    # that writes without one of the two.
    if not args.yes:
        print(f"About to set GPU mode to {args.set!r}.")
        print("This writes non-volatile firmware, takes effect at the next "
              "boot, and a power cycle will not undo it.")
        try:
            if input("Type the mode name to confirm: ").strip() != args.set:
                print("not confirmed; nothing written.")
                return 2
        except (EOFError, KeyboardInterrupt):
            print("\nnot confirmed; nothing written.")
            return 2

    try:
        result = set_mode(args.set, confirm=True,
                          acknowledge_risks=args.accept_risks)
    except GpuModeError as e:
        print(f"refused: {e}")
        return 2

    print(result.get("message") or f"set to {args.set}")
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
