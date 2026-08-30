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
  presets.py       Office / Balanced / Performance, matched against live state
  hotkeys.py       KEY_F14 listener — the physical profile button
  rgb.py           visual key id -> matrix bridge; chin bar modes
  lpp.py           LPP dock protocol (pure, no transport)
  lppd.py          LPP sidecar daemon — BLE, Unix socket at /run/hydroc/lpp.sock
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

**Fan control — the biggest open item.** Registers are identified and confirmed:

```
0x0751  MANUAL_FAN_CTRL   b2:0 level, b4 TURBO, b5 HIGH, b6 BOOST, b7 USER
0x075B/C  PWM_1/2         0..200 (PWM_MAX), hwmon rescales to 0..255
0x07C5  UNIVERSAL_FAN_CTRL  b7 SPLIT_TABLES
0x07C6  AP_OEM_6            b2 ENABLE_UNIVERSAL_FAN_CTRL
0x0F00/10/20  CPU DownT / UpT / Duty   16 points each
0x0F30/40/50  GPU DownT / UpT / Duty
0x1804/0x1809  PWM_*_WRITEABLE — driver says "unstable, WMI interface only"
```

`FAN_MODE_BOOST` works (forces 200). Setting duty directly does not. The
universal-control path (`0x07C6` b2 + tables) accepts writes with no observed
effect — **but that experiment ran while `0x0741` bit 0 was clear, so it must be
repeated before anything is concluded from it.** Manual duty is most likely a
WMI method, not a register write.

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

**The charge threshold does not hold — write-path tested, clobbering still open.**
Setting 80% stops charging briefly, then it resumes. `charge_path_probe.py`
(2026-08-27) A/B-tested the two write paths live: plain ECRW writes to `0x07B9`
and `0x07A6` **land and hold** (the FIXCGLM overlay's "ECRW is dead" premise is
disproven on this EC), and the SCHG shadow set (`0x07CD`, `0x0497`, `0x087F`,
`0x04AB`) is partially WKBC-writable — `0x07CD` and `0x0497` land, `0x087F`
never moved, `0x04AB` is EC-managed. At baseline `0x07CD` held **90%** while the
real threshold was **85%**: the app writes only `0x07B9`, never the shadow set.
The "stops then resumes" behaviour is consistent with the EC checking the shadow
(0x07CD=90) or clobbering `0x07B9` from it when it sets `CHARGE_CTRL_REACHED`.
The clobbering itself is still unproven — it needs a charge cycle with
`charge_ctrl_watch.py` running. Fix direction: write the full SCHG set together.
See DESIGN.md §3.2. **Timeline resolved:** the probe's 85% baseline was the boot
apply (06:16:40 logged `100 -> 85`), and the register reading 80 now is the
user's own post-probe change (profile mtime 12:16:30) — no unattended drift,
and the user-set 80 has held via the ECRW path since. **Process gap:** the probe
run had no saved artifact; re-run with output saved before drawing further
conclusions.

**The 30% shutdown.** Machine powers off at ~30% reported charge at idle.
Discriminator: cell voltage at cutoff (~3.6 V/cell = real reserve; ~3.0–3.2 =
gauge reads high). `battery_watch.py` flushes per sample.

**GPU MUX.** `card2-eDP-2` exists on the NVIDIA card (panel is wireable to the
dGPU) and Control Center has `DgpuOnly`, so a MUX exists. envycontrol cannot
flip it — that's a driver-level tool, not a mux switch. Check the BIOS first;
a wrong MUX write leaves no display, making it the highest-stakes write here.

---

## Next

1. **Re-run the universal fan control experiment** with `0x0741` bit 0 confirmed
   set. The previous null result is void.
2. **Fan curves**: if universal control works, a userspace controller
   (read temp → interpolate → write `0x0F20`/`0x0F50`) with a duty floor and a
   temperature abort. Derive curves from `fan_characterise*.py` measurements,
   not from vendor JSON.
3. **Charge threshold**: write the full SCHG set (`0x07B9` + `0x07A6` +
   `0x0497` + `0x087F` + `0x07CD`) together, via WKBC, in the app's
   charge-threshold path — the shadow set is currently never written and was
   found out of sync (0x07CD=90 vs 0x07B9=85). Note `SCHG` itself does not
   exist in this firmware's DSDT; the sequence must be replicated via WKBC
   (see `hydroc-driver/acpi_table_fix.asl`). Then run a charge cycle with
   `charge_ctrl_watch.py` to settle whether the EC clobbers `0x07B9` at the
   stop point. See DESIGN.md §3.2.
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
this firmware. The overlay's *shadow-register map* (`0x07CD`, `0x0497`, `0x087F`,
`0x04AB`) is real and worth keeping — its "ECRW is dead" premise is disproven
(see DESIGN.md §3.2). Also note the April tree names `0x07CC` `CHARGE_PRIO` while
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
