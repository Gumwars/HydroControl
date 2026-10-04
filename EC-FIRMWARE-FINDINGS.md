# What the EC firmware says about the charging profiles

2026-09-28. Sources: `117.ELUK` (HYDROC-16 G1, this machine) and `125.ELUK`
(HYDROC-16 G2), both 256 KB ITE images shipped plain inside the vendor's EFI
update packages. Reproduce with `ec_image_scan.py`.

## Short version

The charge-ceiling logic **is present in the G1 firmware**, byte for byte the
same as in the G2 build. Every precondition it tests is satisfied on this
machine during a real charge. The bit it exists to set has never been set.

The code is there and it does not run.

That closes the question this project has been circling since August. No choice
of write door was ever going to matter, because the register was never the
problem and neither was the door.

## How the images compare

|  | G1 | G2 |
|---|---|---|
| file | `117.ELUK` | `125.ELUK` |
| size | 262144 | 262144 |
| entropy | 6.18 | 6.49 |
| EC part | `ITE8850-PD` | `ITE8850-PD` |
| core | `{UUITE EC-V14.6` | `{UUITE EC-V14.6` |
| `PROJECT_ID` written at init | **0x19** | **0x1A** |

Consecutive Uniwill projects, one iteration apart, same part and same core.
That is what makes the comparison worth anything: these are two builds of one
source tree, not two unrelated firmwares.

Neither id appears in mainline's enumeration, which stops at `0x18`.

Address layout: file `0x18000-0x1FFFF` maps to logical `0x8000-0xFFFF`, which
checks out — every `LCALL` target decoded from that bank lands on a plausible
function entry.

## The ceiling routine is identical

G1 at file `0x1C8BA`, G2 at file `0x1CCD1`. Sixty-four bytes, **six of which
differ**, and all six are addresses rather than logic:

| offset | G1 | G2 | what |
|---|---|---|---|
| +0x0B | `CF 96` | `D5 1A` | `LCALL` guard 1 |
| +0x1B | `D0 25` | `D5 AF` | `LCALL` guard 2 |
| +0x3D | `08 7F` | `91 64` | scratch slot for the threshold |

Both `LCALL` targets were decoded and are themselves identical between builds.
The scratch slot moved from `0x087F` to `0x9164`, which is incidentally why
`0x087F` appears in this project's older notes as the FIXCGLM overlay's "shadow
limit" — it is the G1's threshold mirror.

The routine, annotated:

```
90 07 B9  E0  54 7F  FF     A = threshold (0x07B9 bits 6:0), R7 = it
CB EF CB                    R3 = threshold
12 <g1>   60 26             guard 1: 0x0490 bit 0, else jump to disarm
E0  54 04  60 21            guard 2: 0x0490 bit 2, else jump to disarm
CF CF EF FD  7C 00
12 <g2>   50 16             guard 3: threshold <= 100
ED  70 02  80 11            guard 4: threshold != 0
90 04 AB  E0  C3  9B        capacity - threshold          <-- the comparison
90 07 B9  E0  40 09         below threshold? then disarm
44 80  F0                   ORL A,#80h -> SET CHARGE_CTRL_REACHED
80 07
90 07 B9  E0  54 7F  F0     disarm path
```

Both guard accessors, also identical in both builds:

```
guard 1   90 04 90  E0  54 01  22      return 0x0490 bit 0
guard 2   C3  94 64  74 80  94 80  22  signed compare of threshold against 100
```

Guard 1 leaves `DPTR` on `0x0490`, which is why the caller's next bare `MOVX`
reads the same register for bit 2.

## Every guard passes on this machine

From `lowec-charge.json`, 140 samples taken while charging. `0x0490` is low RAM
`0x90` — the `0x04` page and low RAM are two views of one SFR block, established
separately via the mailbox at `0x8A-0x8E`.

| condition | required | measured |
|---|---|---|
| `0x0490` bit 0 | 1 | **1** (`0x0F`, all 140 samples) |
| `0x0490` bit 2 | 1 | **1** |
| threshold <= 100 | yes | 80 |
| threshold != 0 | yes | 80 |
| capacity > threshold | to arm | 89-91% vs 80 |

Every precondition satisfied, for the whole window, with capacity clear of the
threshold by nine points.

`CHARGE_CTRL_REACHED` never armed — not in those 140 samples, not in the 981
samples of `wmi-ceiling.csv`, not at any capacity including 100%.

## What follows

The code is present, correct, and reachable by its own guards, and the bit it
exists to set is never set.

**Stated more carefully than the first draft of this document did.** "The
routine is not wired into the task loop" was an inference, and a weak one: the
caller search that suggested it found nothing in the G2 either, where the
feature reportedly works, so the method failed rather than the code being
absent. What is observed is that the bit never arms. Why is not established.

This explains, at once, every result recorded since August:

- Three write doors set `0x07A6` and verified it. All three were correct.
- The mailbox at `0x8A-0x8E` showed the EC servicing our transactions with the
  handshake flags cleared. It was.
- `0x078E` bit 3 advertises the feature. The capability is real; the code it
  advertises is unreachable.
- The pack charged to 100% at full termination voltage under Stationary, and the
  percentage climb was backed by real current to within 0.6%.

There was never an arming step to find.

## The machinery has been seen working once

Recorded late, from the owner's account of work predating this document, and it
is the single most important datum here: an earlier session that held the
charge registers by **continuous re-writing** produced charging that started and
stopped repeatedly. That is a percentage ceiling engaging, without hysteresis.

Every measurement in this document was taken with a *single* write. On that
basis the conclusion was heading toward "the code does not run". It runs.
Whatever distinguishes a held write from a set-once write is what this
investigation has been missing.

The same episode ended in a latched state -- 100% reported, true charge unknown,
surviving reboots, cleared by an EC reflash -- which is why the obvious follow-up
experiment is **not** being run from Linux. DESIGN.md 4.1b has the detail and the
recovery that was never tried. The Control Center service can be watched on
Windows for the same answer at no risk.

## The visible witnesses inside the ECRR window are exhausted

The ceiling routine's only output this machine can observe is
`CHARGE_CTRL_REACHED`, and it has never armed -- across a full cycle under
Stationary (981 samples), a second full charge 77% to 100% (638 samples), and
every earlier run.

Everything else the routine writes is outside the window:

| written by the routine | visible through ECRR? |
|---|---|
| `0x07B9` bit 7 (`REACHED`) | yes -- never changes |
| `0x0742` bit 2 | yes -- never changes, but see below |
| `0x087F` (threshold shadow) | **no**, reads `0xFF` |
| `0x09C7`-`0x09C9`, `0x0A51` | no |

Two tests were built against these and both are weaker than they looked.

`0x0742` bit 2 is written on every pass through the block before the capacity
comparison, set on one branch and cleared on the other. But the clearing branch
writes the same value when the bit is already clear, so a flat line cannot
separate "never ran" from "ran and decided the same way every time". It read
`0x22` for all 638 samples, which means less than it appears to.

`0x087F` looked better because it is causal: the EC copies the threshold into
it only when the two disagree, so changing the threshold should force a write.
It reads `0xFF` -- unmapped space, every bit set. DESIGN.md 3.2 recorded that
on 2026-08-27, in a table, before `shadow_probe.py` was written; the probe was
built anyway and reported "the routine did not run" from it, which is the same
error as reading `0x0984` as a hardware interlock. It now refuses on `0xFF`
without touching the threshold.

The conclusion is not about the EC. It is that this question cannot be
answered from inside the ECRR window, which is an argument for watching what
the Control Center service does on Windows rather than for building another
probe from here.

## Write-triggered evaluation: proposed, tested, refuted

The last surviving Linux-side theory was that the EC does not poll capacity
against the threshold but evaluates when the profile register is written. It
would have explained every null result at once, since this project always
wrote early, below the threshold.

It is wrong, and the disproof was already in the repository:

| capture | profile written at | threshold | current | result |
|---|---|---|---|---|
| `ceiling-test.csv` | 85% | 80 | 2006 mA | charged on |
| `ceiling-test.csv` | 98% | 80 | 306 mA | charged on |
| `phantom-check.csv` | 86% | 80 | 1190 mA | charged on |

All three wrote Stationary with capacity already above the threshold.
`CHARGE_CTRL_REACHED` stayed clear in every one and all three reached 100%.

`phantom-check.csv` contains a single sample reading `reached=1`, which is a
glitch rather than an event: the same sample reads the threshold as 72 instead
of 80, both fields come from one byte (`0x50` misread as `0xC8`), and the
neighbours either side are clean. One sample in 1883, the known ECRR
single-byte glitch.

`charge_profile_probe.py --trigger-at` is kept and marked answered so it is not
rebuilt.

### What the data does support

Sustained writes produced charging that started and stopped -- the only
positive observation this investigation has produced. Single writes above the
threshold do nothing, three times over. The difference is **held versus set**,
and that is the live question.

It is also the operation that latched this EC (DESIGN.md 4.1b), so it is not
being retried from Linux while a safe way to learn the same thing exists:
watching what the Control Center service actually does.

## CORRECTED 2026-09-28: the bank mapping was wrong, and the routine is dispatched

The section below recorded `file = bank * 0x10000 + logical` and an ancestry
ending at `bank0:0xC4AE`. Both are wrong, and the way they were wrong is worth
keeping.

`BANK_SIZE` should be `0x8000`. The error was invisible because halving the
bank number and doubling the stride land on the **same file offset**: bank 1 at
`0x10000` and bank 2 at `0x8000` are the same byte. So every disassembly came
out correct and every bank number came out wrong. Thunks are keyed on
`(bank, target)`, so lookups never matched -- the ceiling task is **bank 2**,
and it was searched for in bank 1. That single constant produced the
"unreachable" reading here, in DeepSeek's independent analysis, and in the G2
image where the feature reportedly works.

The recorded ancestry was wrong for a second reason: it came from an
`enclosing()` heuristic ("nearest call target at or below"), which attributed
the ceiling code to `0xC86D`, a neighbouring function. `0xC86D` does have the
ancestry that was recorded -- it simply is not the container. The task entry is
`0xC88C`.

The real chain, identical in both images:

```
jump table 0x00EA6  ->  thunk 0x1582  ->  bank2:0x86C2
                    ->  LJMP bank2:0xC88C  ->  JNZ  ->  0xC8BA
```

`0x00E90-0x00EB8` is a uniform run of 3-byte `LJMP` entries at a fixed stride,
each pointing at a 6-byte thunk: an indexed jump table, entered by computing
base + index*3. `bank2:0x86C2` is an ordinary function ending in a tail jump to
the task. Not a task table, not dead code.

**The ceiling task is dispatched.** What stops it is the gate -- `0x07C3` reads
`0x0D` where `4` is required -- and everything downstream of that gate is in a
state that would work.

The methodological rule that resolved this, after it had failed three times:
when the G1 and the G2 give the **same** answer, the answer is about the tools.
The G2 is the control, and it showed the identical dead end.

## (superseded) The ceiling routine is reachable code, and the banking is decoded

Established 2026-09-28 with `ec_disasm.py`, and it retires the "dead code"
reading for good.

**The banking scheme.** Four 64 KB banks; logical `0x8000-0xFFFF` is the
window, so `file = bank * 0x10000 + logical`, and below `0x8000` is common.
Cross-bank calls go through a thunk in common memory:

```
90 hi lo    MOV  DPTR,#target
02 11 xx    LJMP dispatcher      0x1100 / 0x1114 / 0x1128 / 0x113C = bank 0..3
```

Each dispatcher pushes the old bank's restore-stub id, pushes `DPTR`, sets the
bank on `P1.0-P1.2` and executes `RET`, which pops `DPTR` into `PC` -- a jump
disguised as a return. The callee's own `RET` lands on the restore stub, which
puts the bank back. 536 thunks in the G1 image, 550 in the G2.

**The routine is reached.** In the G1 the ceiling code at `bank1:0xC8BA` is
entered by a `JNZ` at `0xC8B0`, inside a function at `0xC86D` that has a
complete ancestry: `0xC7F9` -> `0x85FF` -> thunks -> `bank0:0xC4AE`. The G2 has
the same shape, three ancestors instead of ten, ending at `bank0:0xD1D0`.

So the routine is live, callable code in both builds. It is not compiled out,
not orphaned and not stripped of its call site. Whatever explains the ceiling
never engaging here, it is not that the code is absent from the path.

### Why four earlier searches said the opposite

They were byte scans, and a variable-length instruction set defeats byte scans
in both directions.

*False negative.* Searching for `LCALL`, `ACALL`, `LJMP`, `AJMP` and `SJMP`
covers no conditional branch, and the entry to the ceiling code is a `JNZ`. So
every search returned nothing -- in the G1 **and in the G2 where the feature
reportedly works.** Getting an identical empty answer from the build where the
code demonstrably runs should have condemned the method immediately instead of
being read as evidence about the G1.

*False positive.* `80 11` is `SJMP +17`. A scanner testing every byte as an
opcode sees the `0x11` and reports an `ACALL`, which produced a caller chain
that looped back on itself.

`ec_disasm.py` tracks instruction lengths and only decodes at real boundaries.
Both failure classes are pinned by tests against the actual bytes.

### What this leaves

The code exists, is reachable, and its guards pass with capacity above the
threshold -- and `CHARGE_CTRL_REACHED` has still never armed. The remaining
possibilities are about *when* the function is called rather than whether it
can be, and about conditions in its ancestors that have not been decoded.

## Two mechanisms, two gates, and only one of them is shut

Verified from the bytes 2026-09-28, and it reframes everything above.

`0x07A6` (the charging profile) and `0x07B9` (the numeric ceiling) are not two
views of one feature. They are separate code with separate gates.

```
ceiling   bank2:0xC88C   LCALL CFED -> 90 07 C3 E0 64 04    gate: 0x07C3 == 4
profile   bank2:0xBCF6   LCALL CF96 -> 90 04 90 E0 54 01    gate: 0x0490 bit 0
          0x1BCEE: 12 CF 96 / 70 03 / 02 BE 38
```

There is no `0x07C3` read anywhere inside the profile function
(`0x1BCF6-0x1BE38`); the nearest is at `0x1B403`. So the ceiling's gate does not
gate the profile.

On this machine the two resolve in opposite directions:

| mechanism | gate | measured | state |
|---|---|---|---|
| ceiling | `0x07C3 == 4` | `0x0D` | **shut** |
| profile | `0x0490` bit 0 | `0x0F`, bit 0 set | **open while charging** |

`0x0490` read `0x0F` in 522 of 638 charging samples. The profile path's gate has
been open throughout every capture this project has taken, and its output has
never been read.

What the profile does is reduce a charge target rather than cap a percentage:

```
0x0522:0x0523  =  [0x0A5A:0x0A5B]  -  (constant x [0x0A51])
```

with the constant selected by `0x07A6` bits 5:4 -- Stationary 200, Balanced 100,
150 default, 250 on a high-voltage branch -- `0x0A51` a charge-rate code, and
`0x0A5A:0x0A5B` seeded from `0x030E:0x030F`. The result reaches the charger over
SMBus.

A reduced charge target makes a pack terminate early. To anyone watching
capacity that is indistinguishable from a percentage ceiling, which is a
simpler account of the outside report's 82-85% stop than either a designed
disguise or an induced fault.

**This is a hypothesis with a verified mechanism and no measurement.** Whether
the profile path actually runs here is answered by `chg_target` and `hw_base` in
`charge_profile_probe.py`, and those windows -- `0x03xx` and `0x05xx` -- have
never been read on this machine. Empty columns would mean the wrong door, not a
null result.

## The profile does not move the charge target either (measured)

`switch1.csv`, 2026-09-28. 238 samples across a mid-charge profile switch --
Stationary for 126 samples from 74% to 80%, then High Capacity for 112 more.
The single-switch design holds the telemetry and the rate still, which two
separate runs cannot do, because the constant-selection tree forks on
registers that are unmapped.

| | Stationary | High Capacity |
|---|---|---|
| `profile` | `2` | `0` |
| `chg_target` (`0x0522:0x0523`) | **16800** | **16800** |
| `hw_base` (`0x030E:0x030F`) | 34885 | 34885 |
| `rate` | 2 | 2 |

The write landed -- the profile register really does read 2 then 0 -- and the
charge target did not move by a millivolt. `hw_base` stayed far above 500, so
the "constant forced to 0" branch does not explain it, and the rate held.

**Correction: `hw_base` was read with the wrong byte order.** The two 16-bit
values in this computation are stored opposite ways, which the probe did not
account for:

- `0x030E:0x030F` is **big-endian**. The firmware's compare against 500
  (`0x01F4`) does `SUBB A,#F4` on `0x0A5B` and `SUBB A,#01` on `0x0A5A`, so
  `0x0A5A` -- copied verbatim from `0x030E` at `0x1BCF8` -- is the high byte.
- `0x0522:0x0523` is **little-endian**; `0x0522` takes the low-byte result.

So `hw_base` is `0x4588` = **17800**, not `0x8845` = 34885. Caught by DeepSeek.
With that fixed the picture is coherent:

| | pack | per cell |
|---|---|---|
| `hw_base` | 17800 mV | **4450** |
| `chg_target` | 16800 mV | **4200** |
| offset | 1000 mV | **250** |

The charger's 4.45 V/cell maximum, reduced by 250 mV/cell to the 4.20 V/cell
Li-ion standard. Both of the register assignments were right; only the reading
was wrong, and `18085` being impossible for any `constant x multiplier` was the
tell.

**The experimental result is unchanged.** The offset was exactly 1000 in both
phases. Switching Stationary to High Capacity did not move it.

Two things remain unsettled in the formula. The offset `1000` is read as
`250 x 4` with 4 as the charge-rate code from `0x0A51` -- but the capture
measured ~2006 mA throughout, which is rate 2, not 4. A reading that needs no
rate at all fits the per-cell figures better: **250 mV per cell x 4 cells**,
which would make `0x0A51` the cell count rather than a rate. And whichever it
is, the constant did not change with the profile, so either the telemetry gates
routed both phases to the same branch without consulting `0x07A6` -- which the
decision tree at `0x1BD07` allows -- or the profile bits do not reach this
computation on this machine.

What `chg_target` almost certainly *is*: **16800 mV = 4.200 V/cell on a 4S
pack**, the charge termination voltage, held as configuration rather than
computed. The pack climbed 4.03 -> 4.09 V/cell during the capture, heading for
exactly that. It also pins the units question -- the profile constants
(200/150/100/250) are millivolts, so Stationary would target 4.10 V/cell if it
ever applied.

Either way the practical answer is the same, and it is now measured under
control rather than inferred: **on this machine, selecting a charging profile
changes nothing any readable register reflects.** Both mechanisms are inert --
the ceiling because `0x07C3` reads `0x0D`, and the profile for a reason not yet
distinguished.

## Which EC address ranges ECRR can actually read

Established by measurement, and written down because this project has now built
three separate probes on registers that turned out to be invisible.

| range | reads | evidence |
|---|---|---|
| `0x03xx` | **yes** | `0x030E:0x030F` = 17800 |
| `0x04xx` | **yes** | live telemetry window, used throughout |
| `0x05xx` | **yes** | `0x0522:0x0523` = 16800 |
| `0x07xx` | **yes** | the settings window |
| `0x0Fxx` | **yes** | fan tables |
| `0x08xx` | **no** | `0x087F` reads `0xFF` (DESIGN.md 3.2) |
| `0x09xx` | **no** | `0x09C0-0x09CF` all `0xFF` |
| `0x0Axx` | **no** | `0x0A50-0x0A5F` all `0xFF` |

`0xFF` across a whole 16-byte range is unmapped space, not data. Before adding a
register to a probe, check it is in a readable range -- `shadow_probe.py` was
built entirely on `0x087F` and reported a verdict about the EC from a register
it could not see.

### What that costs the charge-voltage decode

The decision tree's inputs live almost entirely in the invisible ranges, so the
tree cannot be run by hand and checked against the measured constant. What
survives is what gets copied in from readable space:

| tree input | readable? | via |
|---|---|---|
| `0x0A5A:0x0A5B` (`hw_base`) | yes | copied from `0x030E:0x030F` at `0x1BCF8` = 17800 |
| `0x0A56:0x0A57` (cycle count) | yes | copied from `0x04A6:0x04A7` at `0x1BC1F` = **132** |
| `0x0A51` (cell count) | indirectly | derived from `0x0491 & 0xC0`, and `0x04xx` is readable |
| `0x0A5C` | **no** | SMBus-filled, no readable source found |
| `0x09C9:0x09CA` | **no** | no readable source found |

So the cycle-count gate is eliminated by measurement -- 132 against a threshold
of 550 -- and the other two cannot currently be observed at all. Finding a
readable source for either, the way `0x0A56` turned out to be a copy of
`0x04A6`, is the only route to checking them without a Windows capture.

## RESOLVED 2026-09-30: the protection is active, and stricter than the profile

A Windows capture with Control Center running, plus a decompilation of the
service itself, closes this. See `TCC-SERVICE-FINDINGS.md`.

**Control Center writes `0x07A6` bits 5:4 and nothing else.** Traced through
`GCUService.MySystem.BatteryProtection2` across the whole assembly: no
keepalive, no software ceiling, no commit or handshake write, no write to
`0x07B9`, none to `0x07D0` or the NVRAM charge fields. It writes at service
start, at resume, and on a user change. Byte for byte what
`charge_profile_probe.py` has been doing since August.

So "Windows drives it differently" is closed at the source level rather than
by inference, and a full charge under Windows confirms it: `0x07C3` = `0x0D`
for all 137 samples, `0x0742` = `0x22`, `chg_target` = 16800, terminating at
4.1810 V/cell.

### The framing this project had wrong

The conclusion was repeatedly written as "the feature does not work". That is
not what the evidence says. The accurate statement:

> The EC's battery protection **is active and at its strictest step**. The
> charge target computes to 16800 mV against a 17800 mV rating -- 250 mV per
> cell below maximum. Stationary asks for 200 mV and Balanced for 100 mV.
> **Both are milder than what this pack already gets**, so the profile has
> nothing to add, and the selector looks inert because it can only ask for
> *less* protection than the EC has already chosen.

### Correction: the pack never reaches 16800 (2026-10-03)

The sentence above used to read "this pack charges to 16800 mV". It does not,
and the evidence against it was already in this document: the line directly
above records Windows **terminating at 4.1810 V/cell**, which is 16724 mV.
The two numbers sat one line apart for weeks without the contradiction being
noticed, because 16800 was the register and 4.1810 was the pack and nobody
multiplied.

Measured directly on the replacement pack, sampling every 15 s through the
top of a charge:

```
volts/cell   4175 / 4181 only        pack 16700 / 16724 mV
current      1.394 A -> 0.782 A      a 44% fall
voltage      did not move
```

In constant-voltage phase the charger *holds terminal voltage at its
setpoint*. A terminal voltage that stays flat while current collapses by 44%
means **the setpoint is 16.70 V**, not 16.80. It was never going to rise to
meet the register.

So `0x0522:0x0523` is a **computed target the charger regulates about 25 mV
per cell beneath**, not the voltage the pack sees. Everywhere this document
quotes 16800, read it as the register's value and not as a delivered
voltage. The real reduction from the 4450 mV/cell rating is **275 mV/cell**,
which is more protection than claimed, not less.

The same 16700/16724 pair appears under Windows and on both battery packs, so
the offset is a property of the charger rather than of a reading.

### The current ramp is programmed, not a taper

From the same run: every single decrement was **exactly 34 mA**, eighteen of
them, fitting a straight line at -1.042 mA/s with R² = 0.993.

A constant-voltage phase decays *exponentially* as the cell's EMF approaches
the setpoint. A linear ramp in uniform steps is something stepping a
current limit down on a schedule. That is consistent with the charge-rate
mechanism found in the firmware -- `0x0A51` holding a rate of 4/3/2 and
`0x0A54:0x0A55` a current of rate x 1040 -- which has never been observable
because the whole `0x0Axx` window reads `0xFF` through ECRR.

Extrapolated, the ramp reaches zero around 90-92% charge. The threshold was
set to 80% throughout.

### Where this leaves the charge investigation

Established:

- The threshold register `0x07B9` is **stored and never enforced**. Confirmed
  across two operating systems, three packs, and most recently a battery with
  zero cycles that charged eight points past an 80% setting.
- The EC **is** doing something, and it is substantial: the pack is held at
  4.175 V/cell against a 4.450 V/cell rating.
- It is **not adaptive to reported wear** -- 9% and 0% produced the same
  target.
- The percentage-ceiling code exists, is reachable, and is gated off:
  `0x07C3` reads `13` where it arms at `4`.

Not established, and the shape of it is still unclear:

- Why the delivered regulation sits 25 mV/cell below the computed target.
- What drives the 34 mA ramp, and whether the rate registers in `0x0Axx` are
  it.
- Whether **temperature** modulates any of this. The thermal path is separate
  from and overlaps the charge threshold, and every capture so far has been
  near 30 °C -- one point on a curve nobody has plotted.
- What, if anything, the three profile names select. They can only ask for
  less derating than is already applied, so an inert selector and a selector
  whose request is always discarded look identical from outside.

That reconciles every measurement without a broken EC, and it explains the
outside report without anyone being mistaken: a pack with a cooler history and
fewer cycles lands on a milder step, where Stationary's 200 mV *is* the binding
limit. That owner sees charging stop early, and the gauge then declares full
and walks the percentage up. Same firmware, different pack.

It also retires the wear question. `charge_full` 5800 against a 6400 design is
90.6%, which is what a 250 mV/cell reduction produces. This project has been
reading the protection as degradation.

### Temperature is not what selects the step

Worth recording because the thermal reading looked strong. The Linux baseline
was taken charging at **24.85 C**, below the 3030 breakpoint; the Windows
charge ran at **27.85-34.85 C**, above it for most of its length. Both report
16800. Across roughly 400 samples spanning two operating systems, three battery
states and a 10 C range, `chg_target` has never been anything else.

So the temperature comparison exists in the code but is not what pins this
machine. The accumulated stress counter at `0x09C9:0x09CA` remains the leading
candidate, and it is unreadable from either OS.

### What HydroControl should do with this

Show the real number instead of implying a protection the selector does not
provide: **"charge target 4.20 V/cell, 250 mV below the pack's rating"** is
true, readable from Linux today (`0x0522` little-endian, `0x030E` big-endian),
and more informative than a profile name. Keep the profile selector, described
as a secondary setting that applies only when the EC's own protection is mild.

## What is not established

**A fleet-wide dead feature is implausible on its face.** `117.ELUK` is what
every HYDROC-16 G1 runs. If the code were simply never invoked, it would be
never invoked on all of them, and an ODM shipping a register map, a capability
bit, guards and a comparison that nothing calls is a poor explanation compared
with a variable specific to this configuration.

The likely shape of it is build-time configuration. Uniwill develops one EC
codebase and ships it to the boutiques, who enable what they want, disable what
they do not, and brand the result -- nobody writes an EC from scratch per
chassis. A feature compiled out at its call site, with the function body still
linked in because an 8051 toolchain does not aggressively strip unreferenced
code, produces exactly what is observed: the routine present, identical, and
never invoked. Deliberate, and deliberate at a layer above the code we are
reading.

(An earlier draft blamed the Prema Mod BIOS on the strength of the vendor
string alone. Withdrawn: Prema is a performance tuner and the owner has direct
knowledge that battery policy was not touched. The stock ACPI tables also
reference none of 0x07B9, 0x0490 or 0x0742, so the BIOS does not reach these
registers at runtime in any case.)

**Unreachability is inferred, not proven.** The direct evidence is that the code
does not run; the mechanism is not identified. Searching for callers found none
in *either* image, including the G2 where the feature reportedly works, so the
search method is what failed. These are banked images and cross-bank dispatch
goes through a trampoline whose format has not been decoded. Proving it would
mean decoding that and showing the G1 dispatch lacks an entry the G2 has.

**The outside report is not contradicted.** The participant's board is an XMG
NEO 16 / TongFang `X6AR5xxY`, a different project again. If their build wires the
routine into its task loop and ours does not, both observations are true and
there was never a conflict to resolve.

### The capability bit was mislabelled here, and it matters

The site at G1 `0x1BBEB` was recorded as a test of `0x078E` bit 3. It is not:

```
90 07 8E  E0  44 08      A = [0x078E] | 0x08
12 <callee>              callee:  F0                     write A BACK to 0x078E
                                  90 07 41  E0  54 01  22   return 0x0741 bit 0
70 07                    JNZ -> keep the selected profile
90 07 A6  E0  54 CF  F0  else force it to high_capacity
```

The capability bit is **written, not read**. The EC advertises charging-profile
support unconditionally, on every machine, whatever the feature actually does.
That is how `uw_has_charging_profile()` in tuxedo-drivers -- which gates on
exactly this bit -- finds it set on a machine where the profiles do nothing,
with nobody having lied anywhere.

What the caller branches on is `0x0741` bit 0 -- which is **`ENABLE_MANUAL_CTRL`,
manual fan control**, defined in `uniwill-acpi.c` and toggled by this project's
own fan code. It was briefly recorded here as the charging-profile enable, and
its value on this machine (`0x04`, bit 0 clear) reported as the answer. It is
not. Reading three instructions past the profile clear shows the same path
going on to clear `0x09C7`, `0x09C8` and `0x09C9`: a restore-defaults routine
with the profile as one item on a list, not a gate.

**No enable bit has been found.** The capability bit is written unconditionally
and nothing yet located decides whether the feature is honoured.

**Which of the two live explanations holds is not yet known.** The code never
running, and the code running against a threshold it reads elsewhere, are
indistinguishable from outside. 0x0742 bit 2 tells them apart: the block
immediately before the capacity comparison writes it on every pass, setting it
on one branch and clearing it on the other, so a change proves execution.
`charge_profile_probe.py` now records it, along with the two guard bits.

**The profile constants are still undecoded.** The branches load `0xC8` (200) for
Stationary and `0x64` (100) for Balanced into R3. Units unknown.

## Consequence for the driver

`uniwill-acpi.c` claims `BATTERY_CHARGE_MODES`, which exposes `charge_types` in
sysfs. The comment there left the question open on the grounds that absence of
effect is not proof of absence. That ground is gone: there is now positive
evidence that the code behind it never runs on this build.

The same file already refuses to expose `charge_control_end_threshold` for
exactly this reason — a control that silently does nothing is worse than a
missing one, because it is why someone leaves a machine plugged in permanently.
The two claims should now be decided the same way.


## The last 10% is fabricated (2026-10-03)

The top of a charge on the zero-cycle replacement pack, 30 s sampling:

```
15:45:04  Charging      90%  5760 mAh  204 mA  16.724 V  4.181 V/cell
15:46:34  Charging      92%  5888 mAh    0 mA  16.654 V  4.163
15:47:04  Charging      94%  6016 mAh    0 mA  16.654 V  4.163
15:47:34  Charging      96%  6144 mAh    0 mA  16.654 V  4.163
15:48:04  Charging      98%  6272 mAh    0 mA  16.654 V  4.163
15:48:34  Not charging 100%  6400 mAh    0 mA  16.631 V  4.158
```

**640 mAh appears with no current flowing.** Being generous — assuming the
higher of each interval's two current readings flowed for the whole interval
— at most **5.1 mAh** could have been delivered. The delivered-to-reported
ratio is **0.008**, against the 0.85 that `battery_summary.py` was written to
flag.

Three things make it unambiguous rather than a sampling artefact:

- **The increments are exactly 128 mAh, five times running.** 128 mAh is
  exactly 2% of 6400. This is a counter, not a measurement.
- **`charge_now` is `capacity × 64`** at every single row. 6400/100 = 64, so
  it is computed from the percentage and carries no independent information.
  There is one number here, not two.
- **The voltage falls** — 16.724 → 16.654 → 16.631 — while the status still
  reads `Charging`. A pack under charge does not drop voltage; a pack at rest
  relaxes downward. Charging had already stopped.

### Charging terminated at 90%, and the gauge walked the rest

The real end of charge is the 15:45:04 row: 204 mA at 16.724 V, the last
point with current. Everything after is interpolation to a round number.

So this pack, held at **4.175 V/cell against a 4.450 V/cell rating**, fills
to about **5760 mAh of a 6400 mAh design capacity — roughly 90%** — and is
then reported as 100%.

### This is what made the protection invisible

It resolves the shape of the thing this investigation kept failing to see:

- **Why the percentage controls look inert.** Every charge ends at a reported
  100%, so no setting can be observed to cap anything. The cap is real and
  below the top of the scale the owner is shown.
- **Why `charge_full` always equals `charge_full_design`.** A gauge that
  invents the last 10% never measures a shortfall, so it cannot report one.
- **Why "0% wear" means nothing on this machine.** Capacity fade is measured
  by `charge_full` drifting below design. That number is not being measured.
- **Why a previous replacement pack shut the laptop off at a reported 46%.**
  The reported percentage is decoupled from the charge actually present. On a
  pack whose real capacity had collapsed, the decoupling is fatal rather than
  cosmetic.

It also puts the original pack's "9% wear over two years" in doubt in both
directions, since the same gauge produced it.

### What is not yet established

Whether the fabrication is the gauge IC, the EC, or the `uniwill-laptop`
driver. The data reaches us through all three, and the exact-2% steps point
at something computing rather than measuring — but which layer computes is
unknown.

Whether it happens on every cycle, or whether this was one termination. One
observation, cleanly measured, is still one observation.

**The measurement that settles the capacity question** is a full discharge
under coulomb counting: integrate `current_now` from a reported 100% to
shutdown and compare against 6400 mAh. If the pack delivers about 5760 mAh,
the 90% reading above is the true state of charge and the design figure is
simply unreachable by design. `battery_watch.py` logs it and
`battery_summary.py` already implements the integration -- it has never been
run across a full cycle on a healthy pack.


## What the derating costs: a measured discharge (2026-10-03)

A reported 98% to a reported 2% on the zero-cycle pack, 30 s sampling, 329
samples over 2.75 h, no gaps. Integrating `current_ma` — the only
independent measurement in the log, since `charge_now` is `capacity × 64`:

```
delivered (integrated current)   4828 mAh
reported  (charge_now delta)     6144 mAh
ratio                            0.786      battery_summary flags below 0.85
per reported point               50.3 mAh   against the 64.0 the gauge assumes
implied real capacity            ~5030 mAh  = 79% of the 6400 mAh design figure
stopped at                       3.387 V/cell, so this is a FLOOR
```

### The percentage scale is honest; the mAh figures are not

This is the cleaner reading of the 0.786. The gauge's **percentage** tracks
the pack consistently — 96 points cost 4828 mAh, about 50 mAh each, evenly.
What is wrong is the number it multiplies them by. `charge_now` is computed
as `capacity × (charge_full / 100)`, and `charge_full` is 6400 mAh, which
this pack does not hold.

So every mAh figure sysfs reports on this machine is a percentage dressed up
in units it has not earned.

### And it puts a price on the voltage derating

The EC holds the pack at **4.175 V/cell against a 4.450 V/cell rating**. A
high-voltage cell charged 275 mV/cell short of its rating would be expected
to deliver roughly three quarters to five sixths of its rated capacity. The
measurement lands at **79%**, inside that range.

So the three results are one result:

| observation | explanation |
|---|---|
| charge terminates near a reported 90% | the voltage ceiling is reached |
| the last 10% appears with no current | the gauge walks the display to 100% |
| a full discharge yields 79% of design | the pack was never more than ~79% full |

The protection is real, permanent, and **costs about a fifth of the rated
runtime**. That is a defensible engineering trade — holding 275 mV/cell off a
Li-ion pack buys a large multiple in calendar life — but it is made silently,
and the reporting is arranged so the owner cannot see it. A 6400 mAh sticker
on a pack the firmware will only ever fill to about 5030 mAh is the part that
is hard to defend.

### Confidence, and what would sharpen it

Solid: the integration itself. 329 clean samples, no interval over 90 s,
current averaging 1754 mA with no stalls.

A floor, not a total: the discharge was stopped at 3.387 V/cell. Typical
Li-ion cutoff is nearer 3.0, so there was usable charge left and real
capacity is somewhat above 5030 mAh.

Assumed: that the percentage scale is linear. It demonstrably is not at the
very top, where 90–100% is fabricated. If the bottom is compressed the same
way, 50.3 mAh per point understates the middle of the range.

**The confirming measurement is the recharge**, and it happens next anyway.
Integrating current *into* the pack from 2% to termination should land near
the same figure. Two independent integrations agreeing, in opposite
directions, would make this a measurement rather than an inference.


## Confirmed by integrating both directions (2026-10-03, late)

The recharge was integrated the same way the discharge was, on the same pack
in the same session:

```
discharged out   4865 mAh    98% -> 2%,  stopped at 3.387 V/cell
charged in       4950 mAh    2%  -> termination at 4.169 V/cell
agreement        98.3%
```

Two independent integrations, opposite directions, agreeing to 1.7%. The
small excess on the charge side is charging inefficiency, which is expected
and is the right sign.

**This pack holds about 4950 mAh against a 6400 mAh design rating — 77%.**
That is now a measurement rather than an inference from a single discharge.

So every mAh figure the system reports is fiction by about a quarter. At the
moment of real termination the gauge read 4928 mAh; it then climbed to 5312
in ninety seconds with the current at zero, heading for 6400.

### The second fabrication event, caught live

```
21:00:45  76%  4864 mAh  272 mA  4.181   Charging
21:01:15  77%  4928 mAh    0 mA  4.1692  Charging   <- real termination
21:01:45  80%  5120 mAh    0 mA  4.1692  Charging   <- +192 mAh, no current
21:02:15  81%  5184 mAh    0 mA  4.1692  Charging
21:02:45  83%  5312 mAh    0 mA  4.1692  Charging
```

384 mAh claimed in 90 seconds with nothing flowing, in increments that are
all multiples of 64 — one percent of the design figure. This is the second
time it has been captured today, which makes it the behaviour rather than an
incident.

**It walks straight through 80.** The threshold was set to 80 and the
reported capacity passed it at 21:01:45 without the charge resuming, which
settles something: the EC does not compare the threshold against the number
in sysfs. It has its own state of charge, and the reported percentage is a
separate, looser story told to the OS.

### Real termination moved between two charges to the same voltage

Earlier the same day, at the same threshold and the same ~4.17 V/cell, the
charge really ended at a reported **90%**. This one ended at a reported
**77%**. Same voltage ceiling, same threshold, twelve points apart in what
the gauge called it.

The integrations agree with each other; the percentages do not agree with
themselves. Whatever the reported scale is anchored to, it moved between two
charges a few hours apart — most likely because the deep discharge in between
changed the gauge's estimate.


## Does the gauge learn? Not in one cycle — but it can (2026-10-03)

After a full discharge to 2% and a full recharge to termination:

```
BAT_FULL_CAPACITY   0x0404:0x0405    6400   unchanged
BAT_DESIGN_CAPACITY 0x0402:0x0403    6400
```

One complete cycle did not move it. But the value is **not a constant** — the
original pack, logged on 2026-09-28, reported:

```
charge_full         5800000 uAh
charge_full_design  6400000 uAh      a 9.4% difference
```

So the gauge can and does report a learned full capacity. This pack simply
has not learned one yet, and a single cycle to a reported 2% was not enough.
That is consistent with impedance-tracking gauges generally: a qualified
learning discharge usually needs to reach the termination voltage, not a
reported percentage, and often more than one pass.

**The relearn advice stands but needs repeating.** One cycle is not a relearn.

### Remaining capacity is fabricated at the EC, not above it

`BAT_REMAIN_CAPACITY` (`0x0436:0x0437`) read **3008** at the threshold-60 stop
and **6400** when full. 3008 is exactly 47 x 64, and 6400 is 100 x 64 — the
same `capacity x 64` the sysfs figure is built from.

That narrows where the invention happens. It is not the `uniwill-laptop`
driver and not ACPI: the EC's own register already holds it. Either the EC
computes the figure from a percentage, or the gauge IC hands it over that way
and the EC passes it through.

### A prediction worth checking in a few weeks

This pack measurably holds **~4950 mAh**. If the gauge eventually learns and
reports that as `charge_full`, the machine will show roughly **23% "wear" on a
battery with a handful of cycles** — because the firmware never fills it past
about 77% of the design figure, and a learning gauge has no way to tell a cap
from a dead cell.

Which also puts the original pack's 9.4% in a new light. 5800 mAh is 91% of
design, well above the ~77% this firmware actually delivers, so that learned
value was not the pack's usable capacity under this charging policy either.
Whatever the gauge converged on, it was not what the pack gives you.
