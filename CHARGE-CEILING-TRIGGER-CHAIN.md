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

## Vendor corroboration (2026-10-01, Eluktronics support call)

First-party confirmation that this is not one unit misbehaving. From the call:

- **A second HYDROC-16 owner has reported the same thing.** Independent of us,
  and the first evidence that the behaviour is not specific to this machine or
  this 9%-worn pack.
- **The support engineer has observed it himself**, and described it in the
  same terms we arrived at: it does not do what it is advertised to do, but it
  is clearly doing *something*. That is the conclusion of this document,
  reached separately from the EC image.
- **Since the feature shipped in 2019, warranty claims and battery
  replacements have declined.** Weak evidence, and confounded — the fleet,
  the cell suppliers and the model mix all changed over the same seven years —
  but it points the same way as the register findings.
- **Tongfang/Uniwill do not share EC internals with their vendors.** So no
  authoritative description of this mechanism is coming. The reverse
  engineering in this repository is the only account of it that exists outside
  Uniwill.

### What this does and does not settle

Settled: the mechanism is real, it is shipping as designed rather than broken,
and the labels in Control Center misdescribe it. The earlier hypothesis that
this laptop was faulty — reasonable, and the reason the Windows trip happened —
is closed.

Not settled: what the EC actually decides. Nothing in the call touched the
voltage derating, `0x07C3`, or the profile constants. That remains ours.

### The 2019 date suggests a testable hypothesis

The feature shipped in 2019. The owner also has a **Mech 15 G3R** — an older
Uniwill machine, likely this one's predecessor — on which the same feature
*does* behave as advertised, as a percentage ceiling.

If both are true, the mechanism changed between generations while the UI labels
did not: a hard percentage stop on the earlier EC, adaptive voltage derating on
this one, same three profile names in front of both. That would account for
every observation at once — the advertising mismatch, "it's doing something",
and why the older machine looks correct.

It is testable with hardware already on hand. On the G3R, check whether
`0x07B9` holds a threshold that is actually enforced as a stop, and whether
anything resembling the `0x0522:0x0523` derating output exists. A percentage
stop there and derating here settles it.

## Outcome evidence: two Uniwill machines, same era (owner-reported)

Everything above is mechanism — what the EC decides, read out of its own
firmware. This is the first evidence about whether any of it *works*.

| machine | span | charge mode | wear |
|---|---|---|---|
| Mech 15 G3R (owner) | ~2 years | Stationary, almost exclusively | **3%** |
| Prometheus G2 (friend) | slightly under 2 years | unaware of the setting, so default | **~15%** |

A five-fold difference over a comparable span, on sibling Uniwill hardware. And
it is the direction theory predicts: holding a Li-ion pack off full is the
best-established intervention against calendar aging, so this is a predicted
effect turning up, not a correlation in search of a story.

### What would make a vendor engineer dismiss it

Named honestly, because this is going into a report:

- **n = 2.** Two machines, two owners, two usage patterns.
- **Different chassis.** G3R and Prometheus G2 differ in pack capacity, cell
  supplier and thermal design.
- **Usage pattern is the serious one.** "Stationary, almost exclusively" means
  a desk-bound machine on AC. Such a laptop barely cycles, and cycle count is
  a dominant wear term alongside time-at-high-SoC and temperature. If the
  Prometheus was cycled daily, that alone could account for the gap with no
  help from the feature.
- **Wear is a gauge estimate**, not a measurement — `charge_full` against
  `charge_full_design`, learned by the fuel gauge, and subject to drift and
  recalibration.

### Cycle count is what discriminates

The confound and the hypothesis make opposite predictions, and one number
separates them:

- **High cycle count on the G3R with low wear** → the machine *was* being
  cycled and still barely aged. Usage pattern is ruled out and the feature is
  doing the work.
- **Low cycle count** → the pack was simply held at a desk and rarely
  discharged. The confound stands and this evidence is weak.

The G3R is on hand and now ~5 years old, which makes its *current* numbers a
far better data point than a two-year recollection. Worth capturing alongside
the `compat_probe.py` run, since both want the same trip to that machine:

```
cat /sys/class/power_supply/BAT*/{charge_full_design,charge_full,cycle_count} \
    /sys/class/power_supply/BAT*/{energy_full_design,energy_full} 2>/dev/null
sudo python3 compat_probe.py
```

(`charge_*` or `energy_*` depending on which the G3R's driver exposes — hence
both, with errors suppressed.)

### How this bears on the current machine

This pack reads 9% wear at 135 cycles — worse, on its face, than the G3R's 3%
over two years of Stationary use. That is consistent with the generational
hypothesis above: whatever the earlier EC did, this one's voltage derating is
not reproducing it. It is not conclusive, because this pack is being RMA'd and
its gauge is the thing under suspicion.


## Correction: the derating is fixed, not adaptive (2026-10-03)

A replacement pack arrived at **0 cycles, 0.0% wear**, and was captured on EC
1.17 while charging — `0x0490` bit 0 set, so the value is live rather than a
stale read.

```
hw_base     0x030E:0x030F  big-endian      17800 mV   (4450 mV/cell)
chg_target  0x0522:0x0523  little-endian   16800 mV   (4200 mV/cell)
derating                                    1000 mV   ( 250 mV/cell)
```

| pack | cycles | wear | `chg_target` |
|---|---|---|---|
| original | 135 | 9% | 16800 mV |
| replacement | 0 | 0% | **16800 mV** |

**Identical.** The explanation this document carried — that the EC derates to
its strictest step *because* the pack is worn, and "the EC is aware and
handles a gently worn battery differently" — is wrong. There is no evidence
of adaptation to wear, because the only pack available was a worn one and the
reading was attributed to the wear it happened to have.

### What is true instead, and it is not worse

The EC charges this pack to **4.200 V/cell against a 4.450 V/cell rating**,
unconditionally. That is a 250 mV/cell reduction held permanently, which is
the single most effective thing anyone can do for Li-ion calendar life. The
protection is real, substantial, and always on.

What it is not is the thing the three profile names describe. Stationary,
Balanced and High Capacity do not select it, and `0x07C3` still reads `13`
where the percentage-ceiling code arms at `4`. Those labels remain a
misdescription of a mechanism that is doing something better than they
promise.

### What the old pack's wear meant — and a second correction

The first version of this section called 9% wear in 135 cycles "fast" and
concluded the pack was faulty. **Both halves are wrong**, and the mistake is
the denominator: that pack was the **original, two years old**. 135 cycles
across two years is light, desk-bound use, and 9% calendar aging over that
span is what a healthy cell does. Cycles were the wrong measure of a pack
whose life was mostly spent on AC.

So the original pack aged normally, under a fixed 4.200 V/cell charge, which
is the outcome that derating is for. Nothing about it needs explaining.

### 0% wear is not evidence of a healthy pack

The owner's history makes this concrete. Between the original and the current
one there was another replacement which **reported 0% wear at 77 cycles and
shut the laptop off without warning at a reported 46% charge**. A gauge
claiming full design capacity while the pack collapses under load is a gauge
that is not measuring anything.

That matters for the control above. The new pack reports `charge_full ==
charge_full_design` exactly, which is the fuel gauge's *initial assumption*
and not a measurement — the same reading the known-bad pack gave. So the
comparison is honestly stated as **reported** wear:

> the EC produced 16800 mV when the SBS data said 9% wear, and 16800 mV when
> it said 0%.

Since the EC reads the same pack data userspace does, that still rules out
adaptation to reported wear. What it cannot rule out is adaptation to some
truth the gauge is not telling either of us.

**Verify this pack's gauge before trusting its numbers.** `battery_watch.py`
logs charge and current; `battery_summary.py` integrates current over time
and compares against the `charge_now` delta. A ratio near 1.0–1.1 is an
honest gauge; below about 0.85 the reported charge is inflated. Running that
over the first few cycles would catch the previous failure early instead of
at a reported 46%.

That also removes a confound from the comparison in the previous section: if
the derating is fixed, the 3% vs 15% difference between the Mech 15 G3R and
the Prometheus G2 cannot be explained by one EC derating harder than another
in response to wear.

### What would still be worth measuring

Whether the derating responds to **temperature** rather than wear. The
thermal path is separate from and overlaps the charge threshold, and this
capture was taken at 29.85 °C — one point on a curve nobody has plotted. A
capture during a hot charge, after a gaming session, would test it for the
cost of one command.


## 79% and a threshold of 80: the experiment was never run (2026-10-03)

The measured capacity came out at **79% of design** on a machine whose charge
threshold register reads **80**. 80% of 6400 mAh is 5120; the measurement
implied ~5030 and was a floor. That is close enough to demand an explanation
rather than a shrug.

### Two hypotheses now fit every observation equally

**(a) Fixed voltage derating.** The EC holds 4.175 V/cell regardless of the
threshold. The threshold register is inert. 79% is what that voltage yields
on a 4.450 V/cell cell, and its proximity to 80 is coincidence.

**(b) The threshold is enforced, and the display is rescaled.** The EC stops
at 80% of true capacity and reports that as 100%. The whole reported 0–100%
scale maps onto 0–80% of the pack. 4.175 V/cell is simply the voltage at 80%
state of charge, not a target.

Both predict everything observed: charging "continues past a reported 80% to
100%", terminates near 4.175 V/cell, and a full discharge yields ~79% of
design. Under (b), charging past a *reported* 80 is expected — reported 80 is
true 64%, and the stop is at reported 100.

### The data that looked like a test is read corruption

Several logs appear to contain runs at other thresholds. They do not:

| log | date | anomalous threshold reads |
|---|---|---|
| `phantom-check.csv` | 2026-09-28 | 5 of 1883 (0.27%) — single samples at 47, 72, and three empty |
| `wmi-ceiling.csv` | 2026-09-28 | 1 of 981 (0.10%) — one sample at 46 |

Each is a lone sample with the neighbours back at 80 and the probe's own
`phase` column unchanged. Both logs predate the ACPI reply truncation fix of
2026-09-30, which is exactly the bug that produced garbled single reads.

`chargectrl.csv` has 424 consecutive rows at threshold 90, which looks like a
genuine run — but it ends at 09:23:04 still `Charging` at 90% with 2040 mA
flowing and `reached` still 0. **The log stopped; the charge did not.**

So no charge has ever been taken to termination with the threshold held at a
value other than 80.

### The discriminating test

Set the threshold to **60**, charge from the current 2% to termination, and
integrate `current_ma`.

| | delivered | terminates near |
|---|---|---|
| (a) fixed derating | ~5030 mAh, unchanged | 4.175 V/cell |
| (b) threshold enforced | ~3840 mAh | lower, around 4.0 V/cell |

Also worth watching at the moment of termination: `0x07B9` bit 7,
`CHARGE_CTRL_REACHED`. Every capture so far has been taken mid-charge with it
clear, which says nothing about whether it arms at the stop.

### This was removed from the UI prematurely

The threshold control was taken out of the app earlier the same day, on the
strength of "the threshold does nothing". That conclusion rests entirely on
observing a charge pass a *reported* 80% — which hypothesis (b) predicts just
as well. The removal should be revisited if (b) holds.


## The threshold works. (2026-10-03, same evening)

The test designed two sections above was run. Threshold moved from 80 to 60
part-way through a charge from 2%, logging every 30 s.

```
                 accepted    terminates at    reported stop
predicted (a)   ~4800 mAh     4.175 V/cell             100%
predicted (b)   ~2900 mAh     ~4.00 V/cell
OBSERVED         3054 mAh     3.9348 V/cell             47%
```

Current reached zero at 19:39:14 and stayed there; the voltage then relaxed
downward while the status still read `Charging`. Hypothesis (b).

**The charge threshold on this machine is enforced.** Every conclusion in
this document that says otherwise is wrong.

### Why it looked inert for a month

The default is 80, and at 80 the cap lands close enough to the gauge's "full"
point that the fabricated last stretch covers the gap. Set it to 80, charge,
watch it reach a reported 100%, and the reasonable conclusion is that nothing
capped anything.

At 60 the gap is too large to paper over, so the stop becomes visible.

Everything the project observed is consistent with a working threshold seen
through a display that cannot show it:

| observation | what it actually was |
|---|---|
| "charges straight through 80% to 100%" | the cap, then the gauge walking to 100 |
| `charge_full` always equals design | the capped capacity reported as full |
| a discharge yields 79% of design | the 80% cap, measured |
| the vendor's own engineer saw "something happening, but not what is advertised" | exactly this |

It also took effect **mid-charge**: the threshold was changed with the charge
already running and the EC acted on it. The reported arming conditions —
profile set before plug-in, a prior discharge below 5% — were not needed
here.

### What is not explained

It stopped at a reported **47%** with the threshold at **60**, thirteen points
low. The reported percentage is known to be unreliable, and the EC compares
against its own internal state of charge rather than the number in sysfs, so
the two need not agree — but the size of the gap is not accounted for.

The threshold was also changed part-way through this charge, so the stop may
reflect something latched under 80 beforehand. **The clean re-run is a
threshold of 60 set before the charger is connected, from a low state of
charge.** That also happens to be run C of the arming-conditions matrix.

### Direct confirmation still outstanding

`0x07B9` bit 7 is `CHARGE_CTRL_REACHED`. With the threshold at 60 and
charging stopped, an armed bit reads `0xBC`. Every previous capture was taken
mid-charge with the bit clear, which was never evidence of anything.

### The UI control was removed on a false conclusion

It was taken out earlier the same day. That was wrong and should be restored.
