# HydroControl — handoff

Linux replacement for the Eluktronics Control Center on an **Eluktronics
HYDROC-16 G1** (Uniwill/Tongfang chassis, i9-14900HX + RTX 4090, EC firmware
`117.ELUK`, `PROJECT_ID` `0x19`). Reverse-engineered from Linux — no Windows
install was ever needed, though the Windows app's own files in `hydroc-driver/`
have since answered several questions for free.

**State:** working, in beta with two external testers on identical hardware, and
one enquiry from a Prometheus G2 (sibling Uniwill chassis — see `compat_probe.py`).
Fans, temps, CPU power limits, GPU cTGP, battery policy, keyboard and chin bar
RGB, performance presets and the LPP cooling dock all functional. **MIT
licensed** (see the GPL carve-out below). A browser UI *and* a GTK4 desktop app
on one privileged Python daemon. Tester bundle is built by `make-bundle.sh`
(~136 KB; excludes ~830 MB of Windows reference material in `hydroc-driver/`).

---

## Orient yourself in two minutes

```bash
cd /home/gumwars/HydroControl
sudo python3 -m hydroc.cli doctor      # every prerequisite, with fixes
hydrocontrol                           # desktop app (or: sudo python3 -m hydroc.server)
```

Read in this order: **`DESIGN.md`** (architecture, corrections, dead ends) →
this file → the code. `OPENDESIGN.md` is the frontend brief;
`KEYBOARD_LAYOUT.md` is the keyboard geometry spec.

---

## The seven things that matter most

Everything else is derivable. These are not.

### 1. `0x0741` bit 0 is the master switch, and it sits ABOVE the TDP latch

`ENABLE_MANUAL_CTRL`. `uniwill-acpi.c` sets it in `uniwill_ec_init()` and
**clears it when the module unloads**. With it clear:

* `0x0727` bit 6 still reads **armed**
* power-limit writes are still **accepted**, and `write_verify` still **passes**
* and the EC ignores all of it and runs its own 75/75/250 defaults

Every indicator this project has says healthy. Anything that unloads the module
clears it — `install.sh --module`, a DKMS rebuild, a stray `modprobe -r`. The
fix is reloading the driver, which `doctor` now checks for and offers as a
**Repair**. See DESIGN.md §4.1. This cost a whole session and produced a handoff
blaming the wrong register.

### 2. The EC has two windows and only one is writable

Both reached through `\_SB.INOU.ECRR` / `ECRW` (MMIO at `0xFE410000`), via
`acpi_call`.

| Window | Purpose |
|---|---|
| `0x07xx` | **settings** — the write side |
| `0x04xx` (ECXP, base + 0x400) | **live readback** — writes accepted and silently discarded |

`ECRW` reports success for writes that do nothing. Every write is read back —
`hydroc/ec.py::write_verify`.

**But `write_verify` proves a register *took* a value, not that it *survived*.**
It reads back after 50 ms; the EC can zero a register later on its own schedule
and the check will have already passed. Drift detection is what catches that.

### 3. The EC needs 6 ms between accesses

`uniwill-acpi.c` sleeps `usleep_range(6000, 12000)` after every `ECRR`/`ECRW`
because the OEM software does. `EC._call` enforces it class-wide. Going faster
has stopped the fans. Bulk scans are still the hazard: 512 reads is 3 s of solid
EC traffic, a full dump 25 s — never under load. See DESIGN.md §4.2.

### 4. CPU power limits: a latch, a half scale, and a quantiser

```
0x0727 bit 6   custom-profile latch — TDP writes are ignored without it
0x0727 bit 7   "double PL4" — set on this machine
0x0783 PL1     sustained  (watts, 0 = firmware default)
0x0784 PL2     boost
0x0785 PL4     peak — HALF SCALE when 0x0727 bit 7 is set
0x046A/B/E/F   the LIVE mirror — PL4 is half-scale HERE TOO
```

Half-scale storage means **odd watts do not exist**: ask for 125 and the
register holds 62, reads back 124, and the profile disagrees with the hardware
forever. `Hardware.normalize()` snaps intent before writing, comparing *and*
persisting. Verified to the watt against the RAPL **energy counter** — the RAPL
limit fields report 205 W regardless, because the EC enforces outside the MSR
path.

### 5. Almost nothing persists — that is why a daemon exists

The charging profile (`0x07A6`), the `0x0727` latch, the power limits and the
chin bar all live in volatile RAM and revert on **every power cycle**.
`hydroc-apply.service` and `hydroc-resume.service` are our `UniwillService`.

Useful safety consequence: **a full power cycle always restores factory
defaults.** Nothing this app writes can be permanently wedged, and no code path
here has ever written flash.

### 6. Intent and state are different things

The profile is *intent*; the hardware is *state*. Drift = hardware changed
underneath you. A user changing a control is **new intent** and must be written
to the profile too — `/api/apply` takes `persist`: true for control changes,
**false for Re-apply**.

When drift reports something, that is drift **working**. Twice now it has been
read as the bug rather than the diagnosis.

### 7. Resolve by identity, never by index

| Surface | Resolve by |
|---|---|
| Chin bar | hidraw whose `uevent` contains `0000048D:00007001` |
| Keyboard | USB VID/PID `048d:600b` (in **no** released `ite8291r3-ctl`) |
| Fans/temps | hwmon whose `name` reads `uniwill` |
| Profile button | input device named `Uniwill WMI hotkeys` |
| LPP dock | BLE name contains `CoolingSystem` |

A hardcoded `/dev/hidraw1` or `event26` is the likeliest cause of any "works
sometimes" bug.

---

## Layout

```
hydroc/            the application
  ec.py            EC access — 6 ms pacing, verified writes, thread-serialised
  hardware.py      settings surface: read_state / normalize / apply / drift
  fancurve.py      EC fan-curve tables; populate-then-enable enforced in code
  presets.py       Office / Balanced / Performance, matched against live state
  hotkeys.py       KEY_F14 listener — the physical profile button
  rgb.py           visual key id -> matrix bridge; chin bar modes
  lpp.py           LPP dock protocol (pure, no transport)
  lppd.py          LPP sidecar daemon — BLE, Unix socket at /run/hydroc/lpp.sock
  deps.py          "can the ROOT daemon import this?" — one answer, three-valued
  kmod.py          refuses a module unload with no file to load back
  cli.py           status · state · telemetry · drift · apply · doctor
  server.py        loopback HTTP bridge, /api/*
  desktop.py       GTK4 + WebKitGTK window; pkexec to start the daemon
  ui/index.html    the app — no build step, no dependencies
  systemd/         apply · resume · server · lpp units (@INSTALL_DIR@ templated)

tests/             unittest suite — pure logic, no hardware, no dependencies
kbctrl/            RGB transports (ITE 8291 libusb, ITE 8233 hidraw)
uniwill-laptop/    patched kernel module — GPL-2.0-or-later, do NOT copy out
hydroc-driver/     Windows reference material (~830 MB, excluded from bundles)
```

**Probes** (`compat_probe.py`, `ec_state_capture.py`, `lb_mode_probe.py`,
`profile_button_probe.py`, `perfmode_listen.py`, `fan_*.py`,
`charge_path_probe.py`) are read-only or restore-on-exit unless their docstring
says otherwise. Read the docstring.

Only the safe subset ships in the tester bundle (`compat_probe.py`,
`ec_state_capture.py`, `charge_path_probe.py`, `lb_mode_probe.py`,
`lb_set_color.py`). The rest are checkout-only on purpose — `fan_write_probe.py`
arms a latch that needs a power cycle to undo, and that does not belong in a
stranger's hands.

**Optional matugen palette.** `matugen/hydrocontrol.css` is a template; matugen
renders it to `~/.config/hydroc/palette.css` and `hydroc/desktop.py` injects it
into the WebView, re-injecting when the file changes so a wallpaper switch lands
without a restart. Absent, the built-in palette stands and nothing degrades.

Three things worth knowing before touching it. It is **desktop-only on purpose**:
the app runs as the user, so `$HOME` resolves correctly there — the daemon runs
as root under systemd and must never read that path (§4.4). **Status colours are
never generated** — `--color-warn`, `--color-danger`, `--color-info` stay fixed,
because a wallpaper must not decide what "danger" looks like on a panel whose
controls can overheat a machine. And **Material You's dark-mode roles run
opposite to the app's naming**: `primary_container` is dark while
`on_primary_container` is light, so mapping by name rather than by lightness
inverts the ramp and renders every light accent tint nearly black. Map by
lightness, and render the template before believing it.

**Superseded — do not run:** `lb_output_probe.py`, `lb_effect_probe.py`,
`lb_effect_sweep2.py`, `charge_mode_probe.sh`, `chinbar_test.py`.

---

## Licensing

**MIT**, except `uniwill-laptop/` which is **GPL-2.0-or-later** (derived from
tuxedo-drivers). One-way boundary: MIT may go into GPL, nothing comes out of
`uniwill-laptop/` into `hydroc/` or `kbctrl/`. `ite8291r3-ctl` and `acpi_call`
are GPL-2.0 and installed separately, not redistributed.

Vendor data in `hydroc-driver/` does **not** become MIT by being copied. Use it
as reference; derive our own values.

---

## Traps

**The driver's DMI table is not the whole story.** `install.sh` refuses to run
off a HYDROC-16, deliberately. `compat_probe.py` gathers evidence about a
sibling chassis read-only; a good result justifies adding a board to the
allowlist, never bypassing the guard.

**Kernel updates break the module — mitigated by DKMS.** A stale hand-installed
copy in `/lib/modules/<ver>/updates/` *shadows* DKMS's `updates/dkms/`;
`install.sh` purges those. Symptom is "hwmon is down", not "module rejected".

**Never unload the module without checking there is a file to load back —
`hydroc/kmod.py`.** A rolling-distro kernel upgrade replaces
`/lib/modules/<version>` while the old kernel is still running. Everything keeps
working, because the modules are resident; but there is nothing on disk to load,
and DKMS has built for the kernel you will boot **next**. `modprobe -r` then
succeeds where `modprobe` cannot, leaving no driver and `0x0741` bit 0 clear
(§4.1) until a reboot — irreversible, from a button labelled Repair. This is
*not* the shadowing trap above: there DKMS is the mitigation, here DKMS is
exactly what does not help. `safe_to_reload()` gates both unload sites
(`hydroc/server.py` Repair, `install.sh`), `doctor` reports it as **kernel
module tree**, and the reload Repair is withdrawn while it is true.

Note also that `dkms status` is per-kernel, so during that window it lists the
next kernel and not the running one. Reading that as "not DKMS-managed" and
recommending a rebuild is confident wrong advice (§4.3); `diagnose()` special-
cases it.

**The EC accessor must stay serialised.** `/proc/acpi/call` is one global file.
`EC._lock` handles it; do not remove it. A garbled reply must raise
`ECUnavailable`, never `ValueError`.

**`FAN_MODE_USER` (`0x0751` bit 7) is a one-way door.** Arming it takes the fans
off automatic and they stop, and *clearing the bit does not hand control back* —
only a power cycle does. `fan_write_probe.py` refuses to arm it without
`--i-will-power-cycle`.

**Never read the fan tacho registers** (`0x0464/5`, `0x046C/D`) through `ECRR`.
It correlates with the fans stopping. hwmon reports the same values safely.

**Anything resolved from `$HOME`, `$USER`, `$SUDO_USER` or `$XDG_*` is
launcher-dependent.** `ite8291r3-ctl` lives in a user pipx venv; under `sudo` it
resolves, under systemd it does not. Adding a second way to start a daemon adds
a second environment for every path lookup in it. DESIGN.md §4.4.

**Never ask "can I import X" — ask `hydroc.deps`.** This trap has landed three
times: `ite8291r3-ctl` under `pkexec`, `bleak` under systemd, and `install.sh`
telling a tester to `pip install bleak` without `sudo`, which lands in
`~/.local` where the root sidecar cannot see it. The daemons run as **root under
systemd**, so an import in the calling shell answers a different question.
`hydroc/deps.py` is the only correct answer: it probes in a subprocess that
reproduces the daemon's environment (root, `HOME=/root`, no inherited
`PYTHONPATH`), returns **three** values — ok / missing / *unknown when the
caller is not root* — and owns the install advice so no two surfaces can print
different remedies. `install.sh`, `doctor` and `lppd` all go through it.

    python3 -m hydroc.deps check bleak     # 0 = ok, 1 = missing, 2 = unknown

Adding a dependency means adding a `Dep` there, not an `import` in a check.

**An unprivileged check is UNKNOWN, not FAILED.** The desktop app runs as the
user, the daemon as root. `diagnose()` marks root-requiring checks `needs_root`
and returns them `unknown` when the caller is not root. Reporting them as
failures told a user their keyboard library was missing when it was installed
and working. DESIGN.md §4.3.

**`ite8291r3-ctl` changed its API between 0.3 and 0.4** (`usb_channel` removed,
`get()` re-signatured). `_ite_handle` picks by capability probe. `0x600B` is in
no released version's `PRODUCT_IDS`, so `kbctrl` owns its own list.

**A cached BLE address is not a reachable device.** `bluetoothctl devices` keeps
entries long after a peripheral goes silent. The LPP dock stops advertising once
connected-then-dropped, so `bleak` can never rediscover it — attach via BlueZ's
existing D-Bus object instead.

**The EC lightbar registers are inert** (`0x0748`–`0x074B`). The chin bar is ITE
8233 over hidraw, full stop.

**Renaming a setting key produces permanent phantom drift.** Add renames to
`LEGACY_KEYS`; never alias them in `read_state`.

**Only zero-current battery voltage readings are meaningful.** Under charge,
`voltage_now` reports the charger's applied voltage.

---

## Open questions

**Fan control — answered. A userspace fan curve works.** Registers:

```
0x0751  MANUAL_FAN_CTRL   b2:0 level, b4 TURBO, b5 HIGH, b6 BOOST, b7 USER
0x075B/C  PWM_1/2         0..200 (PWM_MAX), hwmon rescales to 0..255
0x07C5  UNIVERSAL_FAN_CTRL  b7 SPLIT_TABLES
0x07C6  AP_OEM_6            b2 ENABLE_UNIVERSAL_FAN_CTRL
0x0F00/10/20  CPU DownT / UpT / Duty   16 points each
0x0F30/40/50  GPU DownT / UpT / Duty
```

Verified 2026-08-30 with `0x0741` bit 0 confirmed set — the earlier null ran with
it clear and was void. `pwm1` moved 76 → 102, which is EC raw 80 = **40%**,
exactly our curve's point 2 (`UpT 50 / DownT 45 / Duty 40`), selected when the CPU
touched 50 °C and **held at 40% as it fell to 48 °C** because `DownT` 45 had not
been crossed. Both the duty and the hysteresis we wrote were honoured.
**`0x07C6` bit 2 is reversible** — unlike `FAN_MODE_USER`.

Three things to know before touching it:

* **Populate the tables BEFORE setting the enable bit.** They ship empty
  (96 bytes of `0x00`) and the bit ships clear. Enabling first hands the fans a
  curve that reads zero at every temperature.
* **Write an aggressive curve, not a quiet one.** If an enable bit ever turns out
  to be one-way, stuck-loud is recoverable and stuck-hot is not.
* **`0x1804`/`0x1809` read `0xFF` through `ECRR`** — unmapped. Upstream's "WMI
  interface only" is confirmed; they are not a fallback anyone is neglecting.

Tools: `fan_table_write_probe.py` (is the region writable — bounded, restores),
`fan_universal_enable_probe.py` (enable, observe, restore, and measure
reversibility), `fan_curve.py --read/--write`, `fan-curve-hydroc16.json` (ours,
not vendor data).

**Performance modes: answered — they are not in the firmware.** The button
raises WMI `0xB0`, `uniwill-laptop` maps it to `KEY_F14`, and nothing else
happens; on Windows the OEM service decides. We listen for the keypress and
apply our own presets. DESIGN.md §3.7.

**LPP dock.** The RX characteristic is a *mixed* channel: ASCII (`sw` → `"MCU
F/W Version: 2.0.0.4"`) interleaved with framed replies (`FE 31 05 02 EF`,
opcode `0x31`, payload `0x05`). Notifications can arrive **fragmented** — any
parser must reassemble on `FE`…`EF`. Control Center has `strPumpStatus` and
`LCPUMP_DUTY`, so state is readable and the pump may take a duty rather than
four modes. `lppd` keeps the last 40 notifications for correlation.

**Charging modes do nothing observable.** Mapping confirmed against
tuxedo-drivers; neither `Trickle` nor `Long_Life` caps charging over one cycle.
Do not present them as percentage caps.

**The charge threshold is never enforced — answered, and the feature dropped.**
A full cycle logged on 2026-08-30 with `charge_ctrl_watch.py`: with `0x07B9`
holding **80% unchanged**, the pack charged **76% → 100% without pausing**, and
`CHARGE_CTRL_REACHED` never armed at any sample, including at 100%. Not a
clobber (the register never moved), not hysteresis (charging never stopped), not
a lost write (it read back correctly throughout). The EC stores the threshold and
never evaluates it.

`charge_control_end_threshold` existed only because `hydroc16g1_descriptor`
claimed `UNIWILL_FEATURE_BATTERY_CHARGE_LIMIT` — our own DMI assertion carried
over from sibling chassis. Nothing in the EC advertises it, and `0x078E` bit 3 is
`CHARGING_PROFILE`, not a charge-limit capability. The bit is now dropped, as
`LIGHTBAR` already was in the same struct, because that attribute is a standard
interface and GNOME and TLP were reading it too. See DESIGN.md §3.2.

**The 30% shutdown.** Machine powers off at ~30% reported charge at idle.
Discriminator: cell voltage at cutoff (~3.6 V/cell = real reserve; ~3.0–3.2 =
gauge reads high). `battery_watch.py` flushes per sample.

**GPU MUX.** `card2-eDP-2` exists on the NVIDIA card (panel is wireable to the
dGPU) and Control Center has `DgpuOnly`, so a MUX exists. envycontrol cannot
flip it — that's a driver-level tool, not a mux switch. Check the BIOS first;
a wrong MUX write leaves no display, making it the highest-stakes write here.

---

## Next

1. **Done — universal fan control works, and it is in the app.** See the fan
   entry above. Curves live in `hydroc/fancurve.py`, one pair per preset, with
   "populate then enable" enforced in code rather than remembered.
2. **Answer the emergency-override question.** With universal control on, the EC
   is running *our* table, and §4.2 established the firmware's own ramp lives on
   the same loop we are overriding. Nobody has tested whether it still saves you
   at Tjmax. That answer decides whether `MIN_DUTY` (currently 25%) can come
   down and the stock silent-below-55 °C band can come back — right now we give
   it up on purpose, and the UI says so.
3. **Charge threshold — done, and the answer was no.** Measured, not enforced,
   feature bit dropped. If it is ever revisited, the open part is whether any
   gate exists that switches EC enforcement on; `0x0497` bit 5 and `0x0984`
   bit 3 are both dead ends and are recorded as such in DESIGN.md §3.2.
4. **LPP**: decode the `0x31` reply frame; probe the ASCII console (`?`, `help`)
   carefully — it drives a pump.
5. **envycontrol integration** by detect-and-delegate, not vendoring.
6. **Rust port.** `hydrocd` + Tauri v2 reusing `ui/index.html` unchanged. Note
   Tauri on Linux uses webkit2gtk, so it carries the same 130 MB dependency the
   Python desktop app does. `Intent` and `Observed` as distinct types would have
   made several bugs here compile errors.

---

## How to work on this

**Check published sources before probing. This has now paid off four times** —
the chin bar protocol was in tuxedo-drivers `ite_8291_lb.c`, the TDP latch was
in tuxedo-drivers, the LPP protocol was in LibreLPP, and bleak's own source
explained a reconnect failure that three rounds of guessing did not. Sources
worth checking first: `uniwill-laptop/uniwill-acpi.c` (**our own vendored
driver — read the headers**), `tuxedo-drivers`, `qc71_laptop`, OpenRGB, the
DSDT in `kbctrl/dsdt.dsl`, and `hydroc-driver/` for the Windows app's own
config and strings (use `strings -el` — .NET stores UTF-16).

**The reference driver is NOT ahead of the vendored one on battery writes.**
`hydroc-driver/uniwill-acpi.c` routes `0x07B9` through the `SCHG` ACPI method —
but **`SCHG` exists in no DSDT on this machine** (0 hits in both `kbctrl/dsdt.dsl`
and `hydroc-driver/dsdt.dsl`; it lives only in the never-loaded FIXCGLM overlay,
`hydroc-driver/acpi_table_fix.asl` / `Misc/ssdt12.dsl`). The reference driver is
older upstream plus April-session patches calling a method that does not exist in
this firmware. The overlay's *shadow-register map* does not survive testing either:
`0x0497` and `0x04AB` are live-window and unwritable, `0x087F` rejects writes,
and `0x07CD` is the overlay's own readback slot. Its "ECRW is dead" premise is
disproven in both directions (see DESIGN.md §3.2). Also note the April tree names `0x07CC` `CHARGE_PRIO` while
newer upstream calls it `USB_C_POWER_PRIORITY` — that tree's register semantics
are local guesses and at least one is a mis-naming.

**Suspect the instrument before the hardware.** In one session: `0x07A5`
"arbitration" that was EC-busy from our own polling, an "EC override" that was
a write never landing, a fan stall caused by our reads, a "missing library" that
was a permission error, and a "cached address" that was not a reachable device.
Every one looked like a hardware finding. When something surprising happens, the
measurement apparatus is the first suspect, not the last.

**Verify hardware claims empirically, and say what was actually observed.**
"Connect succeeded and a log line printed" is not "the dock responded". The
transport is write-without-response; only a fan you can hear settles it.

**Change one thing at a time.** A fix and a workaround applied together taught
us nothing about which mattered — the `sw`-sent-twice theory is still unproven
because the UI's Apply button changed in the same step.

**Run the tests before and after.** Stdlib `unittest`, no dependencies, no
hardware access:

```bash
python3 -m unittest discover -s tests -t .
```

They pin the things that have already caused real bugs rather than chasing
coverage: the half-scale PL4 quantiser, renamed keys becoming phantom drift, the
LPP pump codes that are deliberately *not* in speed order, presets staying
applicable (an odd PL4 can never be matched after it is applied), and the two
rules that were learned the hard way — an unprivileged dependency check is
UNKNOWN and never FAILED, and a module is never unloaded without a file to load
back.

Every test was verified by mutation: break the behaviour it claims to protect
and it fails. A test that cannot fail is decoration. One trap when doing that
yourself — restoring a source file can leave stale `__pycache__` that shadows it,
so the suite goes on testing the mutated code. Run `python3 -B` and clear
`__pycache__`, or you will debug the harness instead of the code.

**Write the corrections down.** DESIGN.md carries several entries that say "this
was wrong and here is why". They are worth more than the entries that were right
first time.
