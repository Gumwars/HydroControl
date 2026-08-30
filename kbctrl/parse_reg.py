#!/usr/bin/env python3
import sys

reg_path = "/home/gumwars/kbctrl/Resources/ControlCenter_5.23.50.2_Eluktronics/RGBKeyboard.reg"

try:
    with open(reg_path, "r", encoding="utf-16") as f:
        lines = f.readlines()
except Exception as e:
    print(f"Error reading {reg_path}: {e}")
    sys.exit(1)

interesting_lines = []
for idx, line in enumerate(lines):
    l = line.strip()
    if not l: continue
    # Looking for hex signatures in the registry like hex:0x16 or similar variables
    if "Breathing" in l or "Effect" in l or "Static" in l or "Rainbow" in l or "16,00" in l or "08,02" in l or "14,01" in l:
        interesting_lines.append(l)

with open("/home/gumwars/kbctrl/reg_summary.txt", "w", encoding="utf-8") as out:
    for il in interesting_lines:
        out.write(il + "\n")
print(f"Parsed {len(lines)} lines. Found {len(interesting_lines)} relevant lines.")
