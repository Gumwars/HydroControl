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

Two readings survive.

**The profile path does not reach the target**, which is the ceiling's answer
one layer further out. Or **`0x0522:0x0523` is not the output**, which the
arithmetic rather supports: the value is perfectly static across 238 samples,
which is not how a per-pass computation behaves, and
`34885 - 16800 = 18085` while the largest possible `constant x rate` is
`250 x 4 = 1000`. The formula cannot produce the observed pair, so
`0x030E:0x030F` is probably not the seed for `0x0A5A:0x0A5B`.

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
