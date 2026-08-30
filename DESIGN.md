# HydroControl — Linux Control Center Design

**Scope:** Linux replacement for the Eluktronics Control Center on the HYDROC-16 G1
(Uniwill/Tongfang chassis, EC firmware `117.ELUK`, i9-14900HX + RTX 4090).

**Status:** All major hardware surfaces mapped and verified on this machine. Remaining
gaps are fan curves and the performance-mode index. No Windows install is required —
everything below was reverse-engineered from Linux, the DSDT, and published drivers.

**Verified on:** kernel 7.1.8-1-cachyos-bore, patched `uniwill-laptop` module loaded.

---

## 1. Two rules that govern everything

**Resolve, never hardcode.** `hidraw*` and `hwmon*` indices are assigned at
enumeration/probe order and change across reboots and USB changes. Hardcoding either is
the single most common source of "works sometimes" bugs — it cost this project weeks on
the chin bar.

| Surface | Resolve by |
|---|---|
| Chin bar | hidraw whose `uevent` contains `0000048D:00007001` |
| Keyboard | USB VID/PID `048d:600b` |
| Fans & temps | hwmon whose `name` reads `uniwill` |
| Platform toggles | `INOU0000:00` — ACPI-derived, stable |

**Almost nothing persists.** The EC keeps charging profile, the custom-profile latch, and
CPU power limits in volatile state that clears on every power cycle. This is not a
detail — it is the reason the application must be a daemon rather than a settings panel.
See §7.

---

## 2. Access layers

Four independent paths reach this hardware. They do not overlap.

| Layer | Reaches | Mechanism |
|---|---|---|
| `uniwill-laptop` sysfs | platform toggles, hwmon, battery | patched in-tree module (local DMI entry) |
| ACPI `ECRR`/`ECRW` | the entire EC address space | `acpi_call`, MMIO at `0xFE410000` |
| hidraw feature reports | chin bar | `HIDIOCSFEATURE(9)`, 8-byte packets |
| libusb | keyboard RGB | `ite8291r3` |

### The EC has two windows, both via ECRR/ECRW

The DSDT wraps the EC as memory-mapped I/O and serialises access behind the ACPI mutex
`UWOL`:

```
Method (ECRR, 1) { Local0 = (0xFE410000 + Arg0); Return (MMRW(Local0, 0, 0, 0)) }
Method (ECRW, 2) { Local0 = (0xFE410000 + Arg0); MMRW(Local0, 1, 0, Arg1) }
```

- **`0x07xx` — the settings window.** What the `uniwill-laptop` driver knows about. This
  is the **write** side.
- **`0x04xx` — the ECXP window** (`SystemMemory` at `ECMA + 0x400`, same base). The EC's
  **live** values. Writes here are accepted by `ECRW` and silently discarded.

Going through `ECRR`/`ECRW` is materially safer than `/dev/mem`, which would bypass the
`UWOL` lock and can race the EC's own firmware.

**`ECRW` validates nothing.** It is a debugging tool. The application must never call it
directly for anything with a driver-backed sysfs path.

---
## 3. Feature surface — working

---

### 3.1 CPU power limits ✅ verified

Three steps, proven end to end: 75 W setting produced 75.0 W sustained package draw,
45 W produced 45.0 W (measured via the RAPL energy counter under
`stress-ng --cpu-method matrixprod`).

| Step | Register | Value |
|---|---|---|
| 1. Arm custom profile mode | `0x0727` bit 6 | set — the profile LED beside the power button turns **white**, matching Windows' custom-profile indication |
| 2. Write the limit | `0x0783` PL1_SETTING, `0x0784` PL2_SETTING | watts, directly; `0` = firmware default |
| 3. Confirm | `0x046A` PL1L, `0x046B` PL2L | live readback mirrors the setting |

Also present: `0x046F` PL4L = 125 W (peak), `0x046E` PL3L = 0, `0x0466` bit 0 = turbo.

**RAPL is not the source of truth.** `/sys/class/powercap/intel-rapl` reports 205 W
throughout regardless of the EC limit — the EC enforces outside the MSR path. A UI showing
"current power limit" must read the EC. Actual draw is only observable via the RAPL
*energy counter* under load.

Tooling: `tdp_latch.py --status | --enable | --disable | --set-pl1 <W>`, `powerwatch.py`.

---

### 3.2 Battery ✅ exposed, semantics partly open

Driver path: `/sys/class/power_supply/BAT0/`

| Attribute | Access | Values |
|---|---|---|
| `charge_types` | RW | `Trickle` · `Standard` · `Long_Life` (active one bracketed) |
| `charge_control_end_threshold` | RW | 1–100 |
| `current_now` | RO | µA — non-zero means genuinely charging; the hardest value to fake |
| `charge_now` / `charge_full_design` | RO | µAh; use design as the denominator for a true percentage |

EC registers: profile at `0x07A6` bits 5:4, threshold at `0x07B9` bits 0–6, capability bit
at `0x078E` bit 3 (set on this machine, `0xFC`).

**Label mapping — confirmed** via the tuxedo-drivers comment (`0 => high capacity,
1 => balanced, 2 => stationary`), independent RE of the same EC:

| Control Center | EC value | Linux `charge_types` |
|---|---|---|
| Stationary | 2 | `Trickle` |
| Balanced | 1 | `Long_Life` |
| Long Haul | 0 | `Standard` |

**Unexplained:** neither `Trickle` nor `Long_Life` caps charging on this firmware. Both
charged to 6400 mAh at 4.21 V/cell with an honest coulomb count (delivered/reported ratio
1.00–1.07 throughout). tuxedo-drivers writes the identical register, so the write is
correct; the behaviour differs. Working hypothesis is a long-horizon hold-low policy
invisible to a single charge cycle. **Do not present these as percentage caps in the UI.**

**The threshold has a shadow set the app never writes.** `charge_path_probe.py`
(2026-08-27) A/B-tested the two write paths live:

| Register | Role | Baseline | After ECRW | After WKBC |
|---|---|---|---|---|
| `0x07B9` | threshold + reached bit | 0x55 (85%) | **0x50 (80%)** | 0x50 |
| `0x07A6` [5:4] | charging profile | 0x20 (stationary) | **0x10 (balanced)** | 0x10 |
| `0x0497` bit 5 | charge-limit-mode flag | 0x45 | 0x45 | **0x24** (EC modified) |
| `0x087F` | shadow limit | 0xFF | 0xFF | 0xFF |
| `0x04AB` | SoC gate | 0x64 (100%) | 0x64 | 0x64 (EC-managed) |
| `0x07CD` | threshold shadow (GCHG readback) | **0x5A (90%)** | 0x5A | **0x50 (80%)** |

Findings:

1. **The ECRW path is NOT dead.** `0x07B9` and `0x07A6` both take plain ECRW writes
   and hold. The FIXCGLM overlay's premise ("ECRW→MMRW writes to CGLM are silently
   ignored") is **disproven on this EC** — see the §4 corrections table.
2. **The shadow set was out of sync at baseline:** `0x07CD` held 90% while the real
   threshold was 85%. The app writes only `0x07B9` via sysfs; the SCHG shadow set
   (`0x07CD`, `0x0497`, `0x087F`, `0x04AB`) is never touched. Windows writes the whole
   set via SCHG. "Stops at 80 briefly, then resumes" is exactly what a two-register
   check (0x07B9 stops, 0x07CD=90 resumes) or a clobber-from-shadow would do.
3. **WKBC lands on some shadow registers, not all.** `0x07CD` and `0x0497` took the
   WKBC writes cleanly; `0x04AB` is live SoC (the 0xFF trigger is consumed and
   rewritten). **`0x087F` never moved** — a WKBC write of 80 left it at 0xFF, and
   "uninitialized state per GCHG" was a rationalisation, not a finding. And the
   WKBC→`0x07B9` evidence is inconclusive: Test B's write was indistinguishable from
   Test A's ECRW result, and the restore readback was immediate. On current evidence
   ECRW writes `0x07B9` and WKBC does not — the inverse of the overlay's premise —
   but the WKBC→`0x07B9` question needs a clean re-test to close.

**Timeline resolved (no unattended drift).** The probe's 85% baseline is explained
by the boot apply log: `hydroc-apply` at 06:16:40 logged `charge_threshold: 100 -> 85`
(the 06:20:48 run logged no threshold change because it was already 85). The register
reading 80 now is the user's own change after the probe — profile mtime 12:16:30,
content `charge_threshold: 80` — written via sysfs→ECRW, and it has held since. Neither
the audit's "drift caught in the act" nor its "WKBC restore failed" reading survives
the logs; both were confounded by the boot apply and the user's later change.

**Open:** whether the EC clobbers `0x07B9`'s threshold bits when it sets bit 7 at the
stop point is still unproven — that needs a charge cycle with `charge_ctrl_watch.py`
running. The register-level evidence already points to the fix: write the full SCHG
set together. `charge_path_probe.py` is the tool; it restores every register to its
baseline on exit. **Process gap:** the probe run had no saved artifact — the table
above is transcribed scrollback. Re-run with output saved (`--out`) before drawing
further conclusions.

Bonus: real cycle count lives at `0x04A6`/`0x04A7` (reads 65) while sysfs `cycle_count`
reports 0.

---

### 3.3 Chin bar RGB ✅ ITE 8233 only

8-byte feature reports, `HIDIOCSFEATURE(9) = 0xC0094806`, report-id `0x00` prefixed.
Protocol matches tuxedo-drivers `ite_8291_lb.c` and OpenRGB MR 3166. Setting colour or
brightness turns the bar on; no commit needed.

Packet shape: `08 <variant> <mode> <speed> <bri> <colour source> <dir> 00`, variant `22`
for PID `7001` (`02` = `6010`, `21` = `7000`). All modes below were observed on the bar
with `lb_mode_probe.py`; tuxedo-drivers implements **only** static for `7001` and returns
`-ENOSYS` for the rest, so the mode codes came from the `7000` table and the behaviour
column is ours.

| Effect | Packet | Colour | Verified |
|---|---|---|---|
| Static | `14 00 01 R G B 00 00` then `08 22 01 01 <bri> 01 00 00` | slot 1 | **Yes** |
| Breathing | 8 × `14 00 <n> R G B 00 00` then `08 22 02 <speed> <bri> 08 00 00` | colour list | **Yes** |
| Wave | `08 22 03 <speed> <bri> 01 00 00` | own colours | **Yes** |
| Clash | `14 00 01 R G B 00 00` then `08 22 04 <speed> <bri> 01 00 00` | slot 1 | **Yes** |
| Catchup | `08 22 05 <speed> <bri> 01 00 00` | own colours | **Yes** |
| Flash | `08 22 11 <speed> <bri> <src> <dir> 00` | — | **No — dead** |
| Off | `12 00 03…` → `08 05…` → `08 01…` → `1A…01` | — | **Yes** |

`bri` 0–100, `speed` 1–10 (1 fastest). Single-zone — no per-pixel. No flash-persist
command exists.

**Speed runs backwards on the wire** — `0x01` is fastest, `0x0a` slowest, per
tuxedo-drivers' own comment. The UI presents the usual slower→faster direction and
inverts at the boundary (`toWireSpeed`). The keyboard library documents only a `0-10`
range and never states a direction, so `KB_SPEED_INVERTED` carries the assumption that
it matches the bar. **Unconfirmed** — if a higher setting makes the keyboard slower,
flip that one constant.

**The colour-source byte decides whether an effect animates at all.** Breathing under
source `01` renders as a solid unchanging colour — indistinguishable from Static — and
only animates under `08`, the 8-entry colour list. To breathe in one chosen colour,
write that colour to all eight slots. `rgb.chinbar` shipped `08 22 02 <speed> <bri> 00`
for months: source `00` animates, but no palette write ever happened, so the colour
swatch did nothing and a beta tester reported it. The table above already said `08`;
the code did not match the table.

Wave and catchup run their own colours and ignore anything written to the palette — the
same note tuxedo-drivers makes for `7000` ("no color is available for mode").

**Flash (`11`) is dead on this PID.** Probed at sources `01` and `08` and directions
`00`/`01`/`02`: the bar switches off every time. Upstream implements it only for `6010`.
Removed from `LB_MODES` and from the UI rather than left as a button that does nothing.

Control Center's effect set is `[1, 2, 3, 5, 9, 13, 32]` from `RGBKeyboard.reg`
`save_effect` values. Mapping those to firmware modes is open.

---

### 3.4 Keyboard RGB ✅ complete

USB `048d:600b` via libusb (`ite8291r3`). 9 firmware effects; user mode `0x33` gives
static colour and **per-key** control over a 6×21 matrix; brightness 0–50.

**Persistence solved:** the `save` flag writes to the controller's onboard storage, so
colours survive power loss and are restored by the keyboard before userspace starts.
`restore_palette()` resets to the ITE factory palette.

**`ite8291r3-ctl` is an unpinned dependency with a breaking API change in it.** Two
tester reports came from this one library, and both were invisible on the development
machine:

| | 0.3 | 0.4 |
|---|---|---|
| Handle | `ite8291r3(usb_channel(dev, ep))` | `ite8291r3(dev, ep, traffic_callback)` |
| `usb_channel` | present | **removed** |
| `get()` | `get(loc=None)` | `get(loc, traffic_callback)`, both required |
| `PRODUCT_IDS` | `[6004, CE00]` | `[6004, 6006, CE00]` |

`0x600B` is in **neither**, so the library's own `get()` can never find this keyboard —
it also insists on `bcdDevice == REV_NUMBER`. We therefore do not call `get()` at all:
`_get_ite_device` finds the device itself and `_ite_handle` builds the handle by
capability probe (`hasattr(_ite, "usb_channel")`) rather than version string. The two
transports are equivalent — 0.4 merely inlined what `usb_channel` wrapped.

The development machine had a **hand-patched 0.3** with `0x600B` appended to
`PRODUCT_IDS`, which is why detection worked here and nowhere else. Do not patch a
dependency in place to make something work; the patch does not ship. `doctor` now prints
the installed version beside the keyboard check, because "which version" is the first
question every time.

---

### 3.5 LPP cooling dock ✅ verified on hardware

The **Liquid Propulsion Package** is an external liquid cooler and the only surface in
this project that is not part of the laptop. No EC register reaches it and no sysfs node
describes it: it is a **Bluetooth LE peripheral** advertising as `CoolingSystem`, spoken
to over the Nordic UART Service.

| | |
|---|---|
| TX (write) | `6e400002-b5a3-f393-e0a9-e50e24dcca9e` |
| RX (notify) | `6e400003-b5a3-f393-e0a9-e50e24dcca9e` |

Frames are 8 bytes, `FE <cmd> … EF`:

| Command | Frame |
|---|---|
| Fan duty | `FE 1B 01 <0–100> 00 00 00 EF` |
| Pump mode | `FE 1C 01 3C <mode> 00 00 EF` |
| Init, purpose unknown | `FE 1E 01 00 B8 FF 00 EF` |
| Status / keepalive | `FE 33 00 00 00 00 00 EF` |

**The pump codes are not in speed order.** `0` is High, `1` Max, `2` Low, `3` Medium.
Treating the byte as an ordered scale silently puts the dock in the wrong mode, because
it accepts any value without complaint. `lpp.PUMP_MODES` maps names to codes; nothing
should pass a raw integer.

**The dock ignores everything until it has seen the init sequence**, and needs it twice —
the first pass is unreliable on a cold connection. The sequence ends with the literal
bytes `sw`, which are not a framed command.

**Writes are unacknowledged.** The TX characteristic is write-without-response, so
success means "handed to the adapter", never "the dock acted on it". `status()` reports
`verified: false` and the UI says so, rather than implying a readback this transport
cannot provide. The dock does send notifications on RX, but nothing is known about their
payload — the most recent frame is surfaced in status so that channel is an open question
with data rather than a dropped one.

**Architecture: a sidecar, not part of the HTTP daemon.** `hydroc.lppd` owns the BLE
link and exposes a Unix socket at `/run/hydroc/lpp.sock`; `hydroc.server` proxies
`/api/lpp/*` to it. Three reasons: `bleak` is async and the HTTP bridge is a synchronous
threaded server holding the EC lock; the dock is optional hardware and `bleak` should not
become a hard import for a machine that will never have one; and one owner per device —
the same rule the chin bar follows. If the sidecar is not running the UI hides the
section, and nothing else degrades.

**Verified 2026-08-26** against a real dock: scan, connect, init, and fan control
confirmed *audibly* via `POST /api/lpp/set` with `bleak` 3.0.2.

Two corrections to an earlier, premature "verified" here. Connect + init + a
`re-applied fan …` log line is **not** evidence the dock acted: the NUS TX
characteristic is write-without-response, so a successful `send()` means "handed
to the adapter" and nothing more. Only a fan you can hear settles it.

And the first working test changed two things at once -- the init sequence below
*and* bypassing a UI that staged changes behind an Apply button. Which of the two
was the fault is **not established**. The init change is correct regardless,
because matching the reference implementation exactly is the right default; but
it has not been shown to be the fix. The protocol notes below were derived entirely from reading
LibreLPP rather than from probing, which is why there was nothing to debug —
the same lesson as the chin bar and the TDP latch, for the third time.

Two operational notes. `bleak` must be importable **by root**: the sidecar runs
under systemd, so a `--user` install is invisible to it (DESIGN.md §4.4). And
`Dock._load_state` accepts LibreLPP's own `state.json` verbatim — its `pump` is
a raw device code, which the "older file" compatibility path already handles —
so migrating a user's existing settings is a file copy.

**The trailing `sw` goes exactly once.** LibreLPP sends `init_cmds` (four framed
commands plus `sw`), then `init_cmds[:-1]` -- the four frames again, *without*
`sw`. We sent it on both passes. `sw` reads as *switch*, so a second one may
toggle the dock back out of the mode the first selected. Untested either way;
the asymmetry is reproduced because it is in the reference, not because we have
shown it matters.

**Still unknown:** what the dock sends back on the RX characteristic — but it is
worth decoding, and Control Center says so. `GamingCenter3_Cross.dll` (UTF-16
strings; ASCII `strings` misses them entirely) contains:

```
CoolingSystem LCT21001           full advertised name; LCT21001 is the pump model
strPumpStatus / strPumpStatusErr the Windows app READS pump status
LCPUMP_DUTY                      pump has a duty value, not only four modes
LiquidCoolingAutoModeSupport     an auto mode we do not implement
IsLC_BT_PumpCtrl                 confirms Bluetooth, not USB
```

So the dock reports state and the RX channel is where it arrives. The DLL itself
is a dead end for the format: it is .NET Native (AOT-compiled, no IL to
decompile) and the NUS GUIDs appear nowhere in it, as strings or as `Guid` byte
layout — the BLE plumbing is built at runtime or lives in a WinRT component.

`lppd` now keeps the last 40 notifications with timestamps
(`status()["notifications"]`). Correlating those against commands we send is a
much cheaper route to the format than disassembly.

**This dock stops advertising once it has been connected and then dropped.**
That is device behaviour, not a bug in anything, and it has a sharp consequence:
after the sidecar exits for any reason, a plain BLE scan can never find the dock
again. It looks exactly like the dock being powered off, and the obvious remedy
-- power cycle it -- works, which is how the real cause stayed hidden.

`BleakClient.connect()` does not help either: bleak needs the device present in
BlueZ's discovered-object tree and reports `Device ... was not found` for a
cached-but-silent peripheral. **`bluetoothctl connect <addr>` does**, because
BlueZ opens a *directed* connection from its own cache. Once BlueZ holds the
link, bleak attaches to it normally.

So the connect order is: `HYDROC_LPP_MAC` -> the remembered address -> whatever
BlueZ already knows -> a scan -> **a directed BlueZ connect, then attach again**.
Without that last step every sidecar restart, every reboot and every update
needed a physical power cycle of the dock.

Related: a cached address is not a reachable device. `bluetoothctl devices`
keeps entries long after the peripheral has gone silent, so "BlueZ knows it"
must never be read as "we can connect to it".

**Credit.** The frame layout, service UUIDs and pump encoding were established by
[LibreLPP](https://github.com/Sebastian-Alexis/LibreLPP) (MIT, Copyright (c) 2024 sra),
which reverse-engineered them from the Windows Control Center. `hydroc/lpp.py` is an
independent implementation against those facts, not a copy of that code, but the
knowledge is theirs and this project would not otherwise have had it.

**Limitation:** `set_effect` takes a palette index, not RGB — firmware effects are limited
to eight named colours plus random. Per-key mode is unrestricted RGB. Arbitrary colour in
an *animated* effect means driving per-key frames from the host on a timer, which is a
different mechanism rather than a missing parameter.

---

### 3.6 Fans ✅ mechanism found, ⚠️ writes unverified

Pressing the profile button while the BIOS has it set to **fan profiles** moved seven
registers. Every one of them is already named in `uniwill-acpi.c` — the answer was in
our own vendored driver the whole time, in a header nobody had read.

| Register | Driver name | Observed |
|---|---|---|
| `0x0751` | `EC_ADDR_MANUAL_FAN_CTRL` | `0x00` → `0x40` = `FAN_MODE_BOOST` |
| `0x075B` | `EC_ADDR_PWM_1` | `0x3C` → `0xC8` (60 → 200) |
| `0x075C` | `EC_ADDR_PWM_2` | `0x3C` → `0xC8` |
| `0x0768` | `EC_ADDR_SWITCH_STATUS` | `0x00` → `0x04` = `FAN_BOOST_STATUS` |
| `0x0464` | `EC_ADDR_MAIN_FAN_RPM_1` | `0x06` → `0x11` (RPM high byte) |
| `0x046C` | `EC_ADDR_SECOND_FAN_RPM_1` | `0x06` → `0x12` |
| `0x0417` | **unidentified** | `0x00` → `0xC0` |

`0x0751` decodes as:

```
bits 2:0   FAN_LEVEL_MASK      fan level
bit  4     FAN_MODE_TURBO
bit  5     FAN_MODE_HIGH
bit  6     FAN_MODE_BOOST      <- what the button sets
bit  7     FAN_MODE_USER       <- manual control
```

**PL4's live mirror is half-scale too.** `get_power_limits` doubled `pl4_setting` for
half-scale storage but read `pl4_live` (`0x046F`) raw, so the UI showed "live 60 W"
beside a 120 W setting and "125 W" for a 250 W firmware default. Confirmed by request:
200 W stored as raw 100. Both fields now scale identically.

**PWM scale is 0–200, not 0–255.** `PWM_MAX = 200`, and the driver rescales for hwmon
with `fixp_linear_interpolate(0, 0, PWM_MAX, U8_MAX, value)`. That predicts the measured
values exactly: 60 → 76 and 200 → 255, matching the `pwm1` readings on both sides of the
press. Anything writing a raw 0–255 duty into `0x075B` would be 27% off.

**`0x0768` bit 2 is readable state**, so the UI can show whether fan boost is on —
including when the *button* turned it on rather than us.

**Correction: the fan curve is not at `0x0786`–`0x078A`.** That was carried over from a
tuxedo-drivers comment and is wrong for this chassis; those registers did not move.
The real curves are 16-point tables (`FAN_TABLE_LENGTH = 16`):

| Table | Address |
|---|---|
| CPU temp start / end | `0x0F10` / `0x0F00` |
| CPU fan speed | `0x0F20` |
| GPU temp start / end | `0x0F40` / `0x0F30` |
| GPU fan speed | `0x0F50` |

**The OEM's own curves are in `hydroc-driver/`, and they fit the register layout.**
`Eluktronics Control Center.zip` carries the *deployed* service tree, which contains
`UserFanTables/DefaultFanTable_{Office,Gaming,Turbo}.json` — three 16-point curves,
matching `FAN_TABLE_LENGTH = 16` exactly:

| Mode | Max duty | `*Temp_DefaultMaxLevel` |
|---|---|---|
| Office | 55 % | 8 |
| Gaming | 90 % | 11 |
| Turbo | 100 % | 11 |

Each point is `{UpT, DownT, Duty}` — rise threshold, fall threshold, duty percent — so
the curves carry hysteresis, and `Duty` is 0–100 while the register is 0–`PWM_MAX`
(200). Register value = `Duty * 2`.

Two traps in this material. The installer's *per-chassis* `UserFanTables/PH4*/PH6*`
directories are **not this machine**: every set tops out at PL1 40–60 W against our
measured 75 W default, and `EC_ADDR_PROJECT_ID` (`0x0740`) reads **`0x19`**, one past
the end of the driver's table, which stops at `0x18`. Those are older, lower-power
chassis. And this is vendor data — if any of it is used verbatim, it does not become
MIT by being copied into an MIT tree. Deriving our own curves informed by the shape is
the clean route; `hydroc-driver/` is already excluded from the tester bundle.

**Unverified:** nothing here has been *written* yet. `FAN_MODE_USER` (bit 7) is the
presumed latch for manual duty, by analogy with the `0x0727` bit 6 TDP latch, but that
is a guess. The driver also defines `EC_ADDR_PWM_1_WRITEABLE = 0x1804` and `0x1809` with
an explicit warning that they are "unstable on some models and likely not meant to be
used by applications". Probe before shipping, and remember the safety asymmetry: setting
a fan **too low** is the dangerous direction, and a power cycle clears it.

---

### 3.7 Performance modes ✅ answered — they are not in the firmware

Windows shows Office / Balanced / Beast / Custom with a blue / green / purple / white
LED. None of it is EC state. The chain is:

```
button  ->  EC raises WMI 0xB0 (UNIWILL_OSD_PERFORMANCE_MODE_TOGGLE)
        ->  uniwill-laptop sparse_keymap  ->  KEY_F14
        ->  input device "Uniwill WMI hotkeys"
        ->  (on Windows) UniwillService decides the mode, writes it, sets the LED
```

With no service listening, **nothing happens**. Four button presses arrived as
`KEY_F14` while every register in `0x0400`–`0x04FF` and `0x0700`–`0x07FF` stood still
and the LED never moved. That is the whole mechanism: the EC *announces*, the host
*decides*. Contrast the fan-profile BIOS position (§3.6), where the EC acts alone.

`EC_ADDR_OEM_3` (`0x07A5`) looked promising — the driver defines `FAN_QUIET`,
`OVERBOOST` and `HIGH_POWER` there and never uses them — but writing every
combination changed nothing: not the LED, not the fans, not the live power envelope.

**That null is trustworthy because the instrument was validated.** Live PL1
(`0x046A`) tracked `60 → 45 → 90 → 60` against deliberate setpoint writes, so the live
window is a responsive readout, not a constant. Without that control the null would
have proved nothing — the same trap as reading RAPL's limit fields.

Two intermittent `write_verify` rejections on `0x07A5` were initially read as firmware
arbitration. They did not reproduce. They were EC-busy races, not logic. **Two samples
are not a pattern.**

**The profile LED is not on the keyboard matrix.** All 25 spare ITE 8291 cells were lit
with nothing visible outside the keys. It is EC-driven, and the only state reachable is
**white**, via the `0x0727` bit 6 custom-TDP latch — confirmed: latch on → white, latch
off → blue.

**No shipped config defines the per-mode power limits.** Office/Gaming/Turbo appear in
the service tree only as *fan* curves. The .NET assembly exposes `CPU_PL1Maximum`,
`CPU_PL2Maximum` and `CPU_PL4Maximum`, which suggests the service derives each mode's
limits at runtime from BIOS/EC-reported maxima rather than reading constants. There is
nothing to copy, which is a real answer: our preset values are legitimately ours to
choose.

**Consequence:** presets are ours to define. Listen for `KEY_F14`, advance through named
PL1/PL2/PL4 + cTGP bundles, apply through the path already verified to the watt. Since
every preset arms the latch, the physical LED stays white throughout — the machine is
always "Custom" from the EC's point of view, and the UI should say so rather than imply
the LED tracks our modes.

---

### 3.8 Thermals, fans, platform toggles ✅ read/partial

hwmon (`name` = `uniwill`): `temp1_input` CPU, `temp2_input` GPU (m°C), `fan1_input` /
`fan2_input` RPM, `pwm1` / `pwm2` 0–255 **read-only**.

`/sys/bus/platform/devices/INOU0000:00/`: `fn_lock`, `super_key_enable`,
`touchpad_toggle_enable`, `ac_auto_boot`, `usb_powershare_high` (all 0/1), and
`ctgp_offset` (GPU configurable TGP offset in watts).

**UI constraint:** `ac_auto_boot` and `usb_powershare_high` are mutually exclusive — the
driver rejects both on. Present as a three-way choice, not two independent switches.

---

## 4. Corrections — do not re-tread these

Each of these was investigated and closed. They are recorded so nobody repeats them.

| Path | Verdict |
|---|---|
| **EC lightbar registers** (`0x0748`–`0x074B` AC, `0x07E2`–`0x07E5` BAT) | **Inert on this model.** Writes are accepted and read back correctly but drive nothing. Setting `APP_EXISTS`, clearing `WELCOME`/`S0_OFF`, and writing RGB produced no visible change while the ITE 8233 kept cycling. `UNIWILL_FEATURE_LIGHTBAR` was removed from the device descriptor, so `uniwill:multicolor:status`, `rainbow_animation`, and `breathing_in_suspend` no longer appear. Drive the bar over hidraw only. |
| **64-byte HID output report** on the 8233 | Declared in the report descriptor and accepts writes (10 framings tried, all 65 bytes accepted), but produces no effect. Not the colour channel. |
| **`0x16` effect-selector family** | Wrong family — that is the ITE **8291 keyboard's** effect selector. The 8233 uses `08 22`. A 142-candidate sweep of `0x16` found nothing. |
| **Writing `0x046A`/`0x046B`** to set power limits | Accepted by `ECRW`, silently discarded. That window is the live readback. Write `0x0783`/`0x0784`. |
| **WMI mailbox as a special latch** | `WKBC`/`RKBC`/`SCMD` are a generic EC read/write channel (`FUNCTION_WRITE=0`, `READ=1`, `FEATURE_TOGGLE=5`). tuxedo-drivers sets the charging profile with a plain `0x07A6` read-modify-write, exactly as we do. There is no magic commit command. |
| **FIXCGLM overlay premise** ("ECRW→MMRW writes to CGLM are silently ignored") | **Disproven on this EC** (2026-08-27, `charge_path_probe.py`). Plain ECRW writes to `0x07B9` and `0x07A6` land and hold. The overlay's *shadow set* (`0x07CD`, `0x0497`, `0x087F`, `0x04AB`) is real and partially WKBC-writable, but the "ECRW is dead" premise is not. The overlay was never loaded on this machine (stock SSDT12 is 14.8 KB; FIXCGLM is 338 bytes), and **`SCHG` exists in no DSDT on this machine** — the reference driver's SCHG routing is dead without the overlay. |
| **`WQBA` WMI MOF** | Decodes to Microsoft's stock `AcpiTest_*` sample template (GUIDs `ABBC0F60`–`ABBC0F72`). `GetSetULong` is the generic pipe; no semantics. Command meanings live in `GamingCenter3_Cross.dll`. |
| **`ec_sys` / `/sys/kernel/debug/ec/ec0/io`** | The true EmbeddedControl region (`ECMP`) holds only `XSEC` at 0x40 and `DEVS` at 0x7B. Everything interesting is in the MMIO windows. |

**Process note:** the chin bar protocol and the TDP latch both came from published
drivers (`ite_8291_lb.c`, tuxedo-drivers) after extended blind probing. **Check prior art
before probing.**

---

### 4.1 `0x0741` bit 0 is the master switch, and it sits above the TDP latch

Settings stopped taking effect: power limits accepted, then reverting to `0`, the
EC running its own 75/75/250 defaults. The custom-profile latch (`0x0727` bit 6)
read **armed** throughout, so the obvious suspect was innocent.

The real gate is one the project had never looked at. From `uniwill-acpi.c`:

```c
#define EC_ADDR_AP_OEM      0x0741
#define ENABLE_MANUAL_CTRL  BIT(0)

static int uniwill_ec_init(struct uniwill_data *data)
    regmap_set_bits(data->regmap, EC_ADDR_AP_OEM, ENABLE_MANUAL_CTRL);
    devm_add_action_or_reset(data->dev, uniwill_disable_manual_control, data);
```

The driver **sets** it at probe and **clears** it from a devm cleanup action when
the module unloads. It is the EC's "host software is in control" switch, and it
sits *above* the custom-profile latch:

| `0x0741` b0 | `0x0727` b6 | Result |
|---|---|---|
| set | set | power limits apply |
| set | clear | writes accepted, silently discarded (documented, expected) |
| **clear** | **set** | **writes accepted, latch reads armed, EC ignores everything** |

That third row is the trap. Every indicator this project had said healthy.

**Anything that unloads the module clears it** -- `install.sh --module`, a DKMS
rebuild, a manual `modprobe -r`. If the module comes back, `uniwill_ec_init()`
sets it again; if the reload is incomplete, the machine looks fine and silently
ignores the application. Reloading the driver is the fix, and it is now offered
as a `Repair` action rather than left to be rediscovered.

**Why `write_verify` did not catch it.** It writes, waits 50 ms, reads back. The
EC accepted the value into the register and zeroed it later on its own schedule,
so the readback passed. `write_verify` proves a register *took* a value; it
cannot prove the value *survived*, and nothing in this codebase re-checks after
the fact. Drift detection is the thing that would have caught it, and it did --
it reported the mismatch correctly the whole time. The report was read as a bug
in drift rather than as drift doing its job.

**Confidence.** The reload fixed it and the driver source makes the mechanism
plain, but no snapshot was taken before the reload, so this is a strongly
supported inference rather than a demonstrated one. `ec_state_capture.py` exists
to make the next occurrence provable instead of argued.

---

### 4.2 The EC needs 6 ms between accesses, and starving it stops the fans

**Reading EC registers too fast stops the fans.** Not writing — reading.
`fan_recover.py --report` performs ten reads and no writes, and the fans dropped to
0 rpm. They came back on their own a few minutes later with no power cycle, which is
the tell: this is a **transient starvation, not a latched state**.

**Correction to the first diagnosis.** This was originally written up as general
read-rate starvation. That does not survive contact with the evidence: `hydroc.server`
does ~11 EC reads per `/api/state` poll, every 10 s for a full working day, with no fan
trouble at all -- more reads than `fan_recover --report` performs once. Volume is not
the discriminator. The one thing `--report` did that nothing else in the project does is
read **`0x0464`/`0x0465`/`0x046C`/`0x046D` -- the fan tachometer registers -- through
`ECRR`. Reading the EC's own fan-measurement registers out from under it fits the
symptom far better. Those reads are now opt-in behind `--ec-tacho`; hwmon reports the
same values through the driver's path and is safe.

The 6 ms delay below is still correct and still applied -- the driver does it, the OEM
software does it, and adding it did make `--report` safe. But it was presented as *the*
cause with more confidence than the evidence supported.

`uniwill-acpi.c` says:

```c
/*
 * The OEM software always sleeps up to 6 ms after reading/writing EC
 * registers, so we emulate this behaviour for maximum compatibility.
 */
#define UNIWILL_EC_DELAY_US	6000
```

and calls `usleep_range(6000, 12000)` after **every** `ECRR` and `ECRW`. `hydroc/ec.py`
had no delay on reads at all. The EC services ACPI calls on the same firmware loop that
runs fan control; back-to-back reads starve that loop and the fans stop until the
traffic does. `EC._call` now enforces a 6 ms class-level minimum gap.

**This retroactively explains several earlier results, all of which were misread:**

| Symptom | Was blamed on | Actually |
|---|---|---|
| `0x07A5` write rejections that would not reproduce | EC-busy races | EC busy **because of us** |
| `fan_characterise.py`: 0 rpm at every duty | fans not responding | fans stalled by the sampling |
| Same run: `EC OVERRIDE` on every sample | EC thermal protection | our own polling of `0x075B` |
| Occasional garbled replies | firmware flakiness | probably the same cause |

The fan characterisation ran ~15 minutes at 60 W sustained with the fans stalled. It was
survivable only because an LPP and an external cooler were attached. **Note what that
implies about the backstop:** if the EC's fan loop is starved, so is whatever emergency
ramp lives on it. The real protection in that scenario was the CPU's own PROCHOT/Tjmax
throttling, which is independent of the EC — not anything we or the EC were doing.

Cost of the fix where it matters: `read_state()` ~0.07 s, a telemetry poll ~0.02 s. The
daemon's steady-state EC duty cycle is well under 1 %. **Bulk scans are the hazard** —
512 reads is 3 s of continuous EC traffic and a full `0x000`–`0xFFF` dump is 25 s. Pace
those harder than the 6 ms minimum, and do not run them under load.

---

### 4.3 An unprivileged check is UNKNOWN, not FAILED

The desktop window runs as the user; the daemon runs as root. `diagnose()` was
being run from both, and every check that needs root — opening the ITE 8291 over
libusb, reaching the EC — fails as the user. Reported verbatim, that told someone
their keyboard library was unavailable when it was installed, current and
working, and sent them to reinstall a package that was never the problem.

The failure mode is worth naming because it is not a wrong check, it is a check
answering a different question than the one being asked. "Can *this process*
open the keyboard" is not "is the keyboard working".

`diagnose()` now marks such checks `needs_root`, and when the caller is not root
they come back `unknown`: no remedy, no repair action, excluded from the failure
count. `doctor` prints them as `SKIP` and says how many were skipped; the health
banner and the desktop launch page both ignore them. `keyboard_available()` also
names a permission error as a permission error rather than as absence.

Diagnostics that overstate their reach are worse than no diagnostics: a missing
feature is discoverable, but confident wrong advice costs the user real time.

---

### 4.4 How the daemon is started changes what it can import

`ite8291r3-ctl` lives in a pipx venv under the user's home. `kbctrl.hardware`
found it by searching `$HOME` and `~$SUDO_USER` — which works under `sudo`, and
fails under systemd, where `HOME=/root` and `SUDO_USER` is unset.

Nothing about the machine changed. The *launch path* changed: the desktop app
starts the daemon with `pkexec systemctl start hydroc-server` instead of
`sudo python3 -m hydroc.server`. Same machine, same library, same user — and
`HAS_ITE` went from True to False, reporting "ite8291r3_ctl not installed" while
the library sat in `/home/<user>/.local/share/pipx/`.

The advice that failure produced — reinstall it — could never have helped, since
the process was only ever looking in `/root`.

Root can read every home, so the search now covers `$HOME`, `/root`,
`~$SUDO_USER` and every `/home/*`, de-duplicated. Verified by running with
`HOME=/root` and `SUDO_USER` unset, which is precisely what systemd provides.

**The general shape:** a dependency resolved relative to the invoking user is a
dependency that changes with the launcher. Adding a second way to start the
daemon silently added a second environment for every path lookup in it. Anything
resolved from `$HOME`, `$USER`, `$XDG_*` or `$SUDO_USER` should be assumed
launcher-dependent until tested under each launcher.

---

## 5. Volatility model — the reason this is a daemon

| Setting | Storage | Survives power cycle? |
|---|---|---|
| Keyboard effect / per-key | 8291 onboard flash (`save=1`) | **Yes** — restored by the keyboard itself |
| Chin bar colour/effect | 8233 RAM | No — now saved in the profile and re-sent by `hydroc.cli apply` |
| LPP fan / pump | dock RAM | No — `hydroc-lpp` re-sends intent on every reconnect |
| Charging profile (`0x07A6`) | EC RAM | **No** — observed reverting to `HIGH_CAPACITY` after reboot |
| Custom-profile latch (`0x0727` bit 6) | EC RAM | No |
| CPU power limits (`0x0783`/`0x0784`) | EC RAM | No |
| Charge threshold (`0x07B9`) | EC RAM | No — plus a shadow set (`0x07CD`, `0x0497`, `0x087F`, `0x04AB`) the app does not write; see §3.2 |
| Platform toggles (`INOU0000:00`) | EC | Believed yes — unverified |

This corrects an earlier assumption that the EC keeps its own persistent copy. It does
not, at least for the charging profile — the reboot on 2026-08-18 reverted it.

**Windows solves this with `UniwillService`**, a background service whose job is
re-applying EC settings at boot. That is not installer scaffolding; it is load-bearing,
and HydroControl needs the equivalent.

---

## 6. Application architecture

```
┌──────────────────────────────────────────────────────────────┐
│ hydrocd — privileged daemon (systemd, root)                  │
│                                                              │
│   owns:  EC via acpi_call        chin bar via hidraw         │
│          keyboard via libusb     driver sysfs                │
│                                                              │
│   restores full state on: boot · resume · AC/battery change  │
│   reads actual hardware state at startup — never trusts       │
│     stored preferences alone                                 │
│                                                              │
│   IPC: /run/hydroc/hydroc.sock — newline-delimited JSON      │
│        get-state · apply {...} · reload · subscribe          │
├──────────────────────────────────────────────────────────────┤
│ clients (unprivileged)                                       │
│   hydroc-cli · TUI · DMS/Quickshell widget · GUI             │
└──────────────────────────────────────────────────────────────┘
```

Builds on the existing `kbctrld` pattern (socket IPC, profile JSON, inotify reload), which
already works — the change is widening its remit from RGB to the whole machine.

**Design rules:**

1. **Read hardware state at startup, then reconcile.** Because settings evaporate, a UI
   that trusts its stored preference will confidently display "Stationary" while the EC
   sits in `HIGH_CAPACITY`. Read first, show the truth, then re-apply.
2. **Prefer driver sysfs over raw EC access.** Use `charge_types`, hwmon, and the platform
   attributes wherever they exist. Reserve `ECRR`/`ECRW` for what has no driver path —
   currently only the CPU power limits and the `0x0727` latch.
3. **The profile file is intent; the hardware is state.** Keep them separate and surface
   drift rather than hiding it.
4. **One owner per device.** Two processes writing the chin bar produced the original
   intermittency. The daemon owns hardware; clients only talk to the daemon.

### Privilege model

Every EC write and hidraw open needs root. A GUI must not run as root. udev covers most
of it:

```
# /etc/udev/rules.d/99-hydroc.rules
KERNEL=="hidraw*", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="7001", GROUP="plugdev", MODE="0660"
KERNEL=="hidraw*", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="600b", GROUP="plugdev", MODE="0660"
```

`power_supply` attributes are recreated on hotplug, so udev rules there are fragile — the
daemon should own `charge_types` and the threshold. `acpi_call` is root-only by nature,
which is another argument for keeping EC access daemon-side.

---

## 7. Open questions

1. **Why do the charging profiles have no observable effect?** The register write is
   identical to tuxedo-drivers' and the mapping is confirmed, yet neither `Trickle` nor
   `Long_Life` caps charging over a full cycle (delivered/reported 1.00–1.07 at
   4.21 V/cell). Hypothesis: a long-horizon hold-low policy invisible to one cycle.
   **Do not present these as percentage caps.**

2. **The charge threshold does not hold.** 80% stops charging briefly, then it resumes.
   Predates this codebase, so EC-side. Likely the EC rewrites the whole of `0x07B9` to
   set `CHARGE_CTRL_REACHED` (bit 7), clobbering the threshold in bits 0–6.
   `charge_ctrl_watch.py --set 80` samples fast enough to catch it.

3. **The 30% shutdown.** Powers off at ~30% reported charge at idle, so not voltage sag.
   Discriminator: cell voltage at cutoff (~3.6 V/cell = a real reserve; ~3.0–3.2 =
   genuinely empty and the gauge reads high). `battery_watch.py` flushes per sample.

4. **Fan duty control** (§3.6). Registers are identified and `FAN_MODE_BOOST` works, but
   setting a duty does not. The universal-control path (`0x07C6` b2 + `0x07C5` b7 +
   tables) accepted writes with no observed effect — **that test ran while `0x0741` bit 0
   was clear (§4.1), so it is void and must be repeated.** Manual duty is most likely a
   WMI method, not a register write: the driver marks `0x1804`/`0x1809` "only accessible
   when using the WMI interface".

5. **LPP dock readback.** The RX characteristic carries ASCII and framed replies on the
   same channel, notifications can arrive fragmented, and `FE 31 05 02 EF` (opcode `0x31`,
   payload `0x05`) is unexplained. Control Center has `strPumpStatus` and `LCPUMP_DUTY`,
   so state is readable and the pump may take a duty rather than four discrete modes.

6. **LPP RGB.** Unknown whether the dock even has addressable lighting. The only lead is
   the init frame `FE 1E 01 00 B8 FF 00 EF`, where `00 B8 FF` reads like an RGB triple
   and `0x1E` is documented here as "purpose unknown".

7. **GPU MUX.** `card2-eDP-2` exists on the NVIDIA card and Control Center has `DgpuOnly`,
   so a MUX exists; envycontrol cannot flip it. Check the BIOS first. A wrong MUX write
   leaves no display, which makes it the highest-stakes write in the project.

8. **`0x0730` = 75** — a second copy of the PL1 value. Purpose unknown.

9. **Do the `INOU0000:00` platform toggles survive a power cycle?** Believed yes,
   unverified.

10. **Chin bar** — modes `03`/`04`/`05` confirmed and `11` dead (§3.3). Mapping Control
    Center's effect set `[1,2,3,5,9,13,32]` onto firmware modes is still open.

---

## 8. Next steps

1. **Re-run the universal fan control experiment** with `0x0741` bit 0 confirmed set.
   Everything concluded from the previous run is void.

2. **Fan curves.** If universal control works, a userspace controller — read temp,
   interpolate, write `0x0F20`/`0x0F50` — with a duty floor and a temperature abort.
   Derive curves from `fan_characterise*.py` measurements on this platform, not from the
   vendor JSON: those were calibrated on Windows with DPTF actively shaving power, and
   they are vendor data besides.

3. **LPP**: decode the `0x31` reply frame from captured notifications, then probe the
   ASCII console (`?`, `help`) carefully — it drives a pump.

4. **envycontrol integration** by detect-and-delegate, not vendoring. It is a general
   Linux tool; carrying a copy buys nothing and inherits a graphics-stack bug surface.

5. **Rust port.** `hydrocd` + Tauri v2 reusing `ui/index.html` unchanged. Tauri on Linux
   uses webkit2gtk, so it carries the same ~130 MB dependency the GTK4 app does.
   `Intent` and `Observed` as distinct types would have made several bugs here compile
   errors rather than evenings.

A Windows install is **not** required for any of the above. The Windows *files* in
`hydroc-driver/` have repeatedly been useful — read them with `strings -el`, since .NET
stores UTF-16 and an ASCII scan finds nothing.
