#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
fan_curve.py — read/write 16-point fan curve tables from EC registers.

Register layout (FAN_TABLE_LENGTH = 16):
  0x0F00-0x0F0F  CPU temp start (DownT)   — temp to ramp DOWN
  0x0F10-0x0F1F  CPU temp end   (UpT)     — temp to ramp UP
  0x0F20-0x0F2F  CPU fan speed  (Duty)    — 0-200 (PWM_MAX), = Duty% * 2
  0x0F30-0x0F3F  GPU temp start (DownT)
  0x0F40-0x0F4F  GPU temp end   (UpT)
  0x0F50-0x0F5F  GPU fan speed  (Duty)

Vendor JSON format: {"CPU": [...], "GPU": [...]} with UpT/DownT/Duty (0-100)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hydroc.ec import EC, ECUnavailable, ECWriteRejected

TABLE_BASE = 0x0F00
TABLE_LEN = 16
PWM_MAX = 200

REG_CPU_DOWN_T  = 0x0F00
REG_CPU_UP_T    = 0x0F10
REG_CPU_DUTY    = 0x0F20
REG_GPU_DOWN_T  = 0x0F30
REG_GPU_UP_T    = 0x0F40
REG_GPU_DUTY    = 0x0F50


def read_table(ec: EC, base: int) -> list[int]:
    return [ec.read(base + i) for i in range(TABLE_LEN)]


def write_table(ec: EC, base: int, values: list[int]) -> bool:
    if len(values) != TABLE_LEN:
        raise ValueError(f"need {TABLE_LEN} values, got {len(values)}")
    for i, v in enumerate(values):
        try:
            ec.write_verify(base + i, v & 0xFF)
        except (ECUnavailable, ECWriteRejected) as e:
            print(f"  write 0x{base+i:04X} = {v} failed: {e}")
            return False
    return True


def read_all(ec: EC) -> dict:
    return {
        "cpu_down_t": read_table(ec, REG_CPU_DOWN_T),
        "cpu_up_t":   read_table(ec, REG_CPU_UP_T),
        "cpu_duty":   read_table(ec, REG_CPU_DUTY),
        "gpu_down_t": read_table(ec, REG_GPU_DOWN_T),
        "gpu_up_t":   read_table(ec, REG_GPU_UP_T),
        "gpu_duty":   read_table(ec, REG_GPU_DUTY),
    }


def decode_table(down_t: list[int], up_t: list[int], duty: list[int]) -> list[dict]:
    pts = []
    for i in range(TABLE_LEN):
        pts.append({
            "id": i,
            "up_t": up_t[i] if up_t[i] != 255 else None,
            "down_t": down_t[i] if down_t[i] != 255 else None,
            "duty": duty[i] // 2 if duty[i] != 255 else None,
        })
    return pts


def encode_table(points: list[dict]) -> tuple[list[int], list[int], list[int]]:
    down_t = [255] * TABLE_LEN
    up_t   = [255] * TABLE_LEN
    duty   = [255] * TABLE_LEN
    for p in points:
        i = p["id"]
        if i >= TABLE_LEN:
            continue
        if p.get("up_t") is not None:
            up_t[i] = min(255, int(p["up_t"]))
        if p.get("down_t") is not None:
            down_t[i] = min(255, int(p["down_t"]))
        if p.get("duty") is not None:
            duty[i] = min(PWM_MAX, int(p["duty"]) * 2)
    return down_t, up_t, duty


def load_vendor_json(path: str) -> tuple[list[dict], list[dict]]:
    with open(path) as f:
        data = json.load(f)
    cpu = [{"id": p["ID"], "up_t": p["UpT"], "down_t": p["DownT"], "duty": p["Duty"]} for p in data["CPU"]]
    gpu = [{"id": p["ID"], "up_t": p["UpT"], "down_t": p["DownT"], "duty": p["Duty"]} for p in data["GPU"]]
    return cpu, gpu


def print_table(name: str, points: list[dict]):
    print(f"\n{name}:")
    print(f"  {'ID':>3}  {'UpT':>5}  {'DownT':>5}  {'Duty%':>5}")
    for p in points:
        u = str(p["up_t"]) if p["up_t"] is not None else "---"
        d = str(p["down_t"]) if p["down_t"] is not None else "---"
        dy = str(p["duty"]) if p["duty"] is not None else "---"
        print(f"  {p['id']:>3}  {u:>5}  {d:>5}  {dy:>5}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--read", action="store_true", help="read current tables")
    ap.add_argument("--write", metavar="JSON", help="write curve from vendor JSON")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written")
    args = ap.parse_args()

    if os.geteuid() != 0:
        print("needs root: sudo python3 fan_curve.py ...")
        return 1

    ec = EC()
    ok, why = EC.available()
    if not ok:
        print(f"EC unavailable: {why}")
        return 1

    if args.read:
        tables = read_all(ec)
        print_table("CPU", decode_table(tables["cpu_down_t"], tables["cpu_up_t"], tables["cpu_duty"]))
        print_table("GPU", decode_table(tables["gpu_down_t"], tables["gpu_up_t"], tables["gpu_duty"]))
        return 0

    if args.write:
        cpu_pts, gpu_pts = load_vendor_json(args.write)
        cpu_dt, cpu_ut, cpu_dy = encode_table(cpu_pts)
        gpu_dt, gpu_ut, gpu_dy = encode_table(gpu_pts)

        print("=== CPU curve ===")
        print_table("CPU", cpu_pts)
        print("=== GPU curve ===")
        print_table("GPU", gpu_pts)

        if args.dry_run:
            print("\n[dry-run] would write to EC")
            return 0

        print("\nWriting CPU tables...")
        ok = True
        ok &= write_table(ec, REG_CPU_DOWN_T, cpu_dt)
        ok &= write_table(ec, REG_CPU_UP_T,   cpu_ut)
        ok &= write_table(ec, REG_CPU_DUTY,   cpu_dy)

        print("Writing GPU tables...")
        ok &= write_table(ec, REG_GPU_DOWN_T, gpu_dt)
        ok &= write_table(ec, REG_GPU_UP_T,   gpu_ut)
        ok &= write_table(ec, REG_GPU_DUTY,   gpu_dy)

        if ok:
            print("\nAll writes verified. Reading back...")
            tables = read_all(ec)
            print_table("CPU (readback)", decode_table(tables["cpu_down_t"], tables["cpu_up_t"], tables["cpu_duty"]))
            print_table("GPU (readback)", decode_table(tables["gpu_down_t"], tables["gpu_up_t"], tables["gpu_duty"]))
        else:
            print("\nSome writes failed")
            return 1
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())