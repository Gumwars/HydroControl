# Hidden setup offsets: undervolting on the stock BIOS

Extracted from the stock Eluktronics image `GMxIX9xN111ELU08.BIN`
(BIOS `N.1.11ELU08`, 2026-09-18) before flashing it. Reproduce with:

```
python3 ifr_extract.py GMxIX9xN111ELU08.BIN --lzma --grep voltage --options
```

Every question below is marked `suppress-if` in the shipped IFR: present in the
firmware, hidden from the menu. That is what Prema Mod exposes — it authors
none of these pages.

## How to reach them without modifying the BIOS

A suppressed question still reads its value from the same varstore offset, so
the setting is reachable by writing the offset directly. `setup_var.efi` or
`RU.efi` from an EFI shell do this. Most AMI setup variables are NV+BS with no
runtime attribute, so Linux cannot see them once booted — this is a pre-boot
operation.

**Check the stock menu first.** Eluktronics may leave some of these visible,
in which case none of this is needed.

## The gate

| varstore | offset | width | setting | values |
|---|---|---|---|---|
| `CpuSetup` | `0x0381` | 1 | **UnderVolt Protection** | `0` = Disabled, `1` = Enabled |

Its own help text: *"When UnderVolt Protection is enabled, user will not be
able to program under voltage in OS runtime. Recommended to keep it enabled by
default."* This is why `intel-undervolt` can read all five package domains and
report zero on a machine that is undervolted — reads answer, OS-runtime writes
are refused.

Set it to `0` only if you intend to undervolt from the OS. The BIOS applies its
own offsets regardless of this setting.

## Global core offset

| varstore | offset | width | setting | values |
|---|---|---|---|---|
| `CpuSetup` | `0x01DD` | 1 | Core Voltage Mode | `0` = Adaptive, `1` = Override |
| `CpuSetup` | `0x01DE` | 2 | P-core Voltage Override | absolute mV (Override mode only) |
| `CpuSetup` | `0x01E0` | 2 | **Core Voltage Offset** | magnitude in mV |
| `CpuSetup` | `0x01E2` | 1 | **Offset Prefix** | `0` = `+`, `1` = `−` |

Offsets are a magnitude plus a separate sign byte. **-40 mV is `0x01E0 = 40`
with `0x01E2 = 1`.** Writing the prefix wrong gives +40 mV, which is the
dangerous direction.

Help text for this domain: *"Uses Mailbox MSR 0x150, cmd 0x11. Range -50 to
+50 mV."*

## Per-P-core offsets — what Prema actually exposed

Eight P-cores, each a 2-byte magnitude and a 1-byte sign:

| core | magnitude | sign |
|---|---|---|
| P-core 0 | `0x0262` | `0x0272` |
| P-core 1 | `0x0264` | `0x0273` |
| P-core 2 | `0x0266` | `0x0274` |
| P-core 3 | `0x0268` | `0x0275` |
| P-core 4 | `0x026A` | `0x0276` |
| P-core 5 | `0x026C` | `0x0277` |
| P-core 6 | `0x026E` | `0x0278` |
| P-core 7 | `0x0270` | `0x0279` |

So Prema's default — -40 mV on every performance core — is `40` at each of
`0x0262`–`0x0270` step 2, and `1` at each of `0x0272`–`0x0279`.

## Other domains

| varstore | offset | width | setting |
|---|---|---|---|
| `CpuSetup` | `0x01E9` / `0x01EC` / `0x01EE` | 1 / 2 / 1 | Ring Voltage Mode / Offset / Prefix |
| `CpuSetup` | `0x02AF` / `0x02B2` | 1 / 2 | E-core L2 Voltage Mode / Offset |
| `CpuSetup` | `0x02DE` | 1 | Uncore Voltage Mode |
| `CpuSetup` | `0x0252`–`0x0260` | 1 each | VF Point 1–15 Offset Prefix |
| `CpuSetup` | `0x01F2`–`0x01F7` | 1 each | PLL voltage offsets (units of 17.5 mV, range 0–15) |

E-core cluster offsets carry a range of ±500 mV against the IA core domain's
±50 mV — the ranges are per-domain and not interchangeable.

## Neighbours to not hit by accident

These sit beside the voltage questions and change machine behaviour in ways
that are not obvious from a hex offset:

| varstore | offset | setting |
|---|---|---|
| `CpuSetup` | `0x010E` | Overclocking Lock (BIT 20 of `FLEX_RATIO` MSR 0x194) |
| `CpuSetup` | `0x0334` | IA CEP Enable |
| `CpuSetup` | `0x0335` | GT CEP Enable |
| `CpuSetup` | `0x00D6`–`0x00DD` | P-core Turbo Ratio Limit Ratio 0–7 |

`0x0334` is four bytes from nothing in this document and disabling Current
Excursion Protection is a real decision, not a side effect. Verify each offset
against a fresh `ifr_extract.py` run on the image actually installed.

## Caveats

**The offsets in this file are for `N.1.11ELU08`.** Varstore layouts move
between BIOS versions. Re-extract after any BIOS change rather than reusing
this table.

**Whether a global offset sums with per-core offsets is not established.** Both
go through Mailbox MSR `0x150` cmd `0x11` with different domain selectors, and
`intel-undervolt` only drives the five package-level domains. Setting both is
untested here; set one.

**-40 mV was Prema's default, not a validated figure for this silicon.** The
0x12B-and-later microcode already lowers the voltage the CPU requests of
itself, so an offset is subtracted from an already-reduced target, and on a
part that has taken Vmin shift it eats stability margin. Validate under
sustained load.
