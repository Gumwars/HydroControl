# What Control Center actually does: battery, performance modes, RGB

(Battery first; then
[Performance modes and the profile button](#performance-modes-and-the-profile-button)
and [RGB: the keyboard and the chin bar](#rgb-the-keyboard-and-the-chin-bar).)

Static analysis of the Windows service and its driver, then a measured charge
under Windows (WINDOWS-CAPTURE.md Test 1), 2026-09-30. Read-only throughout:
nothing was written to the EC, to NVRAM, or to the vendor's settings.

**Short version.**
- Control Center writes `0x07A6` bits 5:4 and nothing else. Its kernel driver
  never acts on its own.
- Under Windows, with Stationary set before plug-in, the pack charged
  69% -> 100% without stopping. The charge target sat at 16800 mV throughout,
  as it does on Linux.
- The profile looks like a minor input to a protection scheme that the EC and
  the pack's own gauge run by themselves. See
  [The layered model](#the-layered-model-how-charging-is-actually-decided).

## Setup

| | |
|---|---|
| Machine | ELUKTRONICS HYDROC-16, Windows 11 Pro 26200 (installed 2026-09-30) |
| Firmware | **EC 1.17, BIOS N.1.09ELUK (2024-05-03)** -- the Linux baseline, not updated |
| Control Center | UWP frontend `ControlCenter3` 5.23.50.2; backend `GCUService.exe` 1.0.2.70 (.NET), `GCUBridge.exe` 1.0.1.10 |
| Install path | `C:\Program Files\OEM\Eluktronics Control Center\UniwillService\MyControlCenter\` |
| EC transport | `\\.\ACPIDriver` (`DeviceIoControl`), bound to `ACPI\INOU0000` -- the same `\_SB.INOU` device `acpi_call` reaches on Linux |

`GCUService.exe` is unobfuscated .NET, so this is a disassembly of the real
code rather than an inference from strings. Method bodies were dumped with a
reflection-based IL disassembler (no third-party decompiler), then every
reference to the relevant registers and names was traced to its callers.

Note: Control Center was already installed and launched before this session,
so the "dump before and after TCC install" step in WINDOWS-CAPTURE.md is no
longer possible on this install.

## The answer: Control Center writes `0x07A6` bits 5:4 and nothing else

The only live battery code is `GCUService.MySystem.BatteryProtection2`. It is
enabled unconditionally at startup (`App.<StartUpTask>b__44_1` ->
`BatteryProtection2.EnableByService()`), and all it does is:

```
read  0x07A6
clear bits 4 and 5
set   bit 4  -> Middle (Balanced)            0x10
      bit 5  -> Low    (Stationary)          0x20
      none   -> High   (full capacity)       0x00
write 0x07A6
```

| UI | `HealthProtectionStatus` | `0x07A6` bits 5:4 | HydroControl key |
|---|---|---|---|
| full / high capacity | 0 | `00` | `long_haul` |
| Balanced Mode | 1 (constructor default) | `01` | `balanced` |
| Stationary Mode | 2 | `10` | `stationary` |

HydroControl's `CHARGE_PROFILES` mapping (`hydroc/hardware.py`) matches.

**When it writes:**
- service start (`Init()` reads `HKLM\SOFTWARE\OEM\GamingCenter2\BatteryProtection2\HealthProtectionStatus`, then applies it)
- resume (`PowerModeChanged` Resume -> `Init()`)
- a user change from the frontend (MQTT topic `BatteryProtection/Control`)
- uninstall/disable -> forces High (`0x00`)

**What it does not do**, each checked by tracing callers across the whole assembly:

- **No keepalive.** There is no periodic re-write. The 60 s `BatteryPercentManger` timer and the AC-change handler only publish status to the UI.
- **No software ceiling.** Nothing watches the percentage and stops charging.
- **No commit or handshake write** after `0x07A6`. `MyEcCtrl.Write` goes straight through `AcpiCtrl.Write` to the driver.
- **No write to `0x07B9`** (charge threshold) on this machine.
- **No write to `0x07D0`** (see below).
- **No write to the NVRAM charge fields** (see below).

This is byte-for-byte what `charge_profile_probe.py` and `hydroc` already do.
"Windows drives it differently" is closed at the code level. What is left is
behaviour: the same write, observed on the same firmware.

## Dead code that is worth knowing about

The service contains a fuller charge-limit implementation. **Nothing calls
it** in this build, but it shows what the platform was designed to support:

### `0x07D0` -- `ADDR_BATTERY_CHARGE_LIMIT_DOWN` (new to this project)

`Define.ECSpec` names two registers:

| const | addr |
|---|---|
| `ADDR_BATTERY_CHARGE_LIMIT_UP` | `0x07B9` |
| `ADDR_BATTERY_CHARGE_LIMIT_DOWN` | `0x07D0` |

`BatteryProtection2.SetBatteryChargingLimit_Down(limit)` writes
`(old & 0x80) | limit` to `0x07D0`, accepting only 1..95, default **95**. A
read of 0 is treated as 95. `SetBatteryChargingLimit_Up(limit)` writes
`(old & 0x80) | limit` to `0x07B9`, where 100 means "off" (writes `old & 0x80`).
The default is 100 up / 95 down, which reads as a recharge **floor**: stop at
UP, resume below DOWN.

Both setters have zero callers. A second copy of the `0x07B9` logic lives in
`MyControlCenter.MyFanManager_QC`, which is never constructed. Given
HANDOFF.md's measurement that the EC stores `0x07B9` and never evaluates it,
this looks like a platform-wide API that this EC firmware does not implement.
**`0x07D0` has never been read on this machine** and is worth one
`ec_poke.py read 0x07D0` from Linux, read-only.

### NVRAM charge fields in `UniWillVariable`

`MyControlCenter.NVRAM_STRUCT` is the layout of the `UniWillVariable` EFI
variable (`9f33f85c-13ca-4fd1-9c4a-96217722c593`), the same one that holds the
GPU mode. Packed, with 8-byte RGB arrays at 0x0F-0x2E, it lines up exactly
with the Linux captures: `ProjectID` at `0x06` = `0x19`, and `OemDisplayMode`
at `0x62` = the GPU-mode byte.

| offset | field | Linux captures (all three mux files) |
|---|---|---|
| `0x2F` | PowerMode | `01` |
| `0x30` | **BatteryLimitation** | `FF` |
| `0x31` | **ChargeMaximumLimit** | `FF` |
| `0x32` | **ChargeMinimumLimit** | `FF` |

The setters (`set_m_BatteryChargingLimit_Up/_Down`,
`set_m_BatteryLimitationMode`) call `NvramVariable.SetFwVars`, and have zero
callers. So Control Center 5.23.50.2 never writes these either. The BIOS may
still read them at POST. Their being `FF` (never initialised) is consistent
with nobody on this platform using them.

**Do not write them.** Unlike everything in EC RAM, this variable survives a
power cycle, and it shares storage with the GPU mode byte.

## What this means for the investigation

1. The "missing arming step" is not in the Windows service. There is no
   hidden write, ordering rule or keepalive to copy back to Linux.
2. If Stationary visibly limits charging under Windows, the EC does it in
   response to the same `0x07A6` write. The difference from our Linux runs is
   then conditions (plug-in order, starting charge, temperature, the
   derating rung), not the software.
3. If it does not, the feature is EC-autonomous, as WINDOWS-CAPTURE.md
   predicts, and no OS can drive it.

Either way, **Test 1 in WINDOWS-CAPTURE.md is still the test that decides it**,
and it is now cheaper to interpret, because the code has ruled out every
software explanation.

## The kernel driver is a passive bridge

The service reaches the EC through `UWACPIDriver.sys`. The question was
whether the driver does anything **on its own**, at load or on a timer, that
the service does not ask for.

**Which binary.** The running driver is
`C:\Windows\System32\drivers\UWACPIDriver.sys`, `oem1.inf`, **v11.2.18.347
(2024-08-27)**, Uniwill, WHQL-signed, bound to `ACPI\INOU0000`, demand-start
KMDF 1.15. It is a *newer build* than the 2022 copy in Control Center's
`UWACPIDriver\` folder (different hash), so it came from the driver store or
Windows Update. The analysis below is of the running one.

**The INF** installs the service and nothing else: no `AddReg`, no
co-installer, no registry parameters.

**Imports:** `DbgPrintEx`, `IoWMIRegistrationControl`,
`MmGetSystemRoutineAddress`, `RtlInitUnicodeString`/`RtlCopyUnicodeString`,
plus the WDF loader and WPP recorder. No port I/O, no `MmMapIoSpace`, no
physical-memory access. `MmGetSystemRoutineAddress` only resolves the standard
KMDF/WPP stub names (`PsGetVersion`, `WmiTraceMessage`,
`WmiQueryTraceInformation`, `EtwRegisterClassicProvider`, `EtwUnregister`).

**Which ACPI methods it can call.** `ECRR`, `ECRW` and `SMRW` are immediate
constants in code. An 18-entry table holds most of the rest of `\_SB.INOU`,
stored byte-reversed (`SMCR` = `RCMS`, `DR1T` = `T1RD`, ...): RCMS/WCMS,
MMRB/MMRD/MMWB/MMWD, PCRD/PCWD, IORD/IOWD, RIOP/WIOP, T1RD-T3RD, T1WR-T3WR.
`PWUP`, `PWBT` and `MMRW` are absent. That matches the service's
`IOCTL_GPD_ACPI_*` set one-to-one.

**When it calls them.** No disassembler was installed, so the control flow was
traced from the x64 unwind table (`.pdata`, 64 functions) plus every direct
`call`/`jmp` and every RIP-relative `lea` of a function address:

- `IOCTL_ACPI_EVAL_METHOD` (`0x32C004`) is the only way this driver can run
  an ACPI method. It occurs at exactly six sites, one in each of
  `F_1784`, `F_1A98`, `F_1DC0`, `F_2130` (ECRR), `F_2370` (SMRW), `F_25B8` (ECRW).
  `IOCTL_ACPI_EVAL_METHOD_EX` does not occur.
- All six have exactly one caller, `F_2870`, which holds all 22
  `0x9C40A4xx` IOCTL codes. That is the `EvtIoDeviceControl` dispatcher,
  registered as a callback in `F_8B08` during queue setup.
- The one other large callback, `F_85F0` (registered in `F_839C`, on the
  init path), has no method names and no eval call.

So the driver evaluates an ACPI method **only when a user-mode caller sends
it an IOCTL.** It has no start-up write, no timer and no policy of its own.
With the service established as writing only `0x07A6`, nothing on the Windows
side touches the charge registers beyond that byte.

Caveat: this is a structural trace, not a full disassembly. A call made
through a register-held pointer that is not in `.pdata`-bounded code, or
inline `in`/`out` instructions, would not show up. Nothing in the imports or
strings points that way.

## Test 1 under Windows: Stationary, set before plug-in

Run `win-test1.csv`, 2026-09-30 10:23 -> 11:32, 137 samples at 30 s, logged by
`win_battery_log.ps1`. Barrel adapter only. Stationary (`0x07A6` = `0x28`:
profile bits `10` plus bit 3, the touchpad LED -- see below) was selected in
Control Center before the charger went in. Firmware EC 1.17 / N.1.09ELUK, the
Linux baseline.

**Result: charged 69% -> 100% without stopping.** No ceiling engaged near 80%,
or anywhere else.

| | value across the whole run |
|---|---|
| `chg_target` `0x0522` | **16800** every sample |
| `pack_max` `0x030E` | 17800 |
| `0x07C3` (percentage-ceiling gate) | **`0x0D`** every sample -- never armed |
| `0x0742` | `0x22` every sample (bit 2 clear) |
| `0x07B9` / `0x07D0` | `0x00` / `0x00` -- Control Center writes neither |
| `0x07C6` | `0x04`, one sample `0x00` at 94% -- the same single dip Linux saw (979 of 980) |
| `0x0491` | `0xC1` on AC, `0xC0` off -- bit 0 tracks AC |
| `0x07CC` | `0x80` off AC -> `0x81` on AC -- bit 0 tracks AC; bit 7 (USB-C priority) set although Control Center's setting is 0 |
| battery temperature | 34.9 -> 27.9 C |
| cycles | 134 |

**Coulomb balance.** Integrating `ec_rate` over the run gives 1839 mAh,
against `ec_remaining` rising 4002 -> 5800 = 1798 mAh (2% apart, within the
noise of the stutter samples). Every percent was backed by real current. There
was no phantom climb.

**Phases.**

| time | % | what the charger did |
|---|---|---|
| 10:23-10:39 | 69-78 | constant current, `ec_rate` exactly **2040 mA**, voltage rising 15.72 -> 16.23 V |
| 10:39-10:57 | 78-89 | **stutter**: samples alternate 2040 / 0 / a tapering 3604 -> 2312 series. Every 0-current sample has the voltage 150-250 mV below its neighbours, so the charger really let go, but the average rate is unchanged (78->88% in 17 min vs 69->78% in 16). Most likely the constant-current -> constant-voltage handover. Test 2 samples it at 5 s to check |
| 10:57-11:22 | 89-100 | textbook constant voltage: 16.70-16.72 V (4.18 V/cell) held, current tapering 1972 -> 714 mA |
| 11:22 | **100** | Windows and the gauge declare "fully charged" **with 714 mA still flowing** |
| 11:22-11:32 | 100 | current keeps tapering, 714 -> 510 mA. 112 mAh more went in after "full", while `ec_remaining` sat capped at 5800 |

The end is the **opposite** of the outside report. There, the percentage walks
to 100% with current at zero. Here, the gauge reports 100% while the pack is
still visibly accepting current. That is ordinary gauge behaviour: full is
declared on a taper threshold, and top-up continues past it.

**The 2 A regime is not Windows-specific.** Linux runs split the same way,
all under Stationary and all on the barrel adapter:

| Linux run | charge current |
|---|---|
| `ceiling-test` 09-27, `switch1` 09-28 pm, `phantom-check` 09-24 | pinned flat at 2006-2040 mA |
| `charge-profiles` 09-07, `wmi-ceiling` 09-28 am, `cycle` | up to 4.7-5.4 A |

In `wmi-ceiling` the charge **opened at exactly 2006 mA for ~25 s, then
stepped to 5372 mA** with the voltage jumping 15.8 -> 16.56 V. The pinned runs
never step. Ruled out: power source (barrel every time), profile (Stationary
in both regimes), date, state of charge at plug-in, and **the pack** -- both
regimes occurred on both the replacement and the original pack (see the pack
table under the ACPI reporting layer). Still candidates:
battery temperature or system load *at plug-in*, or a current the pack's own
gauge requests. Cooling from 34.9 to 30.9 C mid-run did not release it.

## EC battery cache, decoded from `win_ec_dump.ps1`

Dump taken mid-charge (~88%, 2 A regime). `0x0300-0x031F` is mirrored at
`0x0320-0x033F`. **`0x03xx` is big-endian, `0x05xx` little-endian**, and
several `0x05xx` fields are byte-swapped copies of `0x03xx`.

| addr | value | reading |
|---|---|---|
| `0x0300-0x0304` | `GF-GF` | ASCII, pack/manufacturer name |
| `0x030E` BE | 17800 | pack maximum voltage, mV (4.45 V/cell) -- known |
| `0x0312` BE / `0x052A` LE | 6400 | design capacity, mAh |
| `0x0314` BE / `0x0534` | `0x590E` | 2024-08-14 if read as an SBS ManufactureDate -- probable, not confirmed |
| `0x0316` BE / `0x052E` LE | 15480 | design voltage, mV -- the factor Windows multiplies `ec_rate` by |
| `0x0318` BE / `0x0532` LE | 8000 | **unknown**; candidate: maximum charge current the pack allows |
| `0x031A` BE / `0x0520` LE | 6300 | unknown |
| `0x0502`, `0x0504` | 3040 | battery temperature, 0.1 K |
| `0x0506`, `0x0508` | 16654 | voltage, mV |
| `0x050A-0x050F` | 1972 x3 | gauge current, mA, **signed** (negative = discharging) |
| `0x0510` | 1972 | fourth current copy; differs slightly from the others -- probably average current |
| `0x0514`, `0x0516` | 88 | gauge state of charge, % |
| `0x0518`, `0x051A` | 5104 | gauge remaining capacity, mAh |
| `0x051C`, `0x051E` | 5800 | gauge full-charge capacity, mAh |
| `0x0522` | 16800 | charge target, mV -- known |
| `0x0528` | 134 | cycle count |
| `0x0542` | 16701 | unknown; *rises* while discharging, so not live voltage |
| `0x0544` | `0x00DD` | constant in all three states -- **not** the status word |
| `0x0566` | 248 | unknown; moves with charge state |
| `0x0571` | 2 | EC charge state (see below) |

No field reads 2040 or 2048, so whatever pins the 2 A regime is not in the
readable cache.

### Three states compared

Dumps mid-charge, at full on the charger, and a few minutes unplugged.
`0x0300-0x033F` was **identical in all three**: it is static pack
information, not live telemetry.

| addr | mid-charge (88%) | full, on AC | unplugged | reading |
|---|---|---|---|---|
| `0x0502` | 3040 | 3010 | 3020 | temperature, 0.1 K |
| `0x0506` | 16654 | 16724 | 15784 | voltage, mV |
| `0x050A` | 1972 | 510 | **-2415** | current, signed |
| `0x0510` | 1972 | 510 | -2381 | probably average current |
| `0x0514` | 88 | **100** | 98 | state of charge, % |
| `0x0518` | 5104 | **5800** | 5684 | remaining, mAh |
| `0x051C` | 5800 | 5800 | 5800 | full-charge capacity, mAh |
| `0x0542` | 16701 | 16724 | 16748 | unknown |
| `0x0544` | `0x00DD` | `0x00DD` | `0x00DD` | constant |
| `0x0566` | 248 | 52 | 55 | unknown |
| `0x0571` | **2** | **0** | **1** | **EC charge state: 2 charging, 0 idle/full, 1 discharging** |
| `0x0572` | 5 | 5 | 4 | bit 0 = AC present |
| `0x0574` | 1 | 1 | 0 | AC present |

At full the gauge reported 100% with remaining pinned to full-charge capacity
while 510 mA was still flowing. That confirms, from inside the EC, the capping
that Windows showed.

**The gauge's own status word (SBS BatteryStatus, with the FC / DSG / TCA
bits) is not in these ranges.** `0x0571` is a usable charge-state signal, but
it is the EC's own summary. There is no route to the pack's word from the OS
either: the DSDT defines no SMBus host controller (`ACPI0001`), so neither
Linux's `sbs`/`sbshc` drivers nor anything else can query the pack directly.
The EC is the only master on that bus.

## The ACPI reporting layer: what the OS is actually told

Both OSes get battery status from the same Control Method Battery,
`\_SB.BAT0` (`kbctrl/dsdt.dsl` ~122479). Its methods read EC fields.
Resolving the field offsets (`Field` blocks at ~121430-121710):

| field | location | use |
|---|---|---|
| `XIF1` | `0x0402` | design capacity |
| `XIF2` | `0x0404` | last full capacity |
| `XST0` | `0x0432` | battery state |
| `XST1` | `0x0434` | present rate -- the `ec_rate` column |
| `XST2` | `0x0436` | remaining capacity |
| `XST3` | `0x0438` | present voltage |
| `CYCN` | `0x04A6` | cycle count |
| `BSOK` / `BPST` | `0x0490` bits 2 / 1 | status OK / battery present |
| `ISDB` | `0x0497` bit 0 | unknown flag gating the rescaling below |
| **`CGLM`** | **`0x07B9` bits 6:0** | **the charge threshold** |

Two pieces of logic in there change what the OS sees.

**1. `0x07B9` sets a "charge limiting" flag, whether or not anything limits.**
In `_BST`:

```
If ((CGLM <= 0x63) && (CGLM >= One))   // threshold 1..99
    state = XST0 | 0x08                 // ACPI _BST bit 3 = "charge limiting"
```

The firmware tells the OS the battery is being held back whenever `0x07B9`
holds 1-99. It never checks whether the EC enforces it, and HANDOFF.md
measured that it does not. On Linux, `hydroc` wrote 80 there, so throughout
those runs the firmware reported "charge limiting" while the pack charged to
100%. (Whether the Linux ACPI battery driver surfaced the bit depends on the
kernel version; the runs' `status` column still read `Charging`.) Under
Windows, `0x07B9` was 0 and the flag was never set.

This is the reporting layer claiming a limit that the control layer does not
apply. It is worth knowing before trusting any OS-level "limited" indication
on this platform, and it is a reason for HydroControl not to write `0x07B9`
at all.

**2. For the first 50 cycles, reported capacity is rescaled.** In `_BIF` and
`_BST`, if `ISDB == 1` and `CYCN < 50`:
- `_BIF` reports **design** capacity (`XIF1`) as the last-full capacity,
  instead of the gauge's real figure (`XIF2`)
- `_BST` reports remaining as `XST2 x XIF1 / XIF2`, scaled up to match
- `_BTP` (the alarm trip point) is scaled the other way

A pack under 50 cycles therefore looks like it has its design capacity, with
the percentage unchanged. This hides early wear, not a charge limit, and at
134 cycles this pack is past it. It does establish that the firmware does
reshape battery reporting, which is the question the user raised.

**The capacity step in the Linux record is a pack swap.** `charge_full` read
6400000 (= design) in `charge-profiles` (09-07) and `phantom-check` (09-24),
and 5800000 from `ceiling-test` (09-27) onward. Per the owner, the earlier
runs were on a **defective replacement pack**, and from 09-27 on the
**original pack** that shipped with the laptop, the one in the machine now.
So the Linux captures split by pack:

| pack | runs | charge current |
|---|---|---|
| replacement (defective) | `charge-profiles` 09-07 | up to 5.0 A |
| replacement (defective) | `phantom-check` 09-24 | pinned ~2040 mA |
| original | `ceiling-test` 09-27, `switch1` / `profile-stationary` 09-28 pm, Windows Test 1 09-30 | pinned 2006-2040 mA |
| original | `wmi-ceiling` 09-28 am | up to 5.4 A |

Two consequences:
- **The 2 A / 5 A split happens on both packs**, so it is not a property of
  one pack. That weakens a fixed gauge request as the explanation and points
  at conditions at plug-in.
- **Both packs settled at ~4.169 V/cell at full** (`charge-profiles` and
  `phantom-check` on the replacement; `ceiling-test` and `wmi-ceiling` on the
  original). So both sat on the 4.20 V step. The charge target register itself
  was only logged from 09-28, on the original pack.

**Why the replacement pack was pulled, and what it says about gauges.** Per
the owner, it had **77-78 cycles and reported 0% wear**. It was RMA'd because
the laptop **shut off without warning with charge still showing**: first at
about 30%, rising to about 46% by the time of the RMA call.

- At 77-78 cycles it was past the first-50-cycles rescaling, so the firmware
  passed the gauge's own figures through. **The gauge itself believed the
  pack still held its full 6400 mAh design capacity.** Real packs lose a few
  percent by then; the original shows 9% at 134 cycles.
- A gauge that stops re-learning capacity keeps computing percentage against
  a figure the cells no longer hold. The percentage reads increasingly high,
  so the pack empties with "charge remaining", and the shutdown point climbs
  as the gap grows -- 30% -> 46%.
- A weak cell produces the same symptom by a different route. The pack's
  protection cuts power when any single series cell reaches its minimum
  voltage, with no OS warning, while the gauge's pack-level percentage still
  shows charge.

Either way it was a genuine pack fault, not firmware. **This resolves
HANDOFF.md's "30% shutdown" open question:** it was the replacement pack, and
the unclean-shutdown count tapering off tracks the swap back to the original.
It is also a concrete case of a gauge misreporting badly -- by failure, not
design -- and a reason to prefer resting voltage over percentage whenever the
two disagree. The deep-discharge test below doubles as a check that the
original pack's percentage is honest at the bottom.

**The low EC RAM "fourth address space" is the `0x04xx` window.** HANDOFF.md
decoded low EC RAM (`ec_sys`, `0x00-0xFF`): current at `0x34`, charge at
`0x36`, voltage at `0x38`, design/full at `0x02`/`0x04`, PL1/PL2 at
`0x6A`/`0x6B`, capacity at `0xAB`. Those are the offsets of `XST1`, `XST2`,
`XST3`, `XIF1`/`XIF2`, the live PL mirror `0x046A`/`0x046B`, and `0x04AB`, all
minus `0x400`. The DSDT's `ECMP` region (EmbeddedControl, `0x00-0xFF`) and
`ECXP` (MMIO, base + `0x400`) appear to be two doors onto the same data.

## The layered model: how charging is actually decided

Taken together -- the firmware decode (EC-FIRMWARE-FINDINGS.md), this
analysis, and every measured run on both OSes -- charging on this machine
looks like four layers, with the user-facing profile as one of the smallest
inputs:

1. **The pack's own gauge (smart battery / BMS).** It measures the cells and
   computes the percentage, `charge_now` and "full". Under SBS it also
   *requests* a charge current and voltage, and it carries its own
   temperature and ageing logic. The EC is the only master on its SMBus. No
   OS, no Control Center and no driver talks to it; they see the EC's cache.
   Everything the OS reports about the battery originates here.
2. **The EC's charge-target logic.** `0x0522` = pack maximum minus a per-cell
   reduction chosen by a decision tree. Its inputs, as decoded:
   - battery temperature (`0x0502`, copied to `0x04A2`)
   - cycle count (`0x04A6` -> `0x0A56`, threshold 550)
   - an accumulated stress counter (`0x09C9:0x09CA`, fed from the temperature
     copy) -- time spent hot and full, the thing that ages lithium cells
   - a gate the EC fetches from the pack (`0x0A5C`)
   - the cell count

   **`0x07A6` bits 5:4 are consulted at only two branches.** The rest ignore
   the profile.
3. **The charger IC.** It executes the plan: constant current to the target,
   then constant voltage with the current tapering. The 78-89% stutter fits
   that handover.
4. **The OS / Control Center.** Two bits in `0x07A6`, written at start-up,
   resume and on a user change. That is the whole of it.

**What this explains.** This pack is on the **most reduced step**: 16800 mV
against a 17800 mV rating, 250 mV per cell below maximum, whatever profile is
set. According to the decode, Stationary asks for 200 mV below maximum
(4.25 V/cell) and Balanced 100 mV (4.35 V/cell). **Both are milder than what
this pack already gets.** So Stationary has nothing to add here, and every
measurement agrees: the target never moved with the profile, including across
a mid-charge switch (`switch1.csv`, 238 samples).

That reconciles the evidence without a broken EC. A pack whose telemetry puts
it on a milder step -- cooler history, fewer cycles, less accumulated stress --
would land where Stationary's 200 mV is the stricter limit. That owner would
see charging end early, and the gauge would then declare full and walk the
percentage to 100% with no current behind it: the outside report, produced by
the same firmware on a different pack.

**How sure to be.**

| claim | status |
|---|---|
| target 16800 mV on every run, both OSes, every profile, 28-36 C | **measured** |
| target unmoved by a mid-charge profile switch | **measured** |
| Control Center adds nothing but the profile bits | **measured** (code trace, service and driver) |
| the decision tree and its inputs | **decoded** from the firmware, never seen changing step |
| accumulated stress is why this pack is on the strictest step | **inferred** -- the counter is unreadable from either OS |
| another pack would land on a milder step where Stationary bites | **inferred** |

**What would test it.**
- **Energy, not percentage.** Charge to "100%" under Stationary and under High
  Capacity, run the same fixed load to shutdown, and compare runtimes (or use a
  wall meter on the charger). This is the only test that does not trust the
  gauge. Equal runtimes mean no hidden limit exists on this pack.
- **A step change.** Log `0x0522` with temperature and cycle count over weeks.
  A move with the profile unchanged confirms the tree is live and shows which
  input drove it.
- **A second pack.** The same probe on the outside reporter's machine, or the
  RMA pack, would show whether the starting step differs between packs.
- **A deep-discharge cycle.** The participant's condition, and the trigger
  for gauge recalibration. See [Next: the deep-discharge test](#next-the-deep-discharge-test).

**For HydroControl.** Show the real protection rather than implying one:
"charge target 4.20 V/cell, 250 mV below the pack's rating" is true, readable
from Linux today (`0x0522`, `0x030E` big-endian), and more informative than a
profile name. Keep the profile selector, described as a secondary setting that
applies only when the EC's own protection is mild.

## Next: the deep-discharge test

The one condition from the outside report never reproduced: **discharge to
~5%, then charge under Stationary.** In the layered model this is also the
classic trigger for a gauge **recalibration cycle**, where the gauge re-learns
full-charge capacity and can reset what "full" means. A relearn can produce
the reported pattern (percentage walking to 100% at zero current) with or
without Stationary. So the participant may have seen the gauge recalibrating
rather than the profile acting. The run is designed to tell the two apart.

**Procedure.**

1. Start the logger **before** discharging, so the discharge is recorded too.
   On Windows (admin shell):
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\JunkOS\Documents\HydroControl-main\win_battery_log.ps1" -Interval 5 -Out "C:\Users\JunkOS\Documents\HydroControl-main\win-test2-deep.csv"
   ```
2. Leave Stationary set. Discharge unplugged (about 1.5 h from full at ~50 W idle).
3. **Plug in at ~6%.** On this Windows install the critical level is 5% with
   action *hibernate*, and the low warning fires at 6%. Hibernating stops the
   logger and blurs the plug-in moment. Do not leave the pack sitting empty.
4. Charge to full plus ~30 min (about 3 h from 6% at 2 A).
5. Dump at full (`win_ec_dump.ps1 -Out ec-dump-deep-full.json`) before unplugging.

Only the starting charge level differs from Test 1. The 5 s sampling does not
touch the battery, so the 78-89% stutter can be compared too.

**What counts as a finding.**

| observation | reading |
|---|---|
| `gauge_fcc_mAh` (`0x051C`, 5800 now) changes during or after the cycle | **gauge recalibration** -- the most likely source of the participant's observation |
| charging ends below 100%, or the percentage climbs with current near 0 | check `gauge_fcc_mAh` at the same moment: moved = recalibration; unmoved = the EC |
| `chg_target_mV` leaves 16800, or `0x07C3` leaves `0x0D` | the EC's decision tree changed step |
| stutter reappears near 80% | charger CV handover, if it also appears under High Capacity |
| voltage at 6% (on discharge) | ~3.4-3.6 V/cell = the original pack's percentage is honest at the bottom; ~3.0-3.2 = it reads high. HANDOFF.md's "30% shutdown" discriminator: that shutdown was the replacement pack, so this is now a health check on the original |

## This can run from Linux

Nothing in the charge path is Windows-specific:
- The service's only battery write is the `0x07A6` profile bits, which
  `hydroc` already makes byte for byte.
- The driver is a passive bridge.
- `_OSI` resolves the same under both OSes.

Linux also reads everything the Windows logger reads, and more:

| data | Linux route |
|---|---|
| `0x05xx` gauge cache, `0x0522` target | `acpi_call` `\_SB.INOU.ECRR`, readable per EC-FIRMWARE-FINDINGS.md |
| `0x04xx` live window, `0x07xx` settings | `ECRR` as today (skip the fan tacho `0x0464/5`, `0x046C/D`) |
| low EC RAM | `ec_sys` -- the same data as `0x04xx` (see above), a second read path |
| sysfs `power_supply` | derived from `_BST`/`_BIF`, so the same numbers Windows gets |

**One Linux-specific precaution:** do not let `hydroc` write `0x07B9`. It is
the `CGLM` field, and any value 1-99 makes the firmware report "charge
limiting" (above), which would contaminate the reporting that this test is
trying to read. Set it to 0 or leave it untouched.

A Linux logger needs the new columns added: `0x0514` (gauge %), `0x0518`
(remaining), `0x051C` (full-charge capacity), `0x050A`/`0x0510` (signed
current), `0x0571` (charge state), `0x0566` and `0x0542` (unknowns), next to
what `charge_profile_probe.py` already logs.

The case for doing it on Windows instead is only that the tools are running
here now, and it matches the participant's setup exactly if they were on
Windows. The result should be the same on either OS.

## EC space not yet explored (read-only candidates)

| range | status | why look |
|---|---|---|
| `0x0580-0x05FF` | never dumped | the rest of the gauge-cache block; the 2 A setpoint or the pack's status word could live here |
| `0x0340-0x03FF` | never dumped | beyond the static pack block, which mirrors at `0x0320` |
| `0x0700-0x07FF`, all of it, in the three states | only single registers read | the runbook's settings-space diff was never taken. Compare charging / full / discharging, and 2 A vs 5 A regime |
| `0x04xx`, all of it, in the three states | partially | the live window; the `XST`/`XIF` fields are identified, the rest is not |
| `0x0F60-0x0FFF` | unread | past the fan tables at `0x0F00-0x0F5F` |
| `0x0600-0x06FF`, `0x0000-0x02FF` | unknown whether mapped | one pass settles it: unmapped space reads `0xFF` throughout |
| `0x08xx-0x0Axx` | **unreadable** (all `0xFF` via `ECRR`) | holds the decision-tree inputs (`0x0A5C`, `0x09C9:0x09CA`). Reaching it needs the `0x8A-0x8E` mailbox, which means **writing** EC RAM to drive it. Not a read-only test; if tried at all, from Linux, deliberately and separately |

All but the last are read-only and can be taken with `win_ec_dump.ps1
-Ranges ...` here or `ECRR` on Linux. Keep each pass small (a few hundred
reads at 20 ms spacing), never under load, and always skip the fan tacho
registers.

## Performance modes and the profile button

Static trace of `GCUService.exe`, same method as the battery analysis. BIOS
set to **performance modes** (not fan profiles). This machine uses
`MyControlCenter.MyFan.MyFanManager_RamFan1p5`.

DESIGN.md 3.7 had the first half right: the EC announces, the host decides.
What was missing was what the host writes. **The answer is mostly one
register, `0x0751`, plus "power limits = 0". The EC then applies its own
per-mode defaults.**

### The chain

```
button
 -> EC raises WMI AcpiTest_EventULong, code 0xB0
 -> MyControlCenter.WMIEC.WMIHandleEvent        (0xA4-based switch, index 12: "OSD_FanModeSwitch")
 -> MyFanCtrl.ModeSwitchChanged()
 -> MyFanManager_RamFan1p5.ModeSwitchChanged()  picks the next mode from the current one
 -> SetModeSwitchChange(mode)                   OSD, then UserSet_Mode1/2/3/4
 -> SetUserProfile(mode)                        the EC writes below
```

Mode numbers (`OperatingMode`): **0 Office, 1 Gaming (Balanced), 2 Turbo
(Beast), 3 Custom.** The cycle order depends on turbo support, app type and
customer ID. The general path is Office -> Gaming -> Turbo -> Custom ->
Office; other paths skip Custom.

### What a mode change writes

**`SetFanMode(mode)` -- the mode encoding, in `0x0751`:**

| mode | `0x0751` | custom latch `0x0727` bit 6 | `0x0726` bit 7 |
|---|---|---|---|
| 0 Office | **`0xA0`** (bits 7 + 5) | cleared | cleared |
| 1 Gaming | **`0x00`** | cleared | cleared |
| 2 Turbo | **`0x10`** (bit 4) | cleared | cleared |
| 3 Custom | `0x00`, or `0x10` with overclocking | **set** | **set** |

Fan boost ORs in bit 6 (`0x40`) in any mode. `GetFanMode()` decodes it the
same way: bit 7 = Office, bit 4 = Turbo, neither = Gaming. So on this firmware
`0x0751` carries the **performance mode**, not only the manual-fan bits the
kernel driver names (`FAN_MODE_USER` b7, `HIGH` b5, `TURBO` b4, `BOOST` b6).

**`SetUserProfile(mode)` -- the rest:**
1. `SetFanMode` as above.
2. The mode's fan table, loaded into the EC tables at `0x0F00-0x0F5F`
   (`SetFanTable`) -- populated before anything depends on them.
3. **PL1 / PL2 / PL4 = 0 for Office, Gaming and Turbo** (`0x0783-0x0785`),
   meaning "firmware default". Only Custom writes the profile's own values.
4. TCC offset, CPU core-voltage offset, GPU clock offsets and target
   temperature, NVIDIA Dynamic Boost (Turbo: `SetDynamicBoostforTurboMode`),
   and a cTGP enable plus target.
5. `UserSet_Mode*` also writes the NVRAM variable **`PowerMode`** =
   mode. That is `UniWillVariable` offset `0x2F`, so it survives a power
   cycle (presumably for the BIOS to restore at POST). The Linux captures
   read `01` there.

### The EC holds the per-mode power limits

With PL = 0, the EC uses defaults it stores **per mode**. The service reads
them (for display) from:

| mode | PL1 | PL2 | PL4 | D-state | TCC offset |
|---|---|---|---|---|---|
| Gaming | `0x0730` | `0x0731` | `0x0732` | `0x0733` | `0x07D8` |
| Office | `0x0734` | `0x0735` | `0x0736` | `0x0737` | `0x07D9` |
| Turbo | `0x07A7` | `0x07A8` | `0x07A9` | `0x07AA` | `0x07DA` |

This corrects DESIGN.md 3.7's "no shipped config defines the per-mode power
limits". No *config file* does, but the values exist, in the EC, and the EC
picks the set from the `0x0751` encoding by itself. Confirmed on AC in the
read-back below.

### The LED

`SetPowerLedStatus(mode)` writes `0x07A5` bits 1:0 (Gaming 0, Office 1,
Turbo 2), the field the kernel driver calls `POWER_LED`. **It has no callers
in this fan manager.** So on this machine the mode LED is not driven through
`0x07A5`, which agrees with DESIGN.md 3.7's null there (that test covered
bits 2, 4 and 7 of `0x07A5` in any case). For the preset modes the only
mode-specific EC write is `0x0751`. The custom latch (`0x0727` bit 6) gives
white, as DESIGN.md already found. **The EC derives the LED colour from
`0x0751`** -- confirmed on the hardware, see the read-back below.

### Why Linux never found the trigger

- There is no EC-side trigger to find. The EC only raises `0xB0`. With no
  listener nothing changes, exactly as DESIGN.md recorded.
- HydroControl's presets arm the custom latch and write explicit PLs. From the
  EC's view that is always Custom, so it always shows white and never uses its
  per-mode defaults.
- `0x0751` bit 7 was treated as `FAN_MODE_USER`, the "one-way door". That bit
  stopped the fans on Linux, **with the fan tables empty** (HANDOFF.md:
  "populate the tables BEFORE setting the enable bit"). Control Center sets
  bit 7, with bit 5, for Office, **after** loading a table. Setting bit 7 on
  its own over empty tables is a different operation from what Windows does.
  That is a hypothesis, and it still gets tested with tables populated and a
  power cycle ready.

### What to replicate on Linux

A native-mode implementation for the profile button (`KEY_F14`) would be,
per mode:

1. load that mode's fan table into `0x0F00-0x0F5F` (HydroControl already does
   populate-then-enable)
2. clear the custom latch (`0x0727` bit 6)
3. write `0x0751` = `0xA0` / `0x00` / `0x10` (+`0x40` for boost)
4. write PL1/PL2/PL4 = 0 so the EC uses its per-mode defaults
5. optionally show the defaults, read from the table above

Leave the NVRAM `PowerMode` write alone unless there is a reason for the mode
to survive a power cycle. It is non-volatile and shares `UniWillVariable`
with the GPU-mode byte.

### Read-back on the hardware (2026-09-30) -- confirmed

The button was pressed through each mode, with a `win_ec_dump.ps1` dump of
`0x0720-0x07DF`, `0x0460-0x047F` and `0x0F00-0x0F5F` after each. First on
battery, then Office and Beast again on AC. Files: `ec-mode-balanced.json`,
`ec-mode-beast.json`, `ec-mode-custom.json`, `ec-mode-office.json`. The
Office and Beast files hold the AC dumps; they overwrote the battery ones,
whose key values are recorded below. The dump timestamps show the button
cycling Balanced -> Beast -> Custom -> Office, the order traced in code.

**Mode encoding and LED -- all as traced, and the LED follows `0x0751`:**

| mode | `0x0751` | `0x0727` | `0x0726` | PL written `0x0783-5` | `0x0743` bat / AC | LED |
|---|---|---|---|---|---|---|
| Office | `0xA0` | `0x80` | `0x08` | 0 / 0 / 0 | `0x02` / `0x01` | **green** |
| Balanced | `0x00` | `0x80` | `0x08` | 0 / 0 / 0 | `0x02` / -- | **blue** |
| Beast | `0x10` | `0x80` | `0x08` | 0 / 0 / 0 | `0x06` / `0x07` | **purple** |
| Custom | `0x00` | **`0xC0`** | **`0x88`** | 75 / 75 / 125 | `0x06` / -- | **white** |

`0x07A5` read 0 in every mode, confirming it is not the LED path here. The EC
derives the colour from `0x0751`, and the custom latch overrides it to white.
This also explains DESIGN.md 3.7's "latch off -> blue": Linux never wrote
`0x0751`, so it sat at `0x00` = Balanced = blue. (DESIGN.md's colour list had
Office and Balanced swapped.)

**The EC's per-mode defaults (identical in every dump):**

| mode | PL1 | PL2 | PL4 raw | D-state | TCC offset |
|---|---|---|---|---|---|
| Balanced (`0x0730-3`, `0x07D8`) | 75 | 75 | 125 (= 250 W, half-scale) | 1 | 5 |
| Office (`0x0734-7`, `0x07D9`) | 45 | 45 | 125 (= 250 W) | 1 | 15 |
| Beast (`0x07A7-A`, `0x07DA`) | **205** | **205** | 200 | 1 | 5 |

Beast's 205 W is the figure HANDOFF.md says RAPL's limit fields always report.
Whether Beast's raw PL4 of 200 is half-scale (400 W) or literal is open.

**The EC applies them by itself on AC, and caps everything on battery:**

| live mirror | Office, AC | Beast, AC | every mode incl. Custom, battery |
|---|---|---|---|
| PL1 `0x046A` | **45** | **205** | 35 |
| PL2 `0x046B` | **45** | **205** | 35 |
| PL4 `0x046F` raw | **125** | **200** | 40 (= 80 W) |

So writing `0x0751` plus PL = 0 is sufficient: the EC selects the matching
default set. On battery, a fixed 35 / 35 / 80 W cap overrides every mode,
Custom's explicit 75 / 75 / 250 included. That is worth remembering when
reading any Linux power-limit test taken unplugged.

**Per-mode fan curves** (EC tables, CPU; GPU tables follow the same pattern):

| mode | below 55 C | points | top UpT | max duty |
|---|---|---|---|---|
| Office | **0%** | 8 | 69 C | **55%** |
| Balanced / Custom | **0%** | 9 | 81 C | 80% |
| Beast | **0%** | 10 | 84 C | 90% |

Two consequences for HydroControl:
- **Every vendor curve stops the fans below 55 C.** That is the silent band
  HANDOFF.md says HydroControl gives up on purpose (`MIN_DUTY` 25%), pending
  whether the EC's emergency ramp survives a custom curve (Next #2).
- **Office holds the fans at 55% however hot it gets.** Shipping that implies
  the vendor relies on an EC thermal override above the table. That makes
  Next #2 more likely to come out "yes" -- but it is still inference, and
  still needs testing before HydroControl drops its floor.

`0x07C6` read `0x04` (universal fan control on) and `0x07C5` read `0x00` (no
split tables) in every mode. Control Center runs its curves through the same
universal-fan path HydroControl uses.

**Also moved between modes, not yet identified:**

| addr | Office | Beast | other |
|---|---|---|---|
| `0x0463` | `0x0F` | `0x05` | `0x05` for Balanced and Custom -- bit 3 tracks Office |
| `0x07C4` | `0x20` | `0x38` | AC dumps only |
| `0x0745` / `0x0746` | `0x00` / `0x00` | `0xFF` / `0x19` | Dynamic Boost targets (`ADDR_DynamicBoost_*`), mirrored at `0x07D4` / `0x07D5` |
| `0x07CC` bit 7 | clear (battery) | set | set in the other battery dumps; nothing in the mode code writes it -- possibly timing |

### What this settles for Linux

The recipe in "What to replicate on Linux" is confirmed: fan table, latch
off, `0x0751`, PL = 0. The EC does the rest, including the LED. The one step
still carrying risk is `0x0751` bit 7 for Office. Windows sets it only with
the tables populated and universal fan control on (`0x07C6` = `0x04`), and
that is the state to reproduce before trying it. Balanced (`0x00`) and Beast
(`0x10`) do not touch bit 7 and are the safe first tests.

## RGB: the keyboard and the chin bar

Static trace of `GCUService.exe`. Both devices go through one protocol layer,
`LightingModel.LM_ITE_RGB`:
- keyboard PID list `0xCE00, 0x6000-0x6004, 0x6006, 0x6007, 0x600A,
  **0x600B**`
- light-bar list **`0x7001`**, `0x7000`

Colours and effects reach the service from the frontend over MQTT. The
per-device classes (`HIDKeyboard`, `HIDRGBLightbar`, ...) translate them into
the packets below.

### The packets (8-byte feature reports, report ID 0)

| cmd | layout (after the report ID) | notes |
|---|---|---|
| `08h` effect | `08, control, effect, speed, light, colorIndex, direction, save` | `control`: 1 off, 2 default, 3 welcome (boot animation), 4 night |
| `09h` brightness | `09, 02, level, ...` | |
| `12h` / `16h` / `26h` | row / picture transfers | per-key "UserMode" (effect `0x33`) |
| `14h` colour | `14, controlblank, index, R, G, B, 0, save` | with `save = 1` the service sleeps 100 ms, consistent with a flash write |
| `1Ah` timeout | `1A, control, enable, time, 0, 0, 0, save` | idle timeout / welcome-timeout effect |
| `80h` | read firmware version (`Ver_High`, `Ver_Low`, `Ver_Test`, `Ver_Customer`) | the service picks the device type from this |
| `88h` | read the current effect (control, effect, speed, light, colour, direction) | lets software read back what the device is running |

**HydroControl's light-bar packet uses the same layout.** Its "variant" byte
(`0x22` for `0x7001`, taken from tuxedo-drivers) sits in Control Center's
`control` position, and the byte HydroControl leaves at 0 is `save`. Control
Center's default control value is 2. `0x22` works on this bar, as measured,
so the high nibble is presumably a device selector. Unconfirmed.

### Keyboard effects

`Translate_ITE_EffectIndex` maps UI effects to firmware codes. `GetEffectType`
routes each one:
- `_APEffectList` = {21, 12, 13, 15, 23}: animated by the host
- 51: per-key rows
- 34: music, driven by host audio
- **everything else: firmware** (one `08h` packet; the keyboard animates by
  itself)

| code | Control Center name | driven by | HydroControl (`ite8291r3-ctl`) |
|---|---|---|---|
| `0x01` | Single / static | firmware | per-key static instead |
| `0x02` | Breathing | firmware | breathing |
| `0x03` | Wave | firmware | wave |
| `0x04` | Reactive | firmware | **"random"**, same code |
| `0x05` | Rainbow | firmware | rainbow |
| `0x06` | Ripple | firmware | ripple |
| `0x09` | Marquee | firmware | marquee |
| `0x0A` | Raindrop | firmware | raindrop |
| `0x0E` | Aurora | firmware | aurora |
| `0x11` | Spark | firmware | **"fireworks"**, same code |
| `0x12` | Flash | firmware | -- |
| `0x13` | Mix | firmware | -- |
| `0x16` | RippleO | firmware | -- |
| `0x18` | StarSpark | firmware | -- |
| `0x19` | StarHitting | firmware | -- |
| `0x21` | Thinking | firmware | -- |
| `0x34` | ColorfulWave | firmware | -- |
| `0x0C` / `0x0D` / `0x0F` / `0x15` / `0x17` | Stack / Impact / Neon / Gaming / Alphabet | **host** | -- (would need a frame loop) |
| `0x22` | Music | host audio | -- |
| `0x33` | UserMode (per-key) | rows | per-key, yes |
| -- | BatteryPercent | host renders the charge level on the keys | -- |

The keyboard's effect read-back table (`Translate_LM_EffectIndex`) knows only
the classic set: 1-6, 9, 10, 14, 17. The newer firmware codes (`0x12`, `0x13`,
`0x16`, `0x18`, `0x19`, `0x21`, `0x34`) may depend on keyboard firmware
version. They are worth a probe: each is a single volatile `08h` packet with
`save = 0`.

### Reactive effects need an EC bit that HydroControl never sets

`Enable_EC_OnkeyPressed(effect, direction)`: for effects `0x04`, `0x06`,
`0x0E` and `0x11` with direction = "on key press", Control Center sets **EC
`0x0741` bit 3**. The kernel driver names it `ITE_KBD_EFFECT_REACTIVE`; it
evidently has the EC forward key presses to the keyboard controller. It is
cleared again (`Disable_EC_OnkeyPressed`) for other effects.

HydroControl offers a `reactive` parameter on exactly those four effects
(`kbctrl/kbctrl/hardware.py` `EFFECT_PARAMS`) and passes it in the keyboard
packet, **but nothing in `hydroc/` or `kbctrl/` writes `0x0741` bit 3.** So
reactive mode on Linux most likely does nothing. Test it, then set the bit
when a reactive effect is applied and clear it otherwise. Use a
read-modify-write: bit 0 of the same register is `ENABLE_MANUAL_CTRL`, the
master switch (HANDOFF.md #1). All four mode dumps read `0x0741` = `0x81`,
with bit 3 clear.

### Colour correction -- this keyboard gets none

`HID_Set_Color_14H` (RVA `0x1c73c`) passes colours through
`WKDColor.cheatRGB_*` before sending them. It is a lookup that swaps **exact
preset colours** for white-balanced ones, not a general transform, and the
switch is on `m_ITE_KB_Type` alone. The constructor at `0x167f8` defines the
`MEZone` series:

| type(s) | series | table |
|---|---|---|
| 2, 3 | — | `cheatRGB_4Zone` |
| 5, 6 | — | `cheatRGB_2ndME` |
| 11, 12, 13, 14 | `MEZone_2p1ndSeries` | `cheatRGB_2p1ndME` |
| 16 / 23 / 24 | — | `cheatRGB_HIDLightbar` / `_HIDLightbar2` / `_HIDLightbar3` |
| 17, 18, 19, 20 | `MEZone_2p2ndSeries` | `cheatRGB_2p2ndME` |
| 21, 22 | `MEZone_3ndSeries` | `cheatRGB_HIDKeyboard3` |
| 25 | — | none — raw (the chin bar) |
| **7, 8, 9, 10** | `MEZone_3p1ndSeries` | **none — raw** |

The last row is the one that matters. The constructor defines
`MEZone_3p1ndSeries = {7, 8, 9, 10}`, but the dispatcher never tests for it:
those types fall through the switch to the default and are sent raw.

**This keyboard is type 7** — firmware 34.3.0.0, `Ver_High` `0x22`, KBID at EC
`0x073C` = `0x18`. So Control Center applies **no** correction to it, and
HydroControl's raw `FF FF FF` is not a shortfall against Windows, it is the
same thing Windows sends.

The `HIDKeyboard3` table, for reference, since we recovered it:

| requested | sent |
|---|---|
| white `FF FF FF` | `7D FF B9` |
| red `FF 00 00` | `FF 00 00` |
| orange `FF A5 00` | `FF 7D 00` |
| yellow `FF FF 00` | `D2 FF ..` |

It lives in `hydroc.rgb.CHEAT_RGB` behind `correct=False`, which is where it
stays. Applying it here is what made white read purplish.

**Correction to an earlier reading.** This section used to say the LED vendor
(Liteon glossy / cloudy / CIE, Everlight / CIE) selected between tables, read
from EC support bytes `0x073D`, `0x0742`, `0x078E`. It does not.
`m_bitLiteon_glossy`, `_cloudy`, `_CIE_JP`, `_CIE_USUK`, `_CIE_FromFW` and
`m_bitEverlight` are declared fields with **zero reads or writes anywhere in
GCUService.exe** — vestigial. Of the four bytes we dumped
(`0x073C = 0x18`, `0x073D = 0x00`, `0x0742 = 0x22`, `0x078E = 0xFC`), only
`0x073C` feeds anything, and it is the KBID that resolves the type.

### The chin bar (`0x7001`)

`ILM_RGBLB_Init`: `0x7001` on usage page `0xFF03` is assigned type **25**.
For type 25:
- **Firmware effects only.** The per-LED light-bar paths
  (`Set_ITE_Effect_Type_UserMode_Lightbar*`, effect `0x33` plus a 65-byte
  frame) are used for types 16, 23 and 24, not 25. So Windows does not
  address this bar's LEDs individually either.
- Colour packet `14, controlblank, index, R, G, B, 0, save`, raw -- the same
  as `lb_palette()` with `save = 0`.
- Effects use the keyboard's code table. That explains the probe results
  recorded in `hardware.py`:

| code | HydroControl name | Control Center name | observed on the bar |
|---|---|---|---|
| `0x01` | static | Single | colour follows |
| `0x02` | breathing | Breathing | colour follows (from the list) |
| `0x03` | wave | Wave | **colour ignored** -- it is a rainbow wave |
| `0x04` | clash | **Reactive** | colour follows |
| `0x05` | catchup | **Rainbow** | **colour ignored** -- rainbow, so it would be |
| `0x11` | "flash" | **Spark** (fireworks) | bar goes off -- Spark is key-reactive; Flash is `0x12` |

The tuxedo-derived names were guesses. Mapped onto Control Center's table,
the "colour ignored" results are the rainbow effects doing what rainbows do.
**Codes never tried on the bar:** `0x06` Ripple, `0x09` Marquee, `0x0A`
Raindrop, `0x0E` Aurora, **`0x12` Flash** (the real flash), `0x13` Mix, `0x16`,
`0x18`, `0x19`, `0x21`, `0x34`. `lb_mode_probe.py` can walk them. They are
volatile, and a power cycle clears the bar.

### Flash writes that HydroControl makes, contrary to HANDOFF.md

By Control Center's layout, byte 7 of `08h`, `14h` and `1Ah` is **`save`**,
and `save = 1` writes the device's flash:

- **`hardware.py` `lb_off()` sends `1A 00 00 00 00 00 00 01`**: "timeout
  disabled, **save = 1**", on every chin-bar off.
- The probe scripts' "commit" (`kbctrl/ec_probe.py`, `ec_recover.py`,
  `kb_output_restore.py`, `kb_perkey_flush.py`, `kb_probe_nodes.py`,
  `charge_probe.py`) sends `1A 00 01 04 00 00 00 01`: "**enable** an idle
  timeout of 4, **save = 1**".
- The keyboard "save to onboard flash" option does this deliberately, and
  that is fine.

HANDOFF.md says "no code path here has ever written flash". For the chin bar
that is probably not true. It is harmless in effect -- a disabled timeout,
saved -- but it is a flash write per call. **`lb_off()` should send `save = 0`,
or drop the `1Ah` packet if the other three packets already turn the bar
off.** If a keyboard or bar ever seems to go dark by itself after idle, the
probe scripts' saved 4-unit timeout is the first suspect.

### Windows features HydroControl does not have

| feature | how Control Center does it | Linux route |
|---|---|---|
| boot / welcome animation | `08h` with control 3 + `save`, stored in the device | one saved packet per device, no daemon needed |
| idle timeout | `1Ah` enable + time | same packet, `save = 0` for a session-only setting |
| night mode | control 4 | untested |
| separate AC / battery brightness | `SetBrightness_ACDC`, switched on power events | daemon-side |
| backlight keys | EC scancodes `0xB1` (down), `0xB2` (up), `0xF0` (update) | check what `uniwill-laptop` maps them to |
| battery-percent, music, Stack/Impact/Neon/Gaming/Alphabet | host-driven frame loops | possible, but a daemon feature |
| persistent chin bar without the daemon | `save = 1` on `08h` / `14h` | trade-off: flash wear vs. a daemon restore at boot. Keep the restore as the default |

## Not covered here

- `GamingCenter3_Cross.dll` (the UWP frontend). It talks to the service only
  over MQTT and cannot reach the driver itself. Its battery page was not read.
- Windows' own battery stack (the ACPI battery miniport polling `_BST`/`_BIF`).
  It is not part of Control Center. Linux's ACPI battery driver evaluates the
  same methods.

`_OSI` was checked and is not a difference. The DSDT (`kbctrl/dsdt.dsl`
~31407) sets `OSYS` from a ladder of `_OSI("Windows 2001" ... "Windows 2015")`
only, with no Linux or other string. Windows 11 and Linux (which answers every
Windows `_OSI` string true by default) both end at `OSYS = 0x07DF`, so every
`OSYS` branch takes the same path under either OS. This does not hold if the
Linux kernel is booted with `acpi_osi=!` or a specific `acpi_osi=` override.
Worth confirming the kernel command line has none.
- `BIOS_OTA.exe`. Firmware updater; do not run it before Run A.
