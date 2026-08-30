#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Summarise battery_watch.py runs: segment into contiguous charge/discharge
runs, find each profile's real ceiling, and coulomb-count to detect masking.

Why segmentation matters
------------------------
battery-run.csv is APPENDED across sessions, so it interleaves discharges,
charges, and mode switches. Grouping rows by profile alone mixes
non-contiguous runs and produces nonsense (negative deltas, phantom peaks).
Segments are split whenever profile OR status changes.

Masking test
------------
Integrating current_now over time gives the mAh physically delivered.
Compare against the charge_now delta:

    ratio ~ 1.0-1.1   honest gauge (>1 is normal charging inefficiency)
    ratio < 0.85      EC is inflating charge_now -- the mask
    ratio > 1.15      EC under-reporting

Usage:
    python3 battery_summary.py [battery-run.csv]
"""

import csv
import sys
from datetime import datetime

PROF = {"0": "Standard", "1": "Long_Life", "2": "Trickle"}
DESIGN_MAH = 6400


def num(r, k):
    v = (r.get(k) or "").strip()
    try:
        return int(v)
    except ValueError:
        return None


def flt(r, k):
    v = (r.get(k) or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def ts(r):
    return datetime.strptime(r["time"], "%H:%M:%S")


def elapsed(a, b):
    d = (ts(b) - ts(a)).total_seconds()
    return d + 86400 if d < 0 else d


# A gap larger than this many seconds means the watcher was not running
# (reboot, suspend, manual restart). Integrating current across such a gap
# would invent charge that was never measured, so we split there.
MAX_GAP_S = 120


def segment(rows):
    """Split into contiguous runs of identical (profile, status).

    Also splits on a sampling gap: battery-run.csv is appended across
    sessions, so a reboot can leave two unrelated runs adjacent with the
    same profile and status. Merging them would corrupt the coulomb count.
    """
    segs, cur, key, prev = [], [], None, None
    for r in rows:
        k = ((r.get("profile") or "?").strip(), (r.get("status") or "").strip())
        gap = elapsed(prev, r) if prev is not None else 0

        if key is not None and (k != key or gap > MAX_GAP_S):
            segs.append((key, cur))
            cur = []
        key = k
        cur.append(r)
        prev = r
    if cur:
        segs.append((key, cur))
    return segs


def coulomb(rs):
    """mAh delivered, by trapezoidal integration of current_now."""
    total = 0.0
    for a, b in zip(rs, rs[1:]):
        dt = elapsed(a, b)
        ia, ib = num(a, "current_ma") or 0, num(b, "current_ma") or 0
        total += (ia + ib) / 2 * (dt / 3600.0)
    return total


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "battery-run.csv"
    try:
        rows = [r for r in csv.DictReader(open(path)) if num(r, "charge_now_mah") is not None]
    except OSError as e:
        raise SystemExit(f"cannot read {path}: {e}")
    if len(rows) < 2:
        raise SystemExit(f"{path}: not enough samples yet")

    segs = [(k, rs) for k, rs in segment(rows) if len(rs) >= 3]
    print(f"{len(rows)} samples, {len(segs)} segments\n")

    for (prof, status), rs in segs:
        a, b = rs[0], rs[-1]
        print(f"[{PROF.get(prof, prof):9}] {status:11} {a['time']}->{b['time']} "
              f"{elapsed(a, b)/60:5.1f}min  "
              f"{num(a,'charge_now_mah')}->{num(b,'charge_now_mah')} mAh "
              f"({a.get('capacity')}%->{b.get('capacity')}%)")

    print("\n--- charging runs ---")
    for (prof, status), rs in segs:
        if status != "Charging":
            continue
        a, b = rs[0], rs[-1]
        rep = num(b, "charge_now_mah") - num(a, "charge_now_mah")
        got = coulomb(rs)
        peak = num(b, "charge_now_mah")
        stopped = (num(b, "current_ma") or 0) == 0

        print(f"\n{PROF.get(prof, prof)}  ({elapsed(a,b)/60:.1f} min)")
        print(f"  charge_now   {num(a,'charge_now_mah')} -> {peak} mAh "
              f"({peak/DESIGN_MAH*100:.1f}% of design)")
        print(f"  reported     {rep} mAh")
        print(f"  delivered    {got:.0f} mAh")
        # charge_now quantises in ~64 mAh steps, so short segments produce wild
        # ratios from rounding alone. Only judge runs with enough signal.
        MIN_MINUTES, MIN_MAH = 5.0, 200
        long_enough = elapsed(a, b) / 60 >= MIN_MINUTES and abs(rep) >= MIN_MAH

        if rep and long_enough:
            ratio = got / rep
            print(f"  ratio        {ratio:.3f}", end="  ")
            if ratio < 0.85:
                print("<<< MASK: EC inflating charge_now")
            elif ratio > 1.15:
                print("<<< EC under-reporting")
            else:
                print("(honest gauge)")
        elif rep:
            print(f"  ratio        {got/rep:.3f}  (too short to judge — "
                  f"need >{MIN_MINUTES:.0f} min and >{MIN_MAH} mAh)")

        # Voltage under charge current is the CHARGER's applied voltage, not the
        # cells' OCV -- measured 827 mV above OCV at 2.1 A on a pack whose
        # discharge-side resistance is only ~8 mOhm. Only zero-current readings
        # reflect the battery, so the charge-voltage question is answered by the
        # settled Full segments below, never here.
        vend = flt(b, "v_per_cell")
        iend = num(b, "current_ma") or 0
        if vend is not None:
            if iend == 0:
                print(f"  V/cell end   {vend:.3f} V (zero current - valid OCV)")
            else:
                print(f"  V/cell end   {vend:.3f} V at {iend} mA "
                      f"- CHARGER voltage, not OCV; ignore")

        if stopped:
            print(f"  CEILING      charging stopped at {peak} mAh "
                  f"= {peak/DESIGN_MAH*100:.1f}% of design")
            try:
                if int(b.get("capacity")) == 100 and peak / DESIGN_MAH < 0.97:
                    print(f"  >>> MASKED: reports 100% at {peak/DESIGN_MAH*100:.1f}% actual")
            except (ValueError, TypeError):
                pass
        else:
            print(f"  still charging at {num(b,'current_ma')} mA")

    # Terminal voltage per profile while Full. Charge voltage is the dominant
    # lithium-ion longevity factor, so if the profiles differ at all, this is
    # the most likely place -- and it is invisible to any capacity-based test.
    rested = {}
    for (prof, status), rs in segs:
        if status != "Full":
            continue
        # Zero-current samples only: anything else is the charger's voltage.
        vs = [flt(r, "v_per_cell") for r in rs if (num(r, "current_ma") or 0) == 0]
        vs = [v for v in vs if v is not None]
        if vs:
            rested.setdefault(prof, []).extend(vs)

    if rested:
        print("\n--- terminal voltage at Full, by profile ---")
        for prof, vs in sorted(rested.items()):
            print(f"  {PROF.get(prof, prof):10} max {max(vs):.3f} V/cell   "
                  f"settled {vs[-1]:.3f} V/cell   ({len(vs)} samples)")
        if len(rested) > 1:
            spread = max(max(v) for v in rested.values()) - min(max(v) for v in rested.values())
            print(f"  spread across profiles: {spread:.3f} V/cell", end="  ")
            print("<<< profiles DO differ by charge voltage" if spread > 0.02
                  else "(no meaningful difference)")

    fulls = {num(r, "charge_full_mah") for r in rows} - {None}
    if len(fulls) > 1:
        print(f"\ncharge_full VARIED across run: {sorted(fulls)} mAh "
              f"<-- masking via charge_full")


if __name__ == "__main__":
    main()
