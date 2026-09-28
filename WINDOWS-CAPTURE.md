# Capturing the charging profiles under Windows

Status: planned, not yet run. Blocked on a Windows install.

## Why this, and why it should have been first

Everything in this repo about the charging profiles is inference from one side
of the interface. We know what the EC accepts, what it reads back, and what the
pack then does. We have never once watched the vendor's own software drive the
feature on hardware where it is supposed to work.

Three write paths have set `0x07A6` and verified it -- ECRW/MMIO, WKBC, WMBC --
and low EC RAM shows the mailbox at `0x8A-0x8E` with the handshake flags
cleared, so the EC serviced the transaction. The byte arrives. The EC does not
act on it. Every remaining explanation lives on the other side of that boundary,
and no amount of further probing from Linux can see across it.

What we did instead was reverse the protocol and then argue about whether our
use of it was correct. That was not wasted -- the EC map, the three doors, the
`0x35` decode and the mailbox all came out of it -- but it answered "can we
write the register" many times over while never answering "does this feature
work on this machine."

## Setting the machine up

### Pull the Linux drive out

Physically, not by leaving it unselected in the installer. Windows writes boot
entries to any ESP it finds, and the Limine + initramfs setup on that drive has
already cost us one evening. Removal is the only guarantee that costs nothing.

### Which battery

The original pack, the one in the machine now -- not the RMA replacement.

`wmi-ceiling.csv` was measured on this pack. Same chassis, same pack, only the
OS differs, which is the comparison the test exists to make. Fitting a new
battery first would put a second variable into it: a ceiling appearing under
Windows would no longer distinguish "Windows engages it" from "the new pack
does". The pack is in any case already eliminated -- two physically different
batteries both charged straight past 80%.

The new pack is a confirmation run afterwards, a third data point, not a
prerequisite.

Charge down below 70% before starting so test 1 has room.

### Capture the firmware versions before anything updates them

This is the one setup step that can be got wrong irreversibly.

The Linux baseline is **EC 1.17, BIOS N.1.09ELUK (2024-05-04)**. A full
Eluktronics driver install is likely to offer an EC or BIOS update. If one
applies before the first capture, the comparison to the Linux run is gone and
we will not know whether a difference came from the OS or the firmware.

So run it deliberately as two passes rather than letting it happen once by
accident:

| | Firmware | What it answers |
|---|---|---|
| **Run A** | EC 1.17, stock, no updates applied | The controlled comparison. Only the OS differs from the Linux baseline. |
| **Run B** | After the vendor package updates whatever it wants | Whether newer EC firmware implements the feature. |

Record EC and BIOS versions in both, and do not let Windows Update apply
driver or firmware packages until run A is captured.

If A fails and B works, the answer is firmware and the investigation is over.
That matters beyond this machine: it is the question we cannot otherwise
settle, because the one person reporting the feature working has been out of
contact and may stay that way. Run B answers it without them.

### Dump before and after Control Center is installed

Free, and it tests something we have never looked at. Take a low-RAM and
`0x07xx` dump on a clean Windows install before TCC, then again after
installing it and launching it once, before touching any setting.

If TCC writes EC state at install or first launch, that write is itself a
candidate for the arming step we have been looking for -- and it would never
show up in a diff taken around a profile toggle, because it happens earlier.

## The one test that may end this

**Test 1 costs nothing and can make the other four unnecessary. Run it first.**

It also answers something never established: whether this feature works at all,
anywhere, under measurement. The single outside report of it working has an
alternative explanation -- `0x35` is the high byte of the current register, so
forcing it produces exactly the "zero current while charge_now climbs" signature
that report describes. The G2 firmware contains the code; no G2 has been
observed doing it. Before hunting for a missing step it is worth knowing there
is one.

Set Stationary in Control Center. Charge from below 70%. Record capacity,
current and voltage every 30 s to a file.

The measurement that matters is **volts per cell** (`voltage / 4`), not
percentage and not current:

| Observation | Meaning |
|---|---|
| Plateaus near **4.05 V/cell**, stops well short of full | The ceiling works under Windows. The gap is ours -- go to tests 2-4. |
| Climbs to **~4.18 V/cell** and reports Full | The feature does not work on this machine under its own vendor software either. The investigation is over; the difference is hardware or firmware, not Linux. |

Linux under Stationary settled at **4.1635 V/cell**, 100%, 0 mA
(`wmi-ceiling.json`, 2026-09-28). That is the number to beat.

Voltage is the right ground truth because the one mechanism anyone has proposed
for faking this -- forcing `0x35` -- is the high byte of the *current* register.
Voltage lives at `0x38`, a different register that mechanism does not touch. A
disguise that suppresses reported current cannot also invent a cell voltage.

Record the TCC version, EC version and BIOS version in the same file. The
outside report is from an XMG NEO 16 (E25) / TongFang X6AR5xxY, a different
barebones from this IDY chassis, so the comparison is only meaningful anchored
to versions.

## Is it the EC or the service? Five minutes, and do it second

Control Center is a service plus a frontend. If the service is what enforces
the ceiling -- polling capacity and acting when it reaches the target -- then
the EC registers are storage for a preference and nothing more, the machinery
decoded out of `117.ELUK` is a legacy or partial path, and no register write
from Linux could ever have worked, because there was never anything to trigger.

That fits every observation on record: the code present and identical to the
G2's, its guards passing, the bit never arming, and no enable bit found after
decoding every reference to the registers involved.

Once test 1 has shown a ceiling, split it:

1. Reach the ceiling with the service running. Confirm charging has stopped.
2. Stop the Control Center service. Leave the profile set.
3. Discharge below the threshold, then charge again with the service stopped.

| Result | Meaning |
|---|---|
| ceiling still engages | The EC enforces it. There is a register state we have not found, and the test 2 diff should show it. |
| charges straight past | **The service enforces it in software.** The hunt through EC RAM was looking in the wrong place. |

The second outcome is the better one for this project, because it is the only
result we can act on: a software ceiling is something HydroControl can
implement directly rather than merely report. It also makes test 2 far more
valuable, because the diff would then reveal the mechanism the service uses to
actually stop charging -- the thing we have never found and have no other way
to find.

## What the service does that we do not

Whatever the split says, capture the service's own behaviour. Three dumps, each
of `0x0700-0x07FF` plus low RAM:

- service stopped, profile unset
- service stopped, profile set through the frontend beforehand
- service running, same profile, five minutes later

Four things worth watching for specifically, because each would be invisible
from where we have been standing:

- **A keepalive.** If the profile must be re-asserted periodically and lapses
  otherwise, we would never have seen it: our probe writes once, and
  `--drift-confirm` deliberately suppresses re-writes after an earlier bug where
  a spurious re-write cleared a latched ceiling. The outside report already
  notes single writes to `0x35` being corrected back within 1-5 s while
  sustained writes hold, so this EC is known to behave differently under
  repetition.
- **A commit or handshake write** to another register after the profile.
- **An ordering requirement** -- threshold before profile, or either only while
  discharging, or only across an AC transition.
- **A different register entirely.** `0x07CD` carries 12-13 references in both
  images and this project has only ever treated it as the FIXCGLM overlay's
  scratch slot.

## Tests 2-4: only if test 1 shows a real ceiling

### 2. What else does Control Center write

Dump, toggle one profile in TCC, dump again, diff.

- `0x0700-0x07FF` -- the settings space. **This is the diff that matters.**
- `0x00-0xFF` -- low RAM, including the mailbox.
- `0x0400-0x04FF` -- live telemetry. Expect noise; diff it but do not trust it.

Take both dumps at a similar battery state, AC in the same position, and with
the machine otherwise idle. Half of `0x04xx` moves on its own.

If TCC writes anything beyond `0x07A6` bits 5:4, that is the missing step and
the whole question collapses to reproducing it.

### 3. Capture the transition live

If the ceiling engages, sample across the stop rather than before and after.
Log every 10 s from 5% below the stop to 10 minutes past it:

- capacity, `charge_now`, current, voltage
- `0x07A6`, `0x07B9` (including `CHARGE_CTRL_REACHED`, bit 7), `0x07C6`

The specific things to answer:

- Does `CHARGE_CTRL_REACHED` arm? It never has here, at any capacity, including
  at 100%.
- Does the reported percentage keep climbing after current reaches 0? That is
  the outside report's central claim and we have never reproduced it.
- Does anything in `0x07C6` move? Here it read `0x04` in 979 of 980 samples.

### 4. Integrate, the same way we did

Same arithmetic as the Linux run, because it is the check that distinguishes a
real ceiling from a display artifact with no ambiguity:

```
sum(current * dt) over the climb   vs   charge_now(end) - charge_now(start)
```

On this machine over 78 -> 100% those were 1268 mAh and 1276 mAh, 0.6% apart --
every percent backed by real current. If a walk to a faked 100% is happening,
the integral falls short of the delta by roughly the faked amount. The outside
report's own numbers imply 8-9 A during the climb, which no charger here
delivers, so the shortfall should be enormous rather than subtle.

## Reaching the registers from Windows

RWEverything reads EC low RAM (`0x00-0xFF`) directly over ports 0x62/0x66. That
covers test 2's low-RAM dump and nothing else -- the `0x07xx` settings space is
in the extended window and is not port-addressable.

Reach it through the mailbox, which is in low RAM and therefore *is*
port-addressable. Confirmed at `0x8A-0x8E` (same block as `0x048A-0x048E` in the
extended window):

```
0x8A  LDAT   address low
0x8B  HDAT   address high
0x8C  FLAGS  RFLG / WFLG / BFLG / CFLG / DRDY(bit 7)
0x8D  CMDL   data low
0x8E  CMDH   data high
```

To read an extended address: write LDAT and HDAT, set the read flag in FLAGS,
poll until DRDY, then read CMDL. This is what WKBC/RKBC do in AML and what
tuxedo's `uw_ec_*_addr_direct()` does from the kernel.

Pace it. 6 ms between accesses is the figure this project uses everywhere, and
sustained EC traffic is the documented hazard (DESIGN.md 4.2). A 256-byte scan
is 256 accesses.

## Getting a Windows install

This is the actual blocker; the machine runs Linux on the only drive, behind
LUKS. Options, least invasive first:

1. **A spare NVMe.** Physically swap, install Windows and TCC, test, swap back.
   Touches nothing on the Linux drive. Best option if a spare exists.
2. **Windows To Go on external USB.** No internal drive touched, but TCC drivers
   on a To Go image are unreliable, and TCC is the entire point.
3. **Shrink and dual-boot.** Repartitioning a LUKS-backed root next to a
   bootloader we have already fought once over initramfs contents. Not worth it
   for one test.

A VM is not an option. This needs real EC access.
