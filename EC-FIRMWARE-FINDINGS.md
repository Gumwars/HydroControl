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

The code is present, correct, and reachable by its own guards, and it does not
execute. The routine is not wired into whatever task loop calls it on the G2.

This explains, at once, every result recorded since August:

- Three write doors set `0x07A6` and verified it. All three were correct.
- The mailbox at `0x8A-0x8E` showed the EC servicing our transactions with the
  handshake flags cleared. It was.
- `0x078E` bit 3 advertises the feature. The capability is real; the code it
  advertises is unreachable.
- The pack charged to 100% at full termination voltage under Stationary, and the
  percentage climb was backed by real current to within 0.6%.

There was never an arming step to find.

## What is not established

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
