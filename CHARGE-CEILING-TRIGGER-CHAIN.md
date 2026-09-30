# Charge ceiling: bank mapping corrected, chain verified

2026-09-28. This document corrects my earlier draft, which used a wrong bank
mapping and drew wrong conclusions from it. The repo's `EC-FIRMWARE-FINDINGS.md`
("CORRECTED" section) and `tests/test_ec_disasm.py` already state the correct
answer; this doc reconciles my notes with them and records what is proven by
byte decoding vs. merely reported.

## The bank mapping (`BANK_SIZE = 0x8000`)

`file = bank * 0x8000 + logical`, for `logical` in the `0x8000-0xFFFF` window;
below `0x8000` is common memory. The earlier `bank * 0x10000` was wrong in a
self-hiding way: `bank1*0x10000` and `bank2*0x8000` are both `0x10000`, so every
disassembly landed on the correct bytes while every bank number was wrong. Thunks
are keyed on `(bank, target)`, so thunk lookups never matched, and the ceiling
task (bank 2) was searched for in bank 1. Hence my earlier "bank1" labels and my
"unreachable / dead code" conclusion were both wrong.

Dispatchers, read directly from the bytes (each pushes state, sets `P1.0-P1.2`,
then `RET`-jumps through `DPTR`):

```
0x1100 -> bank 0   (P1.0=0 P1.1=0 P1.2=0)
0x1114 -> bank 1   (P1.0=1 P1.1=0 P1.2=0)
0x1128 -> bank 2   (P1.0=0 P1.1=1 P1.2=0)
0x113C -> bank 3
```

## The ceiling task is dispatched — verified chain

The ceiling lives at `bank2:0xC88C` (gate) → `bank2:0xC8BA` (comparison), i.e.
file `0x1C88C`–`0x1C8BA`. The reachability chain, each hop read from the bytes:

```
0x00EA6  LJMP 1582        jump table, 3-byte LJMP entries at fixed stride
0x1582   MOV DPTR,#86C2
         LJMP 1128        -> bank2:0x86C2
bank2:0x86C2 (file 0x186C2)
         LCALL 9272
         CLR A / MOV R7,A
         LCALL 1C84        thunk -> bank0:0xE6DE
         LJMP C88C         tail jump to the ceiling task
bank2:0xC88C (file 0x1C88C)
         ... gate ...      JNZ 0xC8B0 -> 0xC8BA
```

`ec_disasm.py` (with the corrected mapping) reports `bank2:0xC88C reached YES`
and `bank2:0xC8BA reached YES`. So the routine is in the call graph: callable,
not orphaned. Whether it *executes* at runtime is a separate question from
reachability, and turns on the gate.

## The gate is `0x07C3 == 4`

```
bank2:0xC88C  CLR A
             MOV DPTR,#0A51h
             LCALL CFEDh       CFED: write A->0x0A51 ; A = [0x07C3]^4  (Z if ==4)
             JZ  arm
             LCALL C49Ch       C49C: R7 = ([0x0770]==4)
             JZ  disarm
arm:         0x0742 |= 0x04 ;  if [0x0490]&4 -> comparison -> ORL A,#80 -> 0x07B9
disarm:      0x0742 &= ~0x04 ; 0x07B9 bit 7 = 0
```

`C49C` tests `0x0770 == 4`. `0x0770` is `ROMID_START`, reading `0xFF` across
`0x0770-0x07FD` with the `55 AA` marker at `0x077E/F` (`ec-before.txt`) — so that
branch is always false. The only live gate is `0x07C3 == 4`.

`0x07C3` is read-only in the firmware (19 `MOVX A,@DPTR`, no writes, direct or
indirect), compared against ~19 values (`0x02 0x04 0x06 0x07 0x09 0x0A 0x0C 0x0D
0x0E 0x0F 0x10 0x12 0x1A …`). It is a state byte written by something outside the
8051 core (BIOS/ACPI handshake or the SMBus engine).

**Provenance flag.** The repo's corrected findings assert "`0x07C3` reads `0x0D`
where `4` is required". I could not locate that measurement: `0x07C3` is not
among the registers captured by `footprint.csv`/`footprint.json` (whose columns
cover `0x07B9`, `0x0742`, `0x0741`, `0x07C6`, `0x0490`, `0x0497`) or any probe
in `*.py`. The `0x0D` value is *reported* in the repo, not *proven* here. It is
plausible (`0x0D` is one of the enumerated states), but it is an assertion whose
source I could not find.

## Corrections to my earlier draft

1. **"The sync routine `0xC81E` is dead" — wrong.** With the correct mapping,
   `bank2:0xC81E` is `reached YES`, called by `bank1:0xDE83`. It is callable.
   What is not settled is whether it runs at runtime and whether the SMBus read
   `F3 0A 54` succeeds; the observation that `0x0497` bit 0 is set while `0x07B9`
   is not rebuilt remains unexplained by reachability alone.
2. **"`0x0770` is an alternate gate" — wrong.** It is `ROMID_START`, `0xFF`, and
   that branch never fires. (This part of the earlier draft was already fixed.)
3. **"The feature is absent" — wrong.** The profile path is reachable and real.
4. **"The ceiling is reachable" via a bank-1 task table — mis-banked.** The
   ceiling is bank 2; the dispatch goes through the jump table at `0x00EA6`.

## The profile path (bank 2) — traced to its output

`bank2:0xBD69` (file `0x1BD69`) is `reached YES`. It reads `0x07A6` (profile,
bits 5:4) and selects a constant: Stationary (`0x20`) → `R3=0xC8` (200), Balanced
(`0x10`) → `R3=0x64` (100), plus 150/250 branches gated on `0x09C9/0x09CA` and
`0x0A56/0x0A57` telemetry.

`0xBE02` (file `0x1BE02`) computes a 16-bit value and stores it to `0x0522/0x0523`:

```
0x0522:0x0523 = [0x0A5A:0x0A5B]  -  (constant x [0x0A51])
```

where the subtraction is `[0x0A5A] - high(product) -> 0x0523` and
`[0x0A5B] - low(product) -> 0x0522` (16-bit, `0x0522` high / `0x0523` low). The
product is `constant x [0x0A51]` via `LCALL 72B3` (16x8 multiply).

The two operands, traced:

- `[0x0A51]` is a **charge-rate code**, `4`/`3`/`2`, selected at `0x1BC39` from
  charger status (`D320`/`D321`), and used elsewhere as `rate x 1040` = charge
  current in mA (`0x0A54:0x0A55`; `0x1BC5E`).
- `[0x0A5A:0x0A5B]` is a 16-bit accumulator **initialised from `[0x030E:0x030F]`**
  at `0x1BCFF` (the profile function's entry reads the `0x03xx` value into it).

`0x0522/0x0523` is then consumed:

- mirrored to `0x0836/0x0837` (`0x14177`, `0x15EDC`);
- nonzero → `0x0432 = 2` (`0x1314B`);
- nonzero → charge functions `C0E3`/`1BBE`/`1936` (`0x13A52`);
- selected against the raw `[0x030E:0x030F]` at `0x12CD0` and copied into
  `0x0A68:0x0A69`, the **SMBus data buffer** (`0x12CD0-0x12CEA`) — i.e. the
  profile result is sent to the battery/charger over SMBus, and the raw value is
  used when the profile is not active;
- passed as a 16-bit value to the `0x0A62` table-lookup `EBF8` (`0x14DD3`).

So the profile path is a genuine charge-control write: a hardware charge value
`[0x030E:0x030F]`, reduced by `profile_constant x rate`, sent to the charger over
SMBus. Direction is consistent with the profile semantics: Stationary (200)
reduces most, Balanced (100) least.

**Still not proven (needs a numeric capture):** the physical unit of
`[0x030E:0x030F]` (voltage vs. current vs. capacity) and therefore the unit of
the 200/150/100/250 constants. The constants are a *reduction magnitude scaled by
the charge rate*; pinning them to mV/mA/mAh depends on what the `0x03xx` source
register measures. `0x030E` is read-only via DPTR in the image and undocumented
in the project's register map.

## Register map (corrected banks)

| reg | dir (f/w) | role |
|---|---|---|
| `0x07C3` | read | master gate: `==4` arms the ceiling |
| `0x0770` | read | `ROMID_START`, `0xFF`; the `==4` branch is dead |
| `0x0742` bit 2 | r/w | charge-ctrl-active latch |
| `0x0490` bits 0/2 | read | guards (charging-active) |
| `0x0497` bit 0 | r/w | gates the `0xC81E` battery-read sync |
| `0x04AB` | read | battery capacity % |
| `0x07B9` | r/w | threshold + `CHARGE_CTRL_REACHED` (bit 7) |
| `0x07A6` | r/w | charging profile (bits 5:4) → profile constant |
| `0x0A51` | r/w | charge-rate scratch (4/3/2); multiplicand |
| `0x0A54/55` | r/w | charge-current value (rate × 1040) |
| `0x0522/23` | write | charge-control output of the profile path |
| `0x0A5A/5B`, `0x0A56/57` | read | 16-bit telemetry accumulators |
