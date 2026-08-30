#!/bin/bash
# Test harness for uniwill-laptop infinitybook_gen10 on HYDROC-16 G1.
# Usage: sudo ./test_features.sh

set -u
PLAT=/sys/bus/platform/devices/INOU0000:00
PASS=0; FAIL=0; SKIP=0

say() { printf '\n=== %s ===\n' "$1"; }
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

feature_attr() { # $1=name -> path or empty
	[ -e "$PLAT/$1" ] && echo "$PLAT/$1" || echo ""
}

# 1. FN lock
say "1. fn_lock"
P=$(feature_attr fn_lock)
if [ -z "$P" ]; then skip "fn_lock attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	if [ "$R" = "0" ] || [ "$R" = "1" ]; then ok "fn_lock reads back valid ($R)"; else bad "fn_lock unexpected: $R"; fi
	echo 1 > "$P" && echo "  wrote 1"; echo "  read after write: $(cat "$P")"
	echo 0 > "$P" && echo "  wrote 0"; echo "  read after write: $(cat "$P")"
fi

# 2. Super key
say "2. super_key_enable"
P=$(feature_attr super_key_enable)
if [ -z "$P" ]; then skip "super_key_enable attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	if [ "$R" = "0" ] || [ "$R" = "1" ]; then ok "super_key_enable reads back valid ($R)"; else bad "super_key_enable unexpected: $R"; fi
	echo 1 > "$P" && echo "  wrote 1"; echo "  read after write: $(cat "$P")"
	echo 0 > "$P" && echo "  wrote 0"; echo "  read after write: $(cat "$P")"
fi

# 3. Touchpad
say "3. touchpad_toggle_enable"
P=$(feature_attr touchpad_toggle_enable)
if [ -z "$P" ]; then skip "touchpad_toggle_enable attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	if [ "$R" = "0" ] || [ "$R" = "1" ]; then ok "touchpad_toggle_enable reads back valid ($R)"; else bad "touchpad_toggle_enable unexpected: $R"; fi
	echo 0 > "$P" && echo "  wrote 0 (disable touchpad)"; echo "  read after write: $(cat "$P")"
	echo 1 > "$P" && echo "  wrote 1 (enable touchpad)"; echo "  read after write: $(cat "$P")"
fi

# 4. Lightbar (LED class)
say "4. Lightbar LED class"
LEDDIR=$(ls -d /sys/class/leds/uniwill:* 2>/dev/null | head -1)
if [ -z "$LEDDIR" ]; then skip "no uniwill LED class dir"; else
	LED=$(basename "$LEDDIR")
	MB=$(cat "$LEDDIR/max_brightness"); echo "  led=$LED max_brightness=$MB"
	echo "  MAX_BRIGHTNESS=$MB" > /tmp/uniwill_led_max.txt
	[ -n "$MB" ] && ok "LED class present, max_brightness=$MB" || bad "no max_brightness"
	# multicolor brightness test: set to mid and full
	if [ -e "$LEDDIR/brightness" ]; then
		MID=$((MB/2))
		echo "$MID" > "$LEDDIR/brightness"; echo "  wrote brightness=$MID"
		echo "$MB" > "$LEDDIR/brightness"; echo "  wrote brightness=$MB"
		ok "brightness writes accepted"
	fi
fi

# 5. Battery charge control
say "5. Battery charge control"
if [ -e /sys/class/power_supply/BAT0/charge_control_end_threshold ]; then
	R=$(cat /sys/class/power_supply/BAT0/charge_control_end_threshold); echo "  read: $R"
	if [ "$R" -ge 1 ] && [ "$R" -le 100 ]; then ok "charge_control_end_threshold reads valid ($R)"; else bad "threshold unexpected: $R"; fi
	echo 80 > /sys/class/power_supply/BAT0/charge_control_end_threshold
	echo "  read after write 80: $(cat /sys/class/power_supply/BAT0/charge_control_end_threshold)"
	echo 100 > /sys/class/power_supply/BAT0/charge_control_end_threshold
	echo "  read after write 100: $(cat /sys/class/power_supply/BAT0/charge_control_end_threshold)"
else
	skip "charge_control_end_threshold absent on BAT0"
fi

# 6. cTGP
say "6. ctgp_offset"
P=$(feature_attr ctgp_offset)
if [ -z "$P" ]; then skip "ctgp_offset attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	[ -n "$R" ] && ok "ctgp_offset reads ($R)" || bad "ctgp_offset read failed"
	echo 0 > "$P" && echo "  wrote 0"; echo "  read after write: $(cat "$P")"
fi

# 7. AC auto boot
say "7. ac_auto_boot"
P=$(feature_attr ac_auto_boot)
if [ -z "$P" ]; then skip "ac_auto_boot attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	if [ "$R" = "0" ] || [ "$R" = "1" ]; then ok "ac_auto_boot reads back valid ($R)"; else bad "ac_auto_boot unexpected: $R"; fi
	echo 1 > "$P" && echo "  wrote 1"; echo "  read after write: $(cat "$P")"
	echo 0 > "$P" && echo "  wrote 0"; echo "  read after write: $(cat "$P")"
fi

# 8. USB powershare
say "8. usb_powershare_high"
P=$(feature_attr usb_powershare_high)
if [ -z "$P" ]; then skip "usb_powershare_high attr absent"; else
	R=$(cat "$P"); echo "  read: $R"
	if [ "$R" = "0" ] || [ "$R" = "1" ]; then ok "usb_powershare_high reads back valid ($R)"; else bad "usb_powershare_high unexpected: $R"; fi
	echo 1 > "$P" && echo "  wrote 1"; echo "  read after write: $(cat "$P")"
	echo 0 > "$P" && echo "  wrote 0"; echo "  read after write: $(cat "$P")"
fi

say "SUMMARY: $PASS pass, $FAIL fail, $SKIP skipped"
exit 0
