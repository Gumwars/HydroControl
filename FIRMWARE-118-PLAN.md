# EC 118.ELUK and the stock BIOS: what to do before, during and after

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

## After the flash

```
sudo systemctl stop hydroc-server.service       # before anything applies a profile
sudo python3 ec_state_capture.py --compare 117-preflash.json
sudo python3 kb_identity.py                     # Ver_High and KBID may both move
```

Then re-check, in this order: fan table contents, native mode read-back,
charge registers, `cat /proc/cpuinfo | grep microcode`, and the BIOS version
and date in DMI. Only start the daemon once the map is confirmed.

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
