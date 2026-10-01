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
