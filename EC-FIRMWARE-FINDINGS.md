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
