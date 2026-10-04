# EC 118.ELUK and the stock BIOS: analysed, and **not flashed**

> **Decision, 2026-10-04: not taking this update.** The reasoning is at
> the bottom, under "Why this was declined". Everything between here and
> there is the pre-flash analysis, kept because it is correct and because
> a future release may be worth taking.

Eluktronics is supplying a BIOS and EC `118.ELUK`, replacing the Prema Mod
`N.1.09ELUK` (dated 2024-05-04) and EC `117.ELUK`. This is the vendor's own
firmware for this exact machine, which is the one case where flashing is
appropriate — unlike the XMG image, which is for different hardware and would
brick this one.

## The microcode argument, accurately

The OS already runs **0x137** (`intel-ucode 20260925`), loaded from
`intel-ucode.img` in early boot. Intel's Vmin-shift mitigation for Raptor Lake
landed at 0x12B in September 2024, so the microcode itself is long since in
place and the urgency is lower than "protect the CPU" suggests.

What the OS cannot reach is the BIOS side: voltage and power behaviour set
before any microcode the initramfs loads, and settings a modded BIOS may hold
open. **The installed BIOS predates Intel's entire response to the issue.** On
a 14900HX that is the real argument for taking 118, and it means losing
Prema's performance tweaks is closer to the point of the update than a cost of
it.

## Before the flash — while still on 117

Once flashed, 117's behaviour cannot be re-measured. Everything here is
read-only.

```
sudo python3 ec_state_capture.py -o 117-preflash.json
sudo python3 ec_dump.py            -o 117-preflash-dump.txt
sudo python3 baseline_capture.py   -o 117-preflash-rest.txt
# and again on AC, mid-charge, because the derating only computes while charging
sudo python3 baseline_capture.py   -o 117-preflash-charging.txt
sudo python3 efivar_capture.py     -o 117-preflash-efivars.txt
```

Not capturable by script, and gone after the flash:

- **Photograph the Prema BIOS menus.** Every tab. The settings are
  unrecoverable once the menus are gone, and we have no record of what is set.
- **The profile-button BIOS option** ("performance modes" vs "fan profiles").
  `server.py` depends on it emitting an event. If that option is Prema's
  rather than stock, the button may go quiet on 118.

## A second variable arrived: capture the new battery on 117 first

The RMA pack was fitted on 2026-10-02 and reads **0 cycles, 0.0% wear,
6400/6400 mAh**. The flash is the next day. That is two variables changing
inside 24 hours, and `117-preflash.json` was taken with the old 9%-worn pack
— so without another capture, any post-flash difference in the charge
registers cannot be attributed to the firmware or the battery.

Three points instead of two:

| capture | firmware | battery | isolates |
|---|---|---|---|
| `117-preflash.json` | 1.17 | 9% worn | (committed) |
| **`117-newbatt-*.json`** | 1.17 | **0% worn** | **the battery** |
| `118-postflash.json` | 1.18 | 0% worn | the firmware |

### And it is the control this project has been missing

The conclusion on record is that the EC's protection is *adaptive*: it holds
16800 mV against a 17800 mV rating — 250 mV/cell, the strictest of the
250/200/150/100/50 steps — **because** the pack is worn. That has never been
tested against an unworn pack, because there wasn't one.

`0x0522:0x0523` now answers it directly:

| reads | meaning |
|---|---|
| `16800` (`A0 41`) | the derating is **fixed**, not wear-adaptive. The explanation on record is wrong. |
| `17600` / `17400` / … | adaptive confirmed, and the step is readable |

**It must be captured while charging.** The derating only computes with
`0x0490` bit 0 set, so a resting `0x0522:0x0523` may be stale. The pack is at
69% against an 80% threshold, so plugging in gives a charging window.

```
sudo python3 ec_state_capture.py -o 117-newbatt-rest.json
# then on AC, while it is actually charging:
sudo python3 ec_state_capture.py -o 117-newbatt-charging.json
sudo python3 baseline_capture.py  -o 117-newbatt-charging.txt
```

## Do not enroll Secure Boot keys first

A BIOS flash normally clears NVRAM, which wipes PK/KEK/db again and returns
the machine to Setup Mode. Doing the `sbctl` enrollment before the flash is
wasted work. The boot entry survives regardless: `Boot0001` points at
`\EFI\BOOT\BOOTX64.EFI`, the removable fallback path, which firmware uses
without an NVRAM entry.

Enroll after the flash, from the runbook in the Secure Boot notes.

## Analyse 118.ELUK before flashing it

This is the part worth doing properly, and it needs no hardware. We hold
`117.ELUK` and the G2's `125.ELUK`, and `ec_image_scan.py` and `ec_disasm.py`
already work on both.

**The risk is the register map.** The daemon writes 44 hardcoded addresses. If
118 moves any of them, HydroControl writes to the wrong place on firmware it
has never been tested against. In rough order of consequence:

| addresses | what | if it moved |
|---|---|---|
| `0x0F00`–`0x0F5F` | fan tables | a curve written into unrelated registers |
| `0x0751` | performance mode | mode writes land somewhere unknown |
| `0x0727` bit 6 | custom-profile latch | limits silently ignored, or worse |
| `0x0730`–`0x0737`, `0x07A7`–`0x07A9`, `0x07D8`–`0x07DA` | per-mode EC limits | wrong limits applied |
| `0x0741` | `ENABLE_MANUAL_CTRL` / reactive | shared with the kernel driver |
| `0x07B9`, `0x07A6`, `0x078E` | charge threshold and profile | the open question |
| `0x073C`, `0x0742` | KBID and panel type | wrong RGB table selection |

Do the static diff against 117 and confirm each one before trusting the daemon
on 118. **Stop the daemon before first boot on new firmware** rather than
letting it apply a saved profile into an unverified map.

## After the flash — runbook

The machine comes back with Secure Boot off and in Setup Mode, because the
flash clears NVRAM. That is fine for booting: Limine only enforces its config
checksum when Secure Boot is *active*, so the first boot is unconditional.

**1. Boot, and keep the daemon out of it.** It starts on boot and would apply
the saved profile into a register map nothing has verified yet.

```
sudo systemctl stop hydroc-server.service
sudo systemctl disable --now hydroc-server.service    # survives the next reboot too
```

**2. Diff the EC against the baseline.**

```
sudo python3 ec_state_capture.py --compare 117-preflash.json
```

What matters: the fan tables at `0x0F00`–`0x0F5F` still 16 points each, the
per-mode limits unchanged (office PL1 45 / TCC 15, beast PL1 205 / TCC 5),
`0x07C3` still `13`, and `0x0522:0x0523` still `160,65` = 16800 mV. Values
that *should* move: `0x0751` reflects whatever mode the EC boots in, and the
cycle count at `0x04A6`.

**3. Check identity, and re-stamp DMI if needed.**

```
cat /sys/class/dmi/id/{bios_vendor,bios_version,board_name,product_name,product_sku}
hydroc doctor
```

`bios_vendor` should become American Megatrends and `bios_version`
`N.1.11ELU08`. If `board_name` comes back blank or as a Tongfang code, the
guard now accepts `product_name` or `product_sku` instead — and if all three
are empty, `HYDROC_ASSUME_SUPPORTED=1` gets past it while you re-stamp with
`AMIDEWINx64.EXE` from Windows, using `~/dmi-preflash.txt`.

**4. Restore Secure Boot.** The sbctl keys and every file signature live on
disk and survive the flash; only the enrollment is lost. One command:

```
sudo sbctl enroll-keys --microsoft --firmware-builtin=db,KEK
sudo sbctl verify          # expect all green, including BOOTX64.EFI
```

Then enable Secure Boot in the firmware menu. `--microsoft` is not optional
here: the RTX 4090's option ROM is Microsoft-signed.

Limine's config checksum is baked into `BOOTX64.EFI` on the ESP, which the
flash does not touch, and all 43 path lines in `limine.conf` already carry
BLAKE2B hashes — so enforcement coming back on is safe.

**5. Only then start the daemon.**

```
sudo systemctl enable --now hydroc-server.service
sudo python3 kb_identity.py      # Ver_High and KBID could both move
```

Also worth a look: `grep microcode /proc/cpuinfo` (the OS loads 0x137
regardless, so this is a curiosity rather than a check), and whether the
profile button still emits events — it depends on a BIOS setting that may not
exist outside the Prema menus.

## Ask Eluktronics while you have them

The charge-ceiling behaviour is the open question of this project, and there is
a live human on the other end:

1. Does 118 change battery charge-limit or charge-threshold behaviour?
2. Is the EC's adaptive derating (16800 mV against a 17800 mV rating on a 9%-worn
   pack) intended, or a symptom they would want reported?
3. Does the stock BIOS keep the profile-button setting that selects performance
   modes rather than fan profiles?

## Do not commit the firmware

`*.ELUK` and `*_EC[0-9][0-9][0-9].zip` are gitignored. They are Eluktronics'
copyrighted material and this is a public repository.

---

# Results of the static analysis (2026-10-01)

Package: `intel_RPL_R_GMxIXxB_xN_BIOS_N.1.11ELU08_EC_1.18.00_20260918`.
BIOS **N.1.11ELU08**, EC **1.18.00**. Nothing has been flashed.

## The EC image is verifiably ours

The archive ships the EC as `EC/GMxIXxx_12L_1.18.00/GMxIXxx_11.800`, not as a
`.ELUK` file — Eluktronics renames Tongfang's image. The vendor readme quotes a
checksum per release, and it is a plain byte sum:

| image | bytes | byte sum | readme says |
|---|---|---|---|
| our installed `117.ELUK` | 262144 | `0x21788A7` | `GMxIXxx_11.700` = `0x21788A7` ✓ |
| new `GMxIXxx_11.800` | 262144 | `0x2178461` | `GMxIXxx_11.800` = `0x2178461` ✓ |
| G2 `125.ELUK` | 262144 | `0x1F2353A` | (different family) |

Two things follow. The new file is intact and genuine. And **our installed EC
is bit-for-bit Tongfang's `GMxIXxx` 12L build** — which retroactively confirms
that every disassembly in this repository was done against the authentic
vendor image, not a repackaged derivative.

## 1.18 changes nothing we care about

The readme is the full release history. In its entirety:

```
2025/5/27   EC Version 1.18.00     [Change item]: 1.Support copilot long press
2024/8/21   EC Version 1.17.00     [Change item]: 1.Modify MCJ USB power
```

No battery, charge, thermal or performance item. The fan tables cite the
*same* source documents in both releases (`R04_20240401`, MCJ `R07`,
`GM6IX9B R03_20240105`), so fan semantics are unchanged by the vendor's own
account.

**This answers the question we were going to put to Eluktronics.** 118 does
not change charge behaviour. The mechanism this project documented is still
the current one.

## ROMID: vendor-confirmed, and the "dead" branch named

The readme lists the ROMID each OEM is stamped with:

| ROMID byte 0 | OEM |
|---|---|
| `0xFF` | **STD (default)** |
| `0x04` | MCJ |
| `0x08` | Thirdwave |
| `0x09` | Monster German, DreamMachine |
| `0x0C` | XMG German |

Two corrections to the record:

- `0x0770` reading `0xFF` is the **STD ROMID**, documented by the vendor. The
  reading of it as a mode flag was wrong; `ROMID_START` was right.
- `CHARGE-CEILING-TRIGGER-CHAIN.md` calls the `0x0770 == 4` branch dead. It is
  the **MCJ** variant — dead on this machine because we are STD, live on an
  MCJ unit. Not dead code, OEM-conditional code.

It also shows why the EC image is shared across OEMs while the BIOS is not:
one `GMxIXxx` build serves XMG, Monster, Thirdwave and the rest, branching on
ROMID. That does not make their *BIOS* images interchangeable, and the refusal
to cross-flash the XMG BIOS stands.

## The register map survives; the code map does not transfer

Every one of the 44 EC addresses the daemon touches, searched as
`MOV DPTR,#addr` (`90 hi lo`):

```
44 addresses checked, 1.17 -> 1.18
  identical reference count : 44
  reference count changed   :  0
  disappeared in 1.18       :  0
```

Including the fan tables, `0x0751`, the latch at `0x0727`, and all the
per-mode limits. **HydroControl's register dependencies look intact.**

The image itself is another matter: 27909 of 262144 bytes differ (10.6%),
concentrated in the upper halves of banks 1 and 2.

```
bank1:0xC000  7795    bank2:0xC000  7851
bank1:0xE000  3156    bank2:0xE000  4419
bank1:0xA000  1858    bank2:0xA000  2693
```

`bank2:0xC000` is where the charge-ceiling chain lives, and none of the four
traced entry points (`0xC88C`, `0xC86D`, `0xC81E`, `0xC7F9`) has a
byte-identical 24-byte signature anywhere in 1.18.

**That is not evidence the logic changed.** Recompilation moves every absolute
address embedded in `90/02/12 hi lo` operands, so any window containing a
reference differs even when the source is identical — and a one-item changelog
plus 44 unchanged register counts both argue for churn. What it does mean is
that the *code* addresses in our notes are specific to 1.17 and would have to
be re-derived against 1.18. Nothing in the daemon depends on them.

## Still unverified: the BIOS

The capsule `.inf` targets ESRT firmware GUID
`{72047706-dd0b-5a80-94f1-510302d18b7a}`, version `108`. This machine exposes
seven ESRT entries but their attributes need root:

```
sudo sh -c 'for e in /sys/firmware/efi/esrt/entries/entry*; do echo "--- $e"; \
  for f in fw_class fw_version lowest_supported_fw_version; do \
    printf "  %-28s %s\n" "$f" "$(cat $e/$f 2>/dev/null)"; done; done'
```

A matching `fw_class` is proof the capsule is for this board. Prema rewrote
`board_name` to "HYDROC-16 powered by premamod.com", so DMI cannot answer it —
and the archive covers three families (IDX `GM6IX8X/9X`, IDN `GM7IX8N/9N`,
IDB `GM6IX9B`) with DMI re-stamping tools for two of them, which is itself a
hint that DMI needs restoring after the flash.

## Recommendation

**Take the BIOS, and do not bother with the EC.** The EC update buys one
Copilot-key behaviour, and spends the only firmware baseline this project has
ever verified against. There is no charge, fan or performance change in it.

Two things to confirm with Eluktronics before flashing:

1. Does BIOS `N.1.11ELU08` require EC 1.18, or is it supported against the
   installed 1.17? Vendors usually ship them as a pair and may not qualify a
   split.
2. Confirm the ESRT GUID above matches HYDROC-16 G1, and ask which DMI tool
   set to re-stamp with afterwards, given Prema overwrote the board name.

## The undervolt: measure it before, and it may be restorable after

Prema exposes dedicated undervolt pages in its setup menu, defaulting to
**-40 mV on the performance cores**. That is a BIOS setting, not something
compiled in, and losing the menu is not the same as losing the capability.

Undervolting is gated on whether the part is overclockable, not disabled
outright. Plundervolt (CVE-2019-11157) led Intel to close the OC mailbox
(`MSR 0x150`) on locked parts; it stays functional on unlocked ones, and the
i9-14900HX is an HX-series flagship, which is overclockable. So the interface
should survive the flash even if the stock Eluktronics menu does not expose a
page for it.

This machine is set up to reach it:

```
msr driver        builtin      (no module to load)
kernel lockdown   [none]       (MSR writes permitted, even with Secure Boot active)
intel-undervolt   extra/1.7-3  (packaged, not installed)
msr-tools         extra/1.3-4  (packaged, not installed)
```

Some distributions force `lockdown=integrity` when Secure Boot is active,
which blocks MSR writes outright. CachyOS does not, which is why this is worth
trying at all.

**But `intel-undervolt` is not the same instrument.** Measured on this machine
with Prema's -40 mV configured, it reports zero on every plane:

```
CPU (0) -0.00 mV   GPU (1) -0.00 mV   CPU Cache (2) -0.00 mV
System Agent (3) -0.00 mV   Analog I/O (4) -0.00 mV
rdmsr -f 28:28 0xCE  ->  1
```

Bit 28 confirms an unlocked part and all five domains answered, so the mailbox
is reachable. The zeros are not a failed read and not a reset setting: the OC
mailbox exposes five **package-level** domains, while Prema's setup pages set
offsets **per P-core and per E-core** individually. Those are different
interfaces. The tool is reading a register that really is zero while the
per-core offsets sit somewhere it never looks.

Two consequences.

**Do not probe it by writing.** A domain-0 offset may sum with the per-core
offsets rather than replace them, which would stack a test value on top of the
-40 mV already in effect. Which of those the silicon does is not established
here, and a 14900HX that may have taken some Vmin shift is the wrong place to
find out by experiment. The BIOS page is the authority on what is applied;
Linux has no reliable per-core reader.

**What survives the flash is coarser than what is being lost.** If the stock
BIOS exposes no undervolt page, `intel-undervolt` can still set a global core
offset, because the mailbox stays open on unlocked parts. That approximates
Prema's arrangement but is not equivalent — per-core offsets let the stronger
cores take a deeper cut than the weakest one tolerates, and a single global
value is bounded by the worst core. Treat it as a replacement with real, if
modest, loss rather than a like-for-like restore.

If it is re-applied globally, set the CPU and cache planes together — they are
linked on Intel — and validate under sustained load rather than trusting -40
because it was the old default. The 0x12B-and-later microcode already lowers
the voltage the CPU requests of itself, so the offset is being subtracted from
an already-reduced target.

## Prema unhid pages; it did not write them

Confirmed from the stock image, before flashing. The 32 MB ROM holds four LZMA
streams (17 MB decompressed); the setup volume carries 12890 UTF-16LE strings,
and among them:

```
Core Voltage Offset            Offset Prefix               Core Voltage Mode
Cluster 0/1/2/3 Voltage Offset P-core Voltage Override     Uncore Voltage Offset
VF Point 1..13 Offset Prefix   E-core L2 Voltage Mode      Ring Voltage Mode
Per Core Ratio Override        Core Ratio Extension Mode   CEP Disable
Overclocking Lock (BIT 20 in FLEX_RATIO MSR)
```

So the entire Intel overclocking and undervolting menu tree ships in the stock
Eluktronics BIOS. Prema authored none of it — the pages are suppressed by the
OEM and the mod exposes them. That is the standard shape of these mods: patch
the AMI IFR to drop `suppress-if` on forms the vendor hid.

The stock tree is in fact **richer** than the -40 mV default suggests:
per-cluster offsets, a full 13-point VF curve, a P-core override and a CEP
disable menu are all present.

### The gate has a name, and its help text is explicit

```
UnderVolt Protection
  "When UnderVolt Protection is enabled, user will not be able to program
   under voltage in OS runtime. Recommended to keep it enabled by default."
```

That is almost certainly why `intel-undervolt` read `-0.00 mV` on every plane
while `0xCE` bit 28 reported an unlocked part: reads answer, OS-runtime
*programming* is refused, and the BIOS applies its own per-core offsets by a
path that does not show up in the package-level mailbox. It also means a write
test would have been rejected or stacked — a second reason that test was right
to withdraw.

### Getting them back without reflashing

Hidden questions still read their values from the AMI varstores (`Setup`,
`CpuSetup`, and friends), so the settings are reachable without modifying the
BIOS at all: write the varstore offset directly with `setup_var.efi` from an
EFI shell. Most AMI setup variables are NV+BS without a runtime attribute, so
Linux cannot see them at runtime — this is a pre-boot operation.

What that needs is the **exact varstore and offset per question**, which means
extracting the IFR from this image rather than guessing. A wrong offset writes
an unrelated setting, and some of the neighbours here are CEP and the
overclocking lock. Extract first.

First, though: boot the stock BIOS and look. Eluktronics may leave some of
these pages visible, in which case none of this is necessary.

## BIOS verified, and it is a one-way trip

ESRT `entry0` on this machine:

```
fw_class                     72047706-dd0b-5a80-94f1-510302d18b7a
fw_version                   105
lowest_supported_fw_version  105
```

The `fw_class` is an **exact match** for the GUID in the capsule's `.inf`, so
the BIOS is for this board. That was the last open safety question and it is
answered. The capsule is version `108`, above the current `105`, so the update
is permitted.

**`lowest_supported_fw_version` equals the current version.** The firmware
already refuses any capsule older than what is installed, so downgrades are
blocked as policy, not as an accident. After flashing, that floor becomes
`108` — and **Prema's `N.1.09ELUK` can never be capsule-flashed back.** We do
not hold a Prema image, and the package contains only stock ROMs. Losing the
Prema tuning is not reversible; it is permanent.

Note that DMI and ESRT disagree about the current version: DMI says
`N.1.09ELUK`, ESRT says `105`. The capsule gate compares against ESRT, so the
`105 -> 108` comparison is the one that governs. The likeliest reading is that
Prema changed the DMI strings and left the ESRT version at its stock base.

### Before starting, ask for the recovery procedure

The package ships `BIOS/GM6IX8B/FlashUtil/Recovery_rom/GMxIX9x.BIN` and does
not document how to invoke recovery — the filename and key combination are
model-specific. That is the one piece of information which is useless to
obtain after it is needed. Ask Eluktronics for it while the conversation is
still open, and put the recovery image on a FAT32 USB stick first.

### Which flash path

The vendor SOP (`OemFirmwareUpdateSOP.txt`) is the Windows capsule route, and
step 1 — disable Secure Boot — is **not** already satisfied. Enabling Secure
Boot in the firmware menu caused it to enroll its own factory defaults from
`PKDefault`/`KEKDefault`/`dbDefault` and leave Setup Mode. As of 2026-10-01
this machine reads:

```
SecureBoot 1   SetupMode 0   PK/KEK/db populated   dbx absent
bootctl: Secure Boot: enabled (user)
```

So Secure Boot must be turned off before the flash.

**The enrolled keys are the factory set, not the local sbctl keys.** Every
signature `sbctl verify` reports as good was made with a key the firmware does
not currently trust, so the sbctl work is untouched rather than done.

`sbctl verify` also reports `/boot/EFI/BOOT/BOOTX64.EFI` as "not signed",
which means *not signed with sbctl's key* — not unsigned. The PE security
directory is present (2104 bytes at `0x4e000`), and the machine booted Limine
under active Secure Boot, so that signature chains to something in the factory
`db`. The Prema BIOS is enforcing; an earlier note here guessed it was not,
on the assumption the file had no signature at all.

Which signer that is decides how much care the flash needs:

- **Signed via a certificate in the factory `db`** (Microsoft UEFI CA being
  the likely one) — the stock BIOS restores the same defaults, so Secure Boot
  can be re-enabled after the flash without signing anything.
- **Signed by anything else** — re-enabling Secure Boot after the flash could
  leave the machine unbootable, and Limine has to be signed with a trusted key
  first.

Either way the flash itself needs Secure Boot off, and the flash clears NVRAM
and returns the machine to Setup Mode — which is the right moment to run the
`sbctl` sequence properly rather than relying on whatever the firmware
enrolls by itself.

The SOP's own path installs a certificate and enables testsigning to load an
unsigned driver package, which is more ceremony than needed.
`FlashUtil/AfuWin64/GMxIX9xN111ELU08.EXE` does the same job directly, and
`AfuEfi64/` does it from an EFI shell with no Windows at all. The Windows
drive is the pragmatic choice regardless, because `AMIDEWINx64.EXE` is needed
afterwards to re-stamp DMI and exists only for Windows.


## Why this was declined (2026-10-04)

The vendor's own `BIOS_Release_Note(Intel).docx` settles it. Nine BIOS
versions, and the complete history for this one:

```
N.1.11ELU08   2026-09-18   microcode 0x136   "Sync std code: Updated Microcode to m_32_b0671_00000136.pdb"
N.1.10ELU07   2024-11-18   microcode 0x120   "update aistone secure boot key"
N.1.10ELU06   2024-08-28   microcode 0x120   "sync std and update ec to 1.17"
N.1.09ELU05   2024-04-02   microcode 0x120   "Wifi 6E support EU+USA"
```

**The update's entire content is a microcode bump to 0x136. This machine
already runs 0x137**, loaded by `intel-ucode` at boot. The firmware is one
revision behind what the OS supplies.

Across all nine releases the document contains no CVE, no vulnerability fix,
no Boot Guard change, and no ME update — ME stays at 16.1.30.2307v4 and the
Source Control Label is identical between N.1.10 and N.1.11. There is no
security content to gain.

The EC is a horizontal move: 1.18's only change item is "Support copilot long
press", and all 44 register addresses this project writes are referenced
identically in both builds.

So the trade was a non-zero bricking risk, the loss of Prema's per-core
undervolt pages, a DMI re-stamp, a Secure Boot re-enrollment and an
unverified register map — in exchange for a microcode already superseded.

**Not flashing also preserves the option.** `lowest_supported_fw_version` is
105; flashing sets the floor to 108 permanently. Declining keeps 108
available for whenever there is a reason to take it.

### What would change the answer

A Prema build based on N.1.11ELU08 — the microcode sync with the setup pages
still unhidden — would be strictly better than either current option, and
worth taking. `BIOS-SETUP-OFFSETS.md` and `ifr_extract.py` are the
preparation for that, and remain valid against whatever image arrives.

A future stock release with an actual fix in it would also qualify. This is
not that release.

### One honest footnote

Prema's base is N.1.09-era, so the *firmware-level* microcode here is
almost certainly 0x120 — pre-mitigation. Anything running before the kernel
loads `intel-ucode` sees it: POST, a UEFI shell, memtest, a live USB without
the package. That is seconds at idle load against a degradation mechanism
driven by sustained voltage under load, so it is negligible. Worth knowing,
not worth acting on.

### Distribution

Eluktronics supplied this package directly and has not posted it to the
support site. It is not ours to redistribute. The archive and every extracted
artefact stay out of this repository, and the CI job that refuses tracked
firmware stays as the thing that notices if that ever slips.
