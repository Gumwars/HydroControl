# Charge control: test plan

Drafted 2026-10-25, after v0.9.8 started exposing `charge_control_end_threshold`
on the HYDROC-16. It turns the open questions from `CHARGE-CEILING-TRIGGER-CHAIN.md`,
`HANDOFF.md` and an outside review of `TCC-SERVICE-FINDINGS.md` into runs, in the
order they should happen.

Everything the threshold claim rests on is **one run** (2026-10-03): the threshold
moved 80 → 60 mid-charge, stopping at 3.9348 V/cell and a reported 47%. That run
was also the first discharge below 5%. Until T1 runs, v0.9.8's claim is "works
after a deep discharge", not "works".

## Rules for every run

These come from DESIGN.md §4.1b and §4.2 and are not negotiable.

- **One write per change.** Set the threshold once, through sysfs. Never hold it
  with a loop: sustained re-writes to the charge registers latched a state that
  survived reboots and took an EC reflash to clear. If it happens again, try
  full power removal (AC out, battery disconnected) before reflashing.
- **Write through the driver, not `ec_write`**, from T0 on. The point is now to
  test the path users get. `charge_ctrl_watch.py --set` bypasses it.
- **Read gently.** Use `charge_ctrl_watch.py`'s adaptive sampling (6 ms pacing,
  fast only near the threshold). Hard EC polling has stopped the fans before.
- **One variable per run.** Record before plugging in: pack (original or
  replacement), profile (`0x07A6`), threshold, starting SoC, ambient
  temperature, and how the previous run ended.
- **Run `baseline_capture.py` before and after** each charging run, so `0x0522`,
  `0x07B9`, `0x07C3` and `0x07D0` are on record at rest.
- **Name logs `<test>-<threshold>-<profile>-<date>.csv`** and commit them with a
  short entry in `CHARGE-CEILING-TRIGGER-CHAIN.md`, including the null results.

## T0 — the driver path works (no charging needed, ~15 min)

Do this right after `sudo ./install.sh --module`.

1. `cat /sys/class/power_supply/BAT0/charge_control_end_threshold`. It should
   equal `0x07B9 & 0x7F`, or 100 when that is 0.
2. Write 70 through sysfs. Read `0x07B9`: bits 6:0 = 70, and bit 7 unchanged.
3. Suspend and resume. It still reads 70 (driver restore on resume).
4. Reboot, then power off and on. Record whether 70 survives each, and what the
   firmware puts there on a cold boot. This decides whether the daemon has to
   re-apply the threshold at boot.
5. The UI threshold card shows the value and writes it, and no drift banner
   appears.

**Pass:** all five. A failure here blocks everything below.

## T1 — run C: is a deep discharge required? (highest priority, ~2 h)

The question HANDOFF.md left open: the 60% stop happened on the first
sub-5% discharge *and* the first sub-ceiling threshold.

1. Discharge to about **40%**. No deep discharge.
2. Unplugged, set the threshold to **60** through sysfs. Profile: Stationary,
   as on 2026-10-03.
3. Plug in. Log with `charge_ctrl_watch.py -o t1-60-stationary-<date>.csv`
   until current has been 0 for 15 minutes.

| result | means |
|---|---|
| stops near a reported 47%, about 3.93 V/cell | the threshold works without a deep discharge; v0.9.8's claim stands |
| charges to the voltage ceiling | something the deep discharge armed matters; v0.9.8 needs a caveat in the UI and release notes |

This also gives the clean version of the 2026-10-03 run: set before plug-in,
no mid-charge change. Capture T2 during the same run.

## T2 — `CHARGE_CTRL_REACHED` at termination (folded into T1)

Read `0x07B9` within a minute of current reaching 0. Armed reads `0xBC` (60 with
bit 7). Every capture so far was mid-charge with the bit clear, which says
nothing. `charge_ctrl_watch.py` already samples fast near the threshold; check
that the `raw` column covers the moment of the stop.

Also note `/sys/class/power_supply/BAT0/status` at the stop. The DSDT `_BST`
reports "charge limiting" whenever CGLM is 1–99, whether or not the EC is the
thing that stopped the charge. See T7.

## T3 — threshold sweep: the 47-vs-60 gap and the crossover (4 runs, ~6 h)

Same procedure as T1, from about 30%, at thresholds **50, 60, 70, 75**. One run
each, same profile, same pack.

Plot threshold against (a) reported % at the stop, (b) V/cell at the stop,
(c) mAh accepted.

- **The gap.** A constant offset (always about 13 points low) points at the
  reported percentage. A proportional one points at a different reference: the
  EC's own SoC, or a percentage of design capacity rather than of `charge_full`
  (the pack holds about 77% of its rating).
- **The crossover.** The threshold at which the stop voltage meets the
  ~4.175 V/cell ceiling. The UI says "above roughly 75"; this measures it. If
  it is a different number, fix the UI text.

## T4 — profile × threshold (3–6 runs)

Every threshold measurement so far was on Stationary. There is also a
contradiction to settle: the outside review calls the 16800 mV target at
`0x0522` fixed regardless of profile, while CHARGE-CEILING-TRIGGER-CHAIN.md
lists `0x0522/23` as the *output* of the profile path.

1. For each profile (High Capacity, Balanced, Stationary), read `0x0522:0x0523`
   mid-charge. Record it at rest too, but `baseline_capture.py` warns the
   resting value may be stale.
2. Repeat T1 (threshold 60, from about 40%) under High Capacity and Balanced.

| result | means |
|---|---|
| `0x0522` same under all three, 60 stops the same everywhere | the two controls are independent; the profiles do nothing on this pack |
| `0x0522` differs by profile | the profile sets the voltage ceiling; the "above 75" line moves with the profile, and the UI has to say so |
| 60 doesn't stop the charge under some profile | they interact; document which combination works |

## T5 — restart point after a stop (~1–3 h, add to T1 or T3)

After a run stops at the threshold, leave it on AC. Record the SoC and voltage
at which charging resumes, or note that it never does over several hours. This
is the hysteresis users will see, and it is the baseline T6 needs.

## T6 — `0x07D0`, the lower threshold (read first, write once)

It has never been read on this machine and nothing calls it.

1. **Read only.** Record `0x07D0` in every `baseline_capture.py` dump from T0 on,
   across boots, profiles and thresholds. Note whether it ever moves on its own.
2. **Only after T5 has a baseline:** one write of 50 with the threshold at 60,
   then discharge to 45% on AC and see whether the resume point moves from T5's.
   Single write, no loop. Restore the original value afterwards.

The Windows service accepts 1..95 and treats 0 as 95; keep the value inside
that range.

## T7 — what the OS reports (piggybacks on T1/T3)

At each stop, record `status`, `capacity`, `current_now`, and what GNOME and
`upower -i` show. Then one extra run at a threshold of **90**, where the voltage
ceiling stops the charge: does the OS still report "charge limiting" or "Not
charging" for a stop the threshold did not cause? If so, the UI should explain
it, because users will read it as the threshold working at 90.

## T8 — run B: deep discharge, threshold set after plug-in (low priority)

Discharge below 5%, plug in, *then* set 60. Only needed if T1 fails. Then B
versus T1 separates the discharge from the ordering, as the matrix in
HANDOFF.md describes.

## Not hardware tests

- **Other Control Center builds.** Check whether any other version calls
  `SetBatteryChargingLimit_Up`/`_Down` (static analysis, as in
  TCC-SERVICE-FINDINGS.md). If one does, it shows how the vendor meant the
  threshold and the floor to be used together.
- **`runc-charge.csv`** (2026-10-05, untracked) is not the run C above. It starts
  at 85% with the threshold at 80, then 100, and reaches Full. Either document
  what it was, or drop it before it gets mistaken for run C.

## Order and what each result changes

| # | test | blocks | if it goes the wrong way |
|---|---|---|---|
| T0 | driver path | everything | fix the driver before any charging run |
| T1 + T2 | run C, bit 7 at stop | T3–T8 | caveat v0.9.8: threshold only after a deep discharge |
| T3 | sweep | UI text | change "above roughly 75" to the measured crossover |
| T4 | profile × threshold | UI text | the ceiling depends on profile; say so beside the slider |
| T5 | restart point | T6 | — |
| T6 | `0x07D0` | — | a second control, if it works |
| T7 | OS reporting | — | explain misleading "charge limiting" in the UI |
| T8 | run B | — | only if T1 fails |

The minimum to stand behind v0.9.8 is **T0 and T1**: one evening.
