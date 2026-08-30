# HydroControl — OpenCode Handoff

**Date:** 2026-08-27  
**Context:** Fan curve investigation session — discovered PL1/2/4 control regression

---

## Summary of Fan Curve Investigation

### What We Discovered

1. **Fan curve tables exist at 0x0F00–0x0F5F** (6 × 16 bytes: CPU DownT/UpT/Duty, GPU DownT/UpT/Duty)
   - Writes accepted and verified
   - All zeros when no fan profile active (BIOS in "performance modes")

2. **Universal EC fan control path** (tuxedo-drivers approach):
   - Enable: `0x07C6` bit 2 (ENABLE_UNIVERSAL_FAN_CTRL) + `0x07C5` bit 7 (SPLIT_TABLES)
   - Write duty to `0x0F20` (CPU fan1) / `0x0F50` (GPU fan2) — first table entries
   - EC interpolates through ALL 16 entries → must clear indices 1–15 (DownT=255, UpT=255, Duty=0)

3. **Test results**: PWM barely responded (76→77) even with clean table
   - Firmware default curve still dominates
   - Missing activation trigger or EC ignores user tables in current state

4. **Old workaround path** (FAN_MODE_BOOST + 0x1804/0x1809):
   - `0x1804`/`0x1809` rejected (write_verify fails — reads back 0xFF)
   - Unstable per driver comment: "unstable on some models and likely not meant to be used"

### Current Fan Control State

| Method | Status |
|--------|--------|
| `FAN_MODE_USER` (0x0751 bit 7) + PWM writes | ❌ Rejected — reverts in 4s |
| `FAN_MODE_BOOST` (0x0751 bit 6) | ✅ Works — forces max fan (200) |
| `FAN_MODE_HIGH` (0x0751 bit 5) + level | ❌ Ignored — stays at default |
| Auto mode (0x0751=0) | ✅ Firmware default curve (PWM 89→204 over 58→87°C) |
| Universal fan control (0x07C6/0x07C5 + tables) | ⚠️ Writes accept, but PWM doesn't follow |
| Vendor curve tables (0x0F00–0x0F5F) | ✅ Writes accept, persist, but not active |

**Vendor curves** (from `hydroc-driver/Resources/.../UserFanTables/`) are 16-point with hysteresis:
- Format: `{UpT, DownT, Duty}` where Duty 0–100 → register = Duty × 2 (PWM_MAX=200)
- `CpuTemp_DefaultMaxLevel: 11` = 11 active points (indices 0–10)

---

## CRITICAL REGRESSION: PL1/2/4 Control Broken

**During fan register experiments, the custom-profile latch (0x0727 bit 6) was likely cleared.**

### Symptoms
- PL1/2/4 settings in UI have no effect
- EC reverts to firmware defaults (PL1=75, PL2=75, PL4=250)
- Writes to 0x0783/0x0784/0x0785 accepted but ignored
- `read_state()` shows live values matching firmware defaults, not user settings

### Root Cause
The custom-profile latch **must be armed** before TDP writes take effect:
```
0x0727 bit 6 = 1  → custom profile enabled (profile LED turns WHITE)
0x0727 bit 6 = 0  → firmware defaults, TDP writes accepted but silently discarded
```

### Fix Required
Re-arm the latch and re-apply user power limits:
```python
ec.set_custom_profile(True)          # arms 0x0727 bit 6
ec.set_power_limit('pl1', 75)        # or whatever user profile has
ec.set_power_limit('pl2', 75)
ec.set_power_limit('pl4', 250)
```

**This must run at daemon startup and after every resume** — the latch is volatile EC RAM.

---

## What Needs Review / Fix

### 1. Immediate (blocking)
- [ ] **Restore PL1/2/4 control** — verify `custom_profile_enabled()` at daemon start, re-arm if false
- [ ] Add latch state to `Hardware.status()` so `/api/status` reports it
- [ ] Ensure `hydroc-apply.service` / `hydroc-resume.service` re-arm latch on boot/resume

### 2. Fan Curves (next priority)
- [ ] Complete universal fan control init sequence (match tuxedo-drivers `uw_init_fan` exactly)
- [ ] Implement userspace curve controller: daemon reads temp → interpolates curve → writes `0x0F20`/`0x0F50`
- [ ] UI for editing 16-point curves (import vendor JSON as starting point)
- [ ] Safety: minimum duty floor (25% per tuxedo), temperature abort, panic restore

### 3. Diagnostic / Safety
- [ ] `fan_characterise_safe.py` works well — keep for thermal calibration
- [ ] Add RPM zero-detection to daemon (watchdog)
- [ ] Document EC register volatility: everything in 0x07xx/0x04xx clears on power cycle

---

## Files Modified During Session

| File | Purpose |
|------|---------|
| `fan_characterise_safe.py` | Safe thermal characterization with RPM monitor + panic key |
| `fan_curve.py` | Read/write 16-point fan curve tables (0x0F00–0x0F5F) |
| `fan-characterisation-safe.json` | Latest safe characterization data |

**No core `hydroc/` files modified** — the PL1/2/4 regression is a runtime state issue (latch cleared), not a code change.

---

## Commands to Restore PL1/2/4

```bash
# Check current state
sudo python3 -c "
from hydroc.ec import EC
ec = EC()
print('Custom profile:', ec.custom_profile_enabled())
print('PL1/2/4 setting:', ec.read(0x0783), ec.read(0x0784), ec.read(0x0785))
print('PL1/2/4 live:', ec.read(0x046A), ec.read(0x046B), ec.read(0x046F))
"

# Re-arm and restore (run as root)
sudo python3 -c "
from hydroc.ec import EC
ec = EC()
ec.set_custom_profile(True)
ec.set_power_limit('pl1', 75)
ec.set_power_limit('pl2', 75)
ec.set_power_limit('pl4', 250)
print('Restored. Custom profile:', ec.custom_profile_enabled())
"

# Verify via HydroControl CLI
sudo python3 -m hydroc.cli apply
```

---

## Next Steps for Fan Curves

1. **Port `uw_init_fan` exactly** from tuxedo-drivers:
   - Clear all 16 entries to (255, 255, 0)
   - Set index 0: DownT=0, UpT=255, Duty=variable
   - Enable `0x07C6` bit 2 + `0x07C5` bit 7
   - Disable firmware curve interference

2. **Implement daemon-side curve controller**:
   - Config: 16-point curve per fan (JSON, editable in UI)
   - Loop: read CPU/GPU temp → interpolate duty → write `0x0F20`/`0x0F50`
   - Respect minimum duty (25%), temperature limits, hysteresis

3. **UI integration**:
   - Fan curve editor (visual, like Control Center)
   - Import/export vendor JSON format
   - "Revert to firmware" button (disable universal control)

---

## Safety Notes

- **Full power cycle clears all EC state** — always the ultimate recovery
- **Fans stopped = thermal emergency** — CPU PROCHOT at ~100°C is last resort
- **`fan_recover.py`** clears manual bits; if hwmon still shows 0 RPM → power cycle
- **Never write fan curves under load** — test idle first, then gradual load

---

## Open Questions

1. Why doesn't universal fan control take effect? (Missing trigger? EC firmware version?)
2. Does BIOS "fan profiles" mode populate tables automatically? (Profile button traces)
3. Can we use `0x078E` bit 6 (`HAS_UW_FAN_CTRL`) as feature gate?
4. What clears the custom-profile latch? (Boot, resume, BIOS change, EC reset?)

---

## Handoff to Next Session

**Start with:** Restore PL1/2/4 control (5 min fix), then decide fan curve approach:
- **Pragmatic:** Userspace curve controller + universal EC control (daemon owns fan logic)
- **Deep:** Find missing activation register, make EC curves work natively

The vendor curves are reference-only (GPL boundary). Derive our own from `fan_characterise_safe.py` data.