# TCC offset: what drives it, and what survives

Drafted 2026-11-25, with the CPU temperature limit control. The CPU throttles at
TjMax minus the TCC offset; on this i9-14900HX TjMax is 100 °C.

## What is known

**Control Center sets it through the EC only.** Disassembled from
GCUService.exe 1.0.2.70 (`MyFanManager_RamFan1p5`, the manager this machine
runs):

```
SetCpuTccOffset(offset, enable):
    EC[0x0786] = enable ? (0x80 | offset) : 0
```

No MSR write anywhere. It is called from `SetUserProfile` (every mode switch),
`UserSet_Mode_Detail`, and from `Disable` / `Uninstall` with `(0, false)`. A
profile with the limit switched off also sends `(0, false)`. The EC keeps a
default offset per mode, which Control Center only reads:

| | balanced | office | beast |
|---|---|---|---|
| address | `0x07D8` | `0x07D9` | `0x07DA` |
| stored (every dump so far) | 5 | 15 | 5 |

`ADDR_TimAP_TccOffset_Setting = 0x0786` was read out of the service's metadata,
the same way the charge addresses were. The method was checked by reproducing
`0x07B9` and `0x07D0`. So was the caller search: it finds zero callers for the
charge setters, as TCC-SERVICE-FINDINGS.md did.

**The CPU side is visible on Linux.** The kernel's `intel_tcc_cooling` driver
registers `/sys/class/thermal/cooling_device*` of type `TCC Offset`, which reads
and writes MSR_TEMPERATURE_TARGET bits 29:24. It only registers when the offset
is programmable and unlocked. On 2026-11-25 it read **5**, with thermald not
running and no thermal zone bound to it.

**The working hypothesis:** with `0x0786` at 0, the EC puts its per-mode default
into the CPU. Linux never writes `0x0751`, so the EC sees Balanced, and
Balanced's default is 5. Nothing below has tested that yet.

## Before any run

- **Deploy first.** The probe and the daemon now share a cross-process lock on
  the EC (`/run/hydroc/ec.lock`), because `/proc/acpi/call` is one global
  request/response file and two unlocked users read each other's answers. A
  daemon from before this change does not take the lock. Copy the repo to
  `/opt/hydrocontrol` and restart `hydroc-server` before using the probe, or
  stop the daemon for the run.
- **One write per change.** The probe's `set-ec` and `set-msr` each write once
  and then only read. Never hold a value with a loop (DESIGN.md 4.1b).
- **Keep the profile out of it while measuring survival.** `hydroc-apply` runs
  at boot and resume and would put a profile's TCC setting straight back. Until
  T5, leave `cpu_tcc_enable` / `cpu_tcc_offset` unset (the default), so what
  you measure is the firmware's.
- Every run appends to `tcc-survival.csv`. Commit it with a short note of what
  was done between rows.

```bash
sudo python3 tcc_survival_probe.py snap "baseline"
```

## T1 — baseline: is 0x0786 empty, and does the CPU hold the mode default?

`snap` on AC, in our custom profile, then once on each native mode from the
app (Office, Balanced, Beast), then back to a preset.

| result | means |
|---|---|
| `0x0786 = 0x00`, CPU 5 on Balanced, 15 on Office | the EC applies its per-mode default when 0x0786 is off |
| CPU stays at 5 whatever the mode | the BIOS set it at boot and nothing changes it; the EC defaults are only data |

## T2 — does the EC drive the CPU from 0x0786?

```bash
sudo python3 tcc_survival_probe.py set-ec 10
```

It snapshots, writes `0x8A` once, then follows for 60 s at 1 s.

| result | means |
|---|---|
| CPU goes to 10, and how fast | **the EC drives it**; the control works as built |
| CPU stays at 5 | 0x0786 alone is not enough; try again with the custom latch armed and with it clear (T4) |

Then `set-ec off`: does the CPU return to the mode default?

## T3 — does the EC overwrite a value written to the CPU directly?

With `0x0786` off:

```bash
sudo python3 tcc_survival_probe.py set-msr 10
```

```bash
sudo python3 tcc_survival_probe.py watch --minutes 10
```

| result | means |
|---|---|
| CPU drifts back to 5 | the EC re-asserts its value; writing the MSR from Linux is pointless |
| CPU stays at 10 | the EC only writes at events; T4 finds which |

Repeat with `set-ec 20` in force and `set-msr 10`: which one wins, and when?

## T4 — events

For each event: `snap` before, do it, `snap` after. Run each twice: once with
`0x0786` off, once with `set-ec 10` in force.

1. Custom latch toggled (Performance → Custom profile mode)
2. Native mode switched (app, or the profile button)
3. AC unplugged, then plugged in
4. Suspend and resume (`snap` immediately on wake, before anything else)
5. Warm reboot (`snap` as soon as you can log in)
6. Full power-off, AC out for 10 s, power on

Note `0x0786` itself on each row. Whether the EC keeps it across 5 and 6 is the
same question the charge threshold answered with "no" (it reads 100 after every
cold boot).

## T5 — the daemon puts it back

Once T2 shows the EC drives the CPU, set a limit in the app (Performance → CPU
temperature limit), then repeat T4.4 and T4.6. `hydroc-apply` re-applies at
boot and resume, and **Hardware differed at …** on the System page records what
`0x0786` held before it did. The card itself shows the offset the CPU is
running, and says so when it does not match the setting.

## Results

### 2026-11-26: T1 (part), T2, T3a

On AC, custom latch armed, `0x0751 = 0x00` (Balanced) throughout; per-mode
defaults 5 / 15 / 5 in every row. Log: `tcc-survival.csv`, rows 0–25.

| step | 0x0786 | CPU offset | when |
|---|---|---|---|
| baseline | `0x00` | 5 | — |
| `set-ec 10` | `0x8A` | 5 → **10** | next 1 s sample, every time (3 of 3) |
| `set-ec off` | `0x00` | 10 → **5** | next 1 s sample, every time (3 of 3) |
| `set-ec off`, already off | `0x00` | 5, unchanged | — |
| `set-msr 10` with 0x0786 off | `0x00` | 5 → 10 | immediate (the kernel's own write) |
| then 60 s follow + 10 min watch | `0x00` | **stays 10** | no change in 11 min |

**The EC drives the CPU from 0x0786, within a second.** Enabled, it puts the
offset into the CPU. Off, it puts the current mode's default there: 5 is
Balanced's, and `0x0751` says Balanced. That confirms the baseline reading.

**The EC writes the CPU when 0x0786 changes, not continuously.** With 0x0786
off it left a direct write of 10 in place for 11 minutes, although its own
default was 5. So a Linux write to the CPU sticks until some event makes the EC
write again, and the CPU can disagree with what the EC holds. Which events do
that is what T4 is for.

**What that means for the daemon.** `apply()` compares the profile with the EC
byte, not with what the CPU runs. If an event resets the CPU but leaves 0x0786
alone, apply() will see nothing to do and the limit will be lost silently,
though the card will say so. Whether any event does that is T4's answer. If one
does, apply() should also re-write when `cpu_tcc_applied` disagrees.

### 2026-11-26: T3b

Rows 26–34, same conditions.

| step | 0x0786 | CPU offset | when |
|---|---|---|---|
| `set-ec 20` | `0x94` | 10 → **20** | next 1 s sample |
| `set-msr 10` | `0x94` | 20 → 10 | immediate |
| then 60 s follow + 10 min watch | `0x94` | **stays 10** | no change in 12 min |

**The EC does not enforce even an enabled 0x0786.** A direct CPU write of 10
stood for 12 minutes against an EC setting of 20. So in steady state the EC
writes the CPU only when 0x0786 changes, enabled or not.

That leaves the machine in the best starting state for T4. The EC says 20, the
CPU runs 10, Balanced's default is 5 and Office's is 15, so after any event the
CPU's value says who wrote it.


### 2026-11-26: T4, first part

Rows 35–55. The labels are mixed up: rows 44–47 all read "T4.3 unplugged".
They are lined up here by their latch and AC columns and by the journal, which
shows a suspend at 11:19:48–11:19:57 and `hydroc-resume` at 11:20:00.

| event | starting state | CPU after | 0x0786 after |
|---|---|---|---|
| custom latch off (row 45) | EC 20, CPU 10, AC | 10 | `0x94` |
| AC unplugged (row 46) | EC 20, CPU 10, latch off | 10 | `0x94` |
| s2idle suspend, ~9 s, on battery (row 47) | EC 20, CPU 10, latch off | 10 | `0x94` |
| AC plugged in (row 54) | EC off, CPU 10, latch off | 10 | `0x00` |

**None of these made the EC write the CPU, and none touched 0x0786.** The CPU
kept the direct write of 10 through all four, whether the EC said 20 or off.
`hydroc-resume` changed nothing either: the profile holds no TCC keys, and it
already said `custom_profile: false`, saved when the latch was turned off.

This machine suspends to **s2idle**, a shallow sleep in which the CPU keeps
its state. A deep S3 sleep could differ; none was tested.

### 2026-11-26: T4, the rest

Rows 56–65, EC off (`0x0786 = 0x00`), CPU at 10 from a direct write.

| event | CPU after | 0x0786 after |
|---|---|---|
| custom latch on (row 56) | 10 | `0x00` |
| back to a preset (row 59) | 10 | `0x00` |
| **warm reboot** at 11:31:37 (row 63) | **5** | `0x00` |

**A boot puts the firmware's default back in the CPU:** 5, Balanced's, with
`0x0751` reading Balanced. Nothing else tested in T4 touched it. On Windows the
service then overwrites it when it starts: `SetUserProfile` writes 0x0786 on
every profile apply, the first one included. `hydroc-apply` does the same job
here at boot.

**The native-mode test did not happen.** Both "native office" rows still read
`0x0751 = 0x00` (Balanced), so the switch never reached the EC. Office's
default is 15, so a real switch would show whether a mode change re-applies the
default. It is the one cheap run left: pick the machine's own Office mode, check
that `mode` reads office, then `snap`.

**The remaining reboots were not run, by choice.** They would only settle
whether 0x0786 itself survives a warm reboot. That mattered because apply()
compared the profile with the EC byte alone: a surviving `0x8A` over a CPU
reset to 5 would have looked like nothing to do. Rather than test for it,
apply() now covers both answers. When the limit is enabled and the CPU is not
running it, apply() drops 0x0786 to 0 and sets it again, giving the EC the
change it acts on, even if the byte already matched.

**Untested:** a full power-off, and an S3 sleep (this machine uses s2idle).
Either would reset the CPU at least as hard as a warm reboot, and apply()'s
check covers whatever they leave behind.

## What each answer changes

| finding | change |
|---|---|
| EC drives the CPU from 0x0786 (T2) | the control stands as built |
| it needs the custom latch (T2/T4.1) | gate it like the power limits, or arm the latch first |
| the EC re-asserts on events (T3/T4) | re-apply on those events, as the daemon does for power on AC change |
| 0x0786 is lost on power-off (T4.6) | nothing new: `hydroc-apply` restores it, like the charge threshold |
| the EC never drives it (T2 fails both ways) | write the MSR through the kernel instead, and re-apply on events |
