#!/bin/bash
# SPDX-License-Identifier: MIT
# Probe what each charge_types mode actually does to the EC charge threshold.
# Records the original mode and restores it on exit.
#
# Usage: sudo ./charge_mode_probe.sh

set -u

BAT=/sys/class/power_supply/BAT0
TYPES="$BAT/charge_types"
THRESH="$BAT/charge_control_end_threshold"

if [ ! -e "$TYPES" ]; then
	echo "ERROR: $TYPES missing — is the patched uniwill-laptop module loaded?" >&2
	exit 1
fi

# "Trickle [Standard] Long_Life" -> "Standard"
ORIG=$(sed -n 's/.*\[\([^]]*\)\].*/\1/p' "$TYPES")
echo "Original mode: $ORIG"

restore() {
	echo
	echo "Restoring original mode: $ORIG"
	echo "$ORIG" > "$TYPES" 2>/dev/null
}
trap restore EXIT

printf '\n%-12s %-10s %-10s %s\n' "MODE" "THRESHOLD" "CAPACITY" "STATUS"
printf '%s\n' "------------------------------------------------"

for mode in Trickle Standard Long_Life; do
	if ! echo "$mode" > "$TYPES" 2>/dev/null; then
		printf '%-12s %s\n' "$mode" "(write rejected)"
		continue
	fi

	# Give the EC a moment to apply the profile.
	sleep 3

	printf '%-12s %-10s %-10s %s\n' \
		"$mode" \
		"$(cat "$THRESH" 2>/dev/null || echo '-')" \
		"$(cat "$BAT/capacity" 2>/dev/null || echo '-')" \
		"$(cat "$BAT/status" 2>/dev/null || echo '-')"
done

echo
echo "Interpretation:"
echo "  - If THRESHOLD changes per mode, the EC drives the cap from the profile."
echo "    The mode whose threshold is lowest (~60-80) is the battery-preserving one."
echo "  - If THRESHOLD stays 100 for all three, the modes control charge *rate*"
echo "    or current, not the cap, and the real difference only shows while"
echo "    actually charging on AC."
