# HydroControl — Frontend Design Brief

**For:** OpenDesign, to produce a prototype frontend.
**Deliverable:** UI for a Linux hardware control panel replacing the Windows
"Eluktronics Control Center" on one specific laptop.

This brief covers **what the interface must show and let the user do**. It deliberately
omits register addresses and protocol detail — that lives in `DESIGN.md`. Everything here
is verified against real hardware.

---

## 1. What this is

A control panel for an Eluktronics HYDROC-16 G1 laptop (i9-14900HX, RTX 4090) running
Linux. It replaces a Windows OEM utility that manages fans, power limits, battery
charging, and RGB lighting.

**The user** is the laptop's owner: a technical Linux user, comfortable with hardware
detail, running CachyOS on Wayland with a Quickshell-based desktop (DankMaterialShell).
They already run a terminal UI for the lighting; this is the graphical successor.

**Not a mass-market product.** One machine, one user, no onboarding flow, no accounts.
Density and directness beat hand-holding. But it is used daily, so it should be pleasant
rather than merely functional.

---

## 2. The single most important design constraint

**Almost nothing this app configures survives a reboot.**

The laptop's embedded controller keeps charging profile, CPU power limits, and the
custom-profile latch in volatile memory. On every power cycle they silently revert to
factory defaults. A background daemon re-applies them at boot.

This has a direct UI consequence: **there are two different truths** — what the user
asked for (saved preference) and what the hardware is actually doing right now. They can
disagree, and when they do, the user needs to see it.

A panel that shows a stored preference as if it were live state will confidently display
"Stationary" while the hardware sits in a completely different mode. **Design for showing
actual hardware state, with drift from the saved preference made visible.**

Suggested pattern, not prescriptive: show live state as primary, and surface a subtle
indicator when it differs from the saved profile, with a one-click "re-apply".

---

## 3. Information architecture

Five areas. A dashboard plus four control sections.

| Section | Contains |
|---|---|
| **Overview** | Live telemetry, current mode summary, daemon health |
| **Performance** | CPU power limits, GPU power offset, fan readouts |
| **Battery** | Charging mode, charge threshold, health data |
| **Lighting** | Keyboard RGB, chin bar RGB |
| **System** | Hardware toggles |

Whether these are tabs, a sidebar, or a single scrolling page is your call. Overview is
the landing view.

---

## 4. Live telemetry

Updates roughly once per second. This is the data that makes the app feel alive.

| Reading | Range | Unit | Notes |
|---|---|---|---|
| CPU temperature | 0–100 | °C | Typical idle 45–50, load 80–95 |
| GPU temperature | 0–100 | °C | Idle can be as low as 32 |
| Fan 1 (Main) | 0–6000 | RPM | ~1500 at idle |
| Fan 2 (Secondary) | 0–6000 | RPM | ~1520 at idle |
| Fan 1 / 2 duty | 0–100 | % | Read-only for now — see §9 |
| CPU package power | 0–150 | W | ~25 idle, up to the configured limit under load |
| Battery charge | 0–100 | % | |
| Battery current | ±5000 | mA | Positive charging, zero when full |
| Battery voltage | 14–17 | V | Also useful per-cell (÷4): 3.6–4.21 |
| AC connected | bool | | |

**Charts matter here.** Temperature and power over a rolling window (say 60 s) tell the
user far more than instantaneous numbers. Fan RPM alongside temperature shows the cooling
system responding.

---

## 5. Controls

### 5.1 Performance

| Control | Type | Range | Notes |
|---|---|---|---|
| CPU power limit | slider or stepper | 15–125 W, plus "firmware default" | Verified accurate to the watt. Default ships at 75 W. Below ~25 W the machine is sluggish; warn rather than block. |
| GPU power offset | slider | 0 to device max | Watts added on top of base GPU power. Currently reads 0. Setting the maximum disables NVIDIA Dynamic Boost — worth a note in the UI. |
| Custom profile mode | toggle | on/off | Must be **on** for the CPU power limit to take effect. When off, the limit control should appear disabled with an explanation rather than silently doing nothing. The laptop's physical profile LED turns white when this is on. |

**Interaction note:** these three are coupled. Custom profile mode is a prerequisite.
Consider presenting the power limit as unavailable-until-armed rather than as an
independent slider.

### 5.2 Battery

| Control | Type | Values |
|---|---|---|
| Charging mode | segmented / radio | Stationary · Balanced · Long Haul |
| Charge threshold | slider | 1–100 % |

**Naming — important.** The underlying system reports these as `Trickle`, `Long_Life`,
and `Standard`, which map to the Control Center names as:

| Show this | System token |
|---|---|
| Stationary | `Trickle` |
| Balanced | `Long_Life` |
| Long Haul | `Standard` |

Use the left column. The system tokens are confusing (`Long_Life` is *not* "Long Haul").

**Do not describe these as percentage caps.** We verified that none of them visibly limits
charging on this firmware — the machine charges to 100% in every mode. The vendor
describes them only as usage patterns. Honest framing:

- **Stationary** — mostly plugged in at a desk
- **Balanced** — mixed use
- **Long Haul** — maximise runtime away from power

Avoid claims like "charges to 80%". We don't know that, and asserting it would mislead.

**Health data to display:** charge cycles (real value available; the OS-reported one is
wrong and reads 0), design capacity, current capacity, and per-cell voltage. A user who
cares about this machine cares about battery longevity — it's the feature that sold them
the laptop twice.

### 5.3 Lighting

**Keyboard** — 6 rows × 21 columns of individually addressable keys.

| Control | Type | Range |
|---|---|---|
| Mode | tabs | Effect · Per-key |
| Effect | picker | breathing, wave, random, rainbow, ripple, marquee, raindrop, aurora, fireworks |
| Effect colour | palette picker | 8 fixed colours + random — **not** a free colour picker |
| Effect speed | slider | 1–10 |
| Effect direction | picker | right, left, up, down (only some effects) |
| Per-key colour | keyboard map + colour picker | full RGB per key |
| Brightness | slider | 0–50 |
| Save to keyboard | button/toggle | Writes to the keyboard's own memory so it survives power loss |

**Design challenge:** the per-key editor is the most interesting surface here. A visual
keyboard where the user paints keys, with selection, fill-all, and rows/regions. Worth
real attention.

**Constraint to respect:** effects accept only a palette index, not arbitrary RGB. Per-key
mode is unrestricted. Don't offer a colour wheel where only eight colours work.

**Chin bar** — a light strip on the front edge. Single zone, no per-pixel.

| Control | Type | Range |
|---|---|---|
| Mode | picker | Static · Breathing · Off |
| Colour | colour picker | full RGB |
| Brightness | slider | 0–100 |
| Speed | slider | 1–10 (breathing only) |

Four further effects (wave, clash, catchup, flash) are likely available but unverified —
design the picker so entries can be added without rework.

### 5.4 System toggles

| Control | Values | Notes |
|---|---|---|
| Fn lock | on/off | |
| Super key | on/off | Disables the Windows/Super key |
| Touchpad toggle hotkey | on/off | |
| AC auto-boot | on/off | Powers on when AC is connected |
| USB powershare | on/off | USB power while off/hibernating |

**Hard constraint:** AC auto-boot and USB powershare **cannot both be on** — the driver
rejects it. Do not present two independent switches that can reach an invalid state.
A three-way choice (neither / AC auto-boot / USB powershare) is safer.

---

## 6. States to design for

This app talks to hardware through a privileged daemon. It will not always be healthy.

| State | What the user should see |
|---|---|
| **Normal** | Live values, everything interactive |
| **Daemon not running** | Clear, actionable — the daemon is what applies settings. Offer to start it. Telemetry unavailable. |
| **Kernel module not loaded** | Fans, temps, battery modes, and toggles are all unavailable. Distinct from the daemon being down; the message should say which. |
| **Settings drifted** | Hardware differs from saved profile (typical after a reboot before the daemon runs). Show both, offer re-apply. |
| **Applying** | EC writes take up to a second and some need a verify read. Brief pending state; don't let the UI look frozen. |
| **Write rejected** | Some writes are silently ignored by hardware. The daemon verifies by reading back. Surface a genuine failure rather than showing success. |

Error copy should say what happened and what to do. No apologies, no vagueness.

---

## 7. Environment

- **Wayland**, on a Quickshell/DankMaterialShell desktop. Expect a dark-leaning desktop,
  though don't assume — support light and dark.
- The user already has a **DankMaterialShell widget** for lighting. Consider whether this
  app is standalone, a shell panel, or both. A compact "quick controls" layout — mode,
  power limit, fan readout — would suit a shell popout; the full app can be denser.
- Screen is a 16" laptop panel, likely 2560×1600. Design for that first; it may also be
  used on an external display.

---

## 8. Tone and character

This is a utility for someone who reverse-engineered their own laptop. It should feel
**precise and instrumented** — real numbers, live data, no rounding away detail. Closer to
a well-made diagnostic tool than a consumer settings app.

Avoid: marketing language, gamer aesthetics (RGB gradients everywhere, angular panels,
"BEAST MODE"), oversized empty hero areas, and hiding numbers behind vague labels.

Favour: legible data density, monospace for numeric readouts, restraint in colour so that
status colour actually means something, and charts that reward looking at them.

---

## 9. Out of scope for the prototype

Do not design controls for these — they cannot be driven yet:

- **Fan curves.** The hardware supports manual fan control but the driver hasn't
  implemented it. Fan speeds are read-only. Leave visual room for a curve editor later;
  don't build a placeholder that does nothing.
- **Performance mode presets** (Office / Gaming / Turbo). The encoding isn't decoded yet.
  The CPU power limit slider covers the same ground for now.
- **Keyboard effect animation with arbitrary colour.** Would require driving frames from
  the host; not currently implemented.

---

## 10. What to hand back

A prototype covering the five sections in §3, with:

1. Overview screen showing live telemetry, current state, and daemon health
2. Each control section with the real controls, ranges, and units from §5
3. The drift/re-apply pattern from §2 — this is the pattern most likely to be got wrong
4. The per-key keyboard editor
5. At least the "daemon not running" and "settings drifted" states from §6

Static screens are fine; interaction notes welcome where behaviour matters. We'll wire it
to the daemon afterwards.
