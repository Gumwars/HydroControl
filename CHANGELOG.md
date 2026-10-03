# Changelog

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
