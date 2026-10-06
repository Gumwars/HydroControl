# Changelog

## [0.9.2] — 2026-10-06

Two features the Windows app has and this one did not, both read out of the
Windows service itself rather than guessed at.

### The panel's factory colour calibration

Each unit's panel was measured at the factory, and the result sits on
Uniwill's server. Control Center's Display -> Color Management -> "Restore" is
nothing more than fetching it. Decompiled from `GCUService.exe`:

```
GET http://iccprofile.uniwill.com.tw/api/iccprofile/<PANEL>_<SERIAL>
GET http://iccprofile.uniwill.com.tw/api/iccprofile/<PANEL>_<MAC>
```

`PANEL` is the EDID ID as Windows writes it (`BOE0B87` for the
NE160QDM-NZA), `SERIAL` the SMBIOS system serial, `MAC` the first wired NIC.
On the development machine it returned the real thing: *NE160QDM-NZA #1
2024-09-26, D6500, gamma 2.2*, a 20 KB ICC 2.2 profile with a calibration
curve and matrix.

- `sudo python3 -m hydroc.cli icc fetch` — or **Get factory profile** on the
  System page. Root, because `product_serial` is mode 0400. The reply is
  checked to be a well-formed ICC display profile before anything is stored;
  an error page served with a 200 never becomes a monitor profile.
- `python3 -m hydroc.cli icc apply` — as yourself. Adds it to every saved
  **hyprmoncfg** profile containing this panel, and to the live rule, with a
  `.hydroc-bak` of every file touched first.
- `icc import FILE` takes a profile you already have, e.g. the `.icm` from a
  Windows install.

**Why not just append the rule.** hyprmoncfg's daemon regenerates the live
monitor file on every display change — it did so twice during development —
so a hand-added line is gone at the next hotplug. Its saved profiles carry ICC
per output and are re-read at every reconciliation, so that is where it goes.
A layout that is not a saved profile is rebuilt from live Hyprland state,
which reports no ICC: save it with `hyprmoncfg save NAME` and apply again.

### Lock keys

Control Center's "Num Lock" switch is not firmware. It reads
`GetKeyState(VK_NUMLOCK)` and synthesises one keypress if the state is wrong.
Caps Lock has no setting over there, only an OSD.

Here: **Leave alone / On / Off** for Num Lock and Caps Lock on the System page,
or `sudo python3 -m hydroc.cli locks num on`. On or Off sets it now and
becomes the default at boot, resume and every login. A compositor starting up
resets each keyboard, so the daemon watches logind for new graphical sessions
and applies twice, a few seconds in — and never again, so a Num Lock you press
yourself is never fought.

The press goes into the **built-in keyboard's own evdev node**. Hyprland keeps
a lock state per keyboard, so a virtual uinput keyboard (what `ydotool`
creates) would toggle only its own. Lock state is not drift: pressing Num Lock
never raises the banner.

### Corrections

**The keyboard's Num Lock LED is not that keyboard's state under Hyprland.**
The first version of the lock-keys code verified against the LED, saw it lit,
and saved a default without pressing anything — while Hyprland held the
built-in's Num Lock off. Hyprland drives every keyboard's LEDs from whichever
keyboard last became active. State is now read from Hyprland's IPC socket
where it runs, and the LED only as a fallback.

That also explains a long-standing complaint that Num Lock "randomly turns
off". Logged for an hour: when a Bluetooth keyboard reconnected with its own
Num Lock off, the built-in's LED went dark while Hyprland reported the
built-in's Num Lock **on** throughout. Three config reloads did not touch it.
The light was reporting a different keyboard. Setting Hyprland's
`input.numlock_by_default = true` makes keyboards that reconnect come up
agreeing.

673 tests.

## [0.9.1] — 2026-10-04

One evening with a battery that was not defective, and most of what this
project believed about charging turned out to be wrong.

### The charge threshold works

It had been called inert since the second day. It is not. Set to 60, charging
stopped at 3.93 V/cell; raised back to 80, it restarted within one 30-second
sample.

**Why a month of testing missed it.** There are two limits and charging stops
at whichever comes first: this threshold, and a fixed hold at about 4.175 V
per cell against a 4.450 V rating. The default is 80, and at 80 the voltage
ceiling arrives first, so the threshold never bites. Every test used the
default. Every result was locally correct and generalised past its evidence.

The control is back in the UI, with the three things that make it usable: it
does nothing above roughly 75, the number set will not match where charging
stops, and the reported percentage keeps climbing after the current reaches
zero.

### The pack holds 77% of its rating, measured twice

```
discharged out   4865 mAh    98% -> 2%
charged in       4950 mAh    2%  -> termination
agreement        98.3%
```

Two integrations in opposite directions, same session. Against a 6400 mAh
design figure. The ~23% difference is the voltage derating, which is real
protection and costs real runtime.

### The last stretch of every charge is fabricated

Caught twice in one evening. At real termination the current goes to zero,
the voltage starts relaxing downward, and the reported charge keeps climbing
in exact multiples of 64 mAh — one percent of the design figure. 640 mAh of
it in ninety seconds, with nothing flowing.

`BAT_REMAIN_CAPACITY` in the EC holds the same invented number, so it is not
the kernel driver and not ACPI. And the reported figure walks straight
through the threshold setting without the charge resuming, which settles that
the EC compares against its own state of charge rather than the number in
sysfs.

### Shining a light on it

- **A live coulomb counter.** `current_now` is the only independent
  measurement on the machine. The daemon integrates it and shows it beside
  what the OS claims, with the ratio between them. It refuses to invent: no
  ratio from one sample, nothing counted across a gap over 300 s, clean reset
  on direction change.
- **A banner** that appears only while the seam is open, saying in plain words
  that the battery is not charging and the number is still going up.
- **A volts-per-cell bar** scaled 3.00 V to the pack's 4.450 V rating with the
  ceiling marked at 4.175, so the 19% of the track that can never be reached
  is the protection, drawn to scale. It explains itself under load, because
  terminal voltage is not open-circuit voltage.

### Corrections

- The derating is **not** adaptive to wear. A 0-cycle pack produced the same
  16800 mV as a 9%-worn one. The adaptive story came from having only ever
  had a worn pack.
- The pack never reaches 16800 mV. It regulates at ~16700, and the
  contradiction had been sitting one line from the claim for weeks because
  one number was the register and the other was the pack.
- 9% wear over two years is normal calendar aging, not fast. Cycles were the
  wrong denominator.
- Temperature at `0x0502:0x0503` is little-endian. Labelled the other way, it
  read 188 °C.
- `battery_watch.py` appended 13 columns under an 11-column header, found
  while it was collecting the decisive measurement.

### The 118 firmware update was analysed and declined

The vendor's release note gives the whole change: a microcode bump to 0x136.
This machine already runs 0x137 from `intel-ucode`. Across nine BIOS
releases there is no CVE, no vulnerability fix and no ME update. The EC's
only change is "Support copilot long press".

Declining also keeps the option — `lowest_supported_fw_version` stays at 105
rather than being pinned to 108.

### Still open

- Whether the discharge below 5% was necessary, or whether setting the
  threshold below the ceiling was always sufficient. Tonight was the first
  time either had happened, and they are confounded.
- Whether the charging mode and the threshold interact. Every measurement so
  far was taken on Stationary.
- What drives the 34 mA current quantum, which appears in every charge on
  three packs across two months.

## [0.9.0] — 2026-10-02

The first tagged release: 94 commits over five weeks, and the last known-good
point before EC `1.18.00` and the stock BIOS `N.1.11ELU08` replace the
firmware this was built against.

Not 1.0, for one reason above all others — **it has only ever run on one
machine**. The installer refuses anything else on purpose, because an EC
register map is specific to a chassis and writing HYDROC-16 addresses
elsewhere could set anything at all.

### What it controls

- **Fan curves.** 16-point CPU and GPU tables through the EC's universal
  fan control, with split per-fan handling. Populate, then enable — never
  the reverse, because the tables ship empty and the enable bit ships clear,
  and setting the bit first hands the fans a curve reading zero at every
  temperature.
- **The machine's own performance modes.** Office, Balanced and Beast, as
  the EC runs them, with fan boost. The EC holds each mode's power limits
  and thermal margin; selecting one hands the machine to it and releases the
  custom-profile latch.
- **Custom profiles.** PL1, PL2 and PL4 behind the `0x0727` bit 6 latch —
  power-limit writes are accepted and silently ignored while it is clear.
- **GPU mode.** Dynamic, dGPU-only and iGPU-only, written to two non-volatile
  EFI variables. Two gates and a pre-flight that refuses when the machine
  would plausibly come back without a desktop.
- **Battery.** Charge profiles, charge threshold, cycle count read from the
  EC because sysfs reports zero.
- **Keyboard and chin bar RGB.** Per-key colours, firmware effects, reactive
  effects via `0x0741` bit 3, white balance.
- **The profile button**, cycling either the machine's modes or your presets.
- A loopback web UI, systemd units for boot and resume, and a DKMS module.

### What the reverse engineering established

- **The battery protection is voltage derating, not a percentage ceiling.**
  The EC holds 16800 mV against a 17800 mV pack rating — 4.200 V/cell,
  250 mV below maximum, the strictest step it has. Windows with Control
  Center behaves identically. The percentage ceiling the UI implies is in the
  firmware (`bank2:0xC88C`) and gated off: `0x07C3` reads `13`, and the code
  arms at `4`.
- Eluktronics support independently confirmed the behaviour, and a second
  owner has reported it. Tongfang/Uniwill do not share EC internals with
  their vendors, so this repository is the only account of the mechanism
  outside Uniwill.
- **Our EC is bit-for-bit Tongfang's `GMxIXxx` 12L build** — `117.ELUK` sums
  to exactly the checksum the vendor's own release notes quote. Every
  disassembly here was done against the authentic image.
- **EC 1.18 changes one thing**: "Support copilot long press". No battery,
  thermal or fan item, and all 44 register addresses the daemon writes are
  referenced identically in both builds.
- **`SPLIT_TABLES` is accurately named.** `0x07C5` bit 7 is one of three
  conditions — with `0x0741` bit 0 and `0x07C6` bit 2 — gating a path that
  handles the two fans independently rather than as one.
- **This keyboard is panel type 7**, which Control Center sends raw. The
  colour correction table belongs to types 21/22; applying it is what turned
  white purple.
- **The stock BIOS contains the entire Intel overclocking menu tree**,
  suppressed rather than absent. Prema Mod unhides it and authors none of it.
  Varstore offsets for the per-core voltage offsets are extracted and
  documented.

### Corrections

Recorded because a project that only lists its successes is not reporting,
and because each of these was believed and acted on first:

- `0x0741` bit 0 is `ENABLE_MANUAL_CTRL`, not a charging enable — it is the
  kernel driver's master switch, documented in a header that was in the
  repository the whole time.
- **Every fan curve this project wrote had rise and fall swapped.** The test
  helper shared the inversion, so round-trips confirmed themselves. Three
  separate copies carried it; the last was found by an outside review.
- ACPI replies were truncated at the last null rather than the first, so the
  tail of a previous reply survived as part of the next. Nine probe scripts
  reintroduced the same bug after the package was fixed.
- The bank stride is `0x8000`, not `0x10000`. The error produced the same
  file offset with the bank number halved, which made three independent
  analyses agree that working code was unreachable.
- `shadow_probe` was built on an address recorded as unmapped a month
  earlier. It now refuses before touching anything.
- Keyboard colour correction was defaulted off on the strength of a test that
  had never run: the UI never sent the flag.
- The Prema BIOS *is* enforcing Secure Boot. The opposite was inferred from a
  file having no signature, which was never checked.

### Safety

- Every EC write is read back, because `ECRW` reports success for writes the
  EC ignores.
- 6 ms between EC accesses. Starving it stopped the fans at 0 rpm once.
- The fan tachometer registers are never read through ACPI.
- Fan curves are validated before writing: duty floor, monotonic duty and
  temperature, hysteresis, and no zero duty at temperature.
- An unreadable sysfs value is `None`, never `0` — 0 rpm and 0 mA are real
  readings here.
- The daemon refuses cross-origin POSTs. It runs as root and writes
  non-volatile firmware; loopback is not an access control.
- The installer refuses any chassis that is not a HYDROC-16, with a
  deliberate, visible override for a machine whose DMI has been cleared by a
  firmware flash.

### Infrastructure

- 600 tests, stdlib `unittest`, no hardware required.
- CI on Python 3.10 through 3.14, plus shellcheck and a job that refuses
  tracked vendor firmware — a 58 MB BIOS package once sat unignored in this
  tree because the pattern expected a three-digit version.
- Curve reads are cached, so a poll no longer spends two seconds re-reading a
  table that cannot have changed; mode switching writes only the bytes that
  differ, which took it from 15 seconds to about one.

### Known limitations

- **One machine.** No second HYDROC-16 has ever run this.
- The charge threshold control sets a register the EC stores and does not
  enforce. This is the hardware's behaviour, documented rather than hidden.
- Features Control Center has and this does not: boot animation, keyboard
  idle timeout, night mode, separate AC and battery brightness, backlight
  keys, battery-percentage and music-reactive effects.
- Fan boost is settable only while one of the machine's own modes is active.
