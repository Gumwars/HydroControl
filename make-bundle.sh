#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build a clean HydroControl bundle for beta testers.
# Excludes the Windows reference material and reverse-engineering artefacts —
# useful for development, ~760 MB, and irrelevant to running the app.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-/tmp/hydrocontrol-beta}"
rm -rf "$OUT"; mkdir -p "$OUT"

copy() { mkdir -p "$OUT/$(dirname "$1")"; cp -r "$DIR/$1" "$OUT/$1"; }

# the application
copy hydroc/ec.py;      copy hydroc/hardware.py; copy hydroc/rgb.py
copy hydroc/cli.py;     copy hydroc/server.py;   copy hydroc/__init__.py
copy hydroc/lpp.py;     copy hydroc/lppd.py
copy hydroc/presets.py; copy hydroc/hotkeys.py
copy hydroc/deps.py;    copy hydroc/kmod.py
copy hydroc/desktop.py; copy hydroc/desktop_files
copy hydroc/ui;         copy hydroc/systemd

# RGB transport (keyboard + chin bar)
copy kbctrl/kbctrl
copy kbctrl/key_matrix.json
copy kbctrl/pyproject.toml

# kernel module source
for f in uniwill-acpi.c uniwill-wmi.c uniwill-wmi.h Makefile dkms.conf LICENSE \
         uniwill-laptop.rst sysfs-driver-uniwill-laptop; do
  [[ -e "$DIR/uniwill-laptop/$f" ]] && copy "uniwill-laptop/$f"
done

# docs + installer
copy install.sh; copy README.md; copy DESIGN.md; copy LICENSE

# diagnostics testers are asked to run -- see "Diagnostic scripts" in README.md
for f in lb_mode_probe.py lb_set_color.py compat_probe.py ec_state_capture.py \
         charge_path_probe.py; do
  [[ -e "$DIR/$f" ]] && copy "$f"
done

find "$OUT" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$OUT" -name '*.pyc' -delete 2>/dev/null || true

TAR="$OUT.tar.gz"
tar -czf "$TAR" -C "$(dirname "$OUT")" "$(basename "$OUT")"
printf '\nbundle: %s\n  size: %s\n files: %s\n' \
  "$TAR" "$(du -h "$TAR" | cut -f1)" "$(find "$OUT" -type f | wc -l)"
