# HydroControl

A Linux replacement for the Eluktronics Control Center, for the **HYDROC-16 G1**
(i9-14900HX / RTX 4090). Fans, temperatures, CPU power limits, GPU cTGP,
battery charging policy, keyboard and chin bar RGB — in a browser UI backed by a
small privileged daemon.

> **Beta.** This writes to your laptop's embedded controller. Read
> [Safety](#safety) before you start. It is validated on **one** machine model
> and the installer refuses to run on anything else, by design.

---

## What works

| | |
|---|---|
| **Fans & temperatures** | live readings, CPU and GPU, with duty cycle |
| **CPU power limits** | PL1 / PL2 / PL4, verified accurate to the watt |
| **Performance presets** | Office · Balanced · Performance, cycled by the profile button |
| **GPU cTGP offset** | watts on top of base TGP |
| **Battery charging modes** | Stationary · Balanced · Long Haul |
| **Keyboard RGB** | 9 effects, per-key colour across all 101 keys, save to onboard flash |
| **Chin bar** | static, breathing, wave, clash, catchup — colour and speed where the effect supports it, restored at boot |
| **LPP cooling dock** | fan duty and pump mode over Bluetooth LE, re-applied on reconnect — verified |
| **Fan curves** | your own curve driven by the EC's own tables — CPU and GPU independently, with hysteresis |
| **Platform toggles** | Fn lock, Super key, touchpad hotkey, AC auto-boot, USB powershare |

**Not yet:** switching GPU mode from the app — the mechanism is understood (see
below) but it is a firmware write and a reboot, so it is being approached
carefully.

The profile button beside the power key cycles the presets, but only when the
BIOS has it set to performance modes rather than fan profiles. It works by
listening for the key event the firmware emits; the EC has no performance modes
of its own, so the presets are power-limit bundles and the profile LED stays
white throughout. See [DESIGN.md](DESIGN.md) §3.7.

---

## Requirements

- An **Eluktronics HYDROC-16 G1**. The installer checks DMI and stops otherwise.
- A recent kernel (≥ 6.10) with matching headers
- `base-devel`, `python3` ≥ 3.10
- `acpi_call-dkms` — how the daemon reaches the EC
- `pipx` + `ite8291r3-ctl` — for keyboard RGB. `./install.sh --check` flags it if
  missing; install it **as your own user, not root**. Versions **0.3 and 0.4**
  both work; `doctor` prints which one you have
- `bleak` — only if you own the **LPP cooling dock**. Optional; without it
  everything else works and the dock section simply does not appear

On CachyOS / Arch:

```bash
paru -S base-devel acpi_call-dkms python pipx linux-cachyos-headers
pipx install ite8291r3-ctl
```

---

## Install

```bash
git clone <repo> HydroControl && cd HydroControl
./install.sh --check      # verify prerequisites, change nothing
sudo ./install.sh         # build + install
```

The installer builds the patched `uniwill-laptop` kernel module (stock Linux has
no DMI entry for this laptop, so the driver never binds), sets it to load at
boot, and installs two systemd units that re-apply EC settings after every power
cycle and resume.

Then launch **HydroControl** from your application menu, or:

```bash
hydrocontrol
```

The desktop app is a window around the same UI. It starts the privileged daemon
for you through a normal authentication dialog, so there is no terminal to keep
open. If GTK4 / WebKitGTK are not installed the installer skips it and you can
still run the daemon directly and use a browser:

```bash
sudo python3 -m hydroc.server
```

and open **http://127.0.0.1:8781**.

It binds to loopback only — nothing is exposed to your network.

---

## Why it needs root, and why there's a daemon

Two things are unavoidable on this hardware:

**Root.** EC access goes through `/proc/acpi/call`, and the keyboard needs raw
USB. Neither is available unprivileged. The daemon holds root; the UI in your
browser does not.

**A daemon.** The EC keeps the charging profile, the custom-profile latch, and
the CPU power limits in **volatile memory**. They revert to factory defaults on
every power cycle. Windows solves this with a background service; so do we.
Without it, nothing you configure survives a reboot.

This is why the UI shows a drift banner after booting: it reads *actual hardware
state* rather than trusting a saved preference, and tells you when the two
disagree. **"Re-apply" pushes your saved settings back.**

---

## Something's wrong

**The app tells you.** When a check fails — most often after a kernel or system
update — a banner appears at the top of the UI naming what broke, why, and what
fixes it. If it is the kernel module, there is a **Repair** button that rebuilds
and reloads it in place. You should not need to know that a system update means
re-running the installer.

The same diagnosis is available from the terminal:

```bash
sudo python3 -m hydroc.cli doctor
```

### "hwmon is down" / most controls missing

The kernel module isn't loaded. Most often a **kernel update** — the module is
built against one kernel version and rejected by the next:

```bash
sudo ./install.sh --module
```

That rebuilds and reinstalls it — the **Repair** button in the UI does the same
thing. With DKMS this should not recur, since the module rebuilds itself on
kernel updates. If it keeps happening, `doctor` will report the driver as "NOT
DKMS-managed", and Repair registers it properly.

### CPU power sliders greyed out

Custom profile mode isn't armed. Toggle it at the top of **Performance** — EC
register `0x0727` bit 6. Without it, power-limit writes are accepted and
silently ignored by the firmware. The profile LED beside the power button turns
white when it's on.

### Keyboard RGB unavailable

`doctor` names the actual reason — read what it says beside the check rather
than guessing, because the three causes need different fixes:

```bash
sudo python3 -m hydroc.cli doctor
```

**"ite8291r3_ctl not installed"** — install it *as the user who runs the
daemon*. Under `sudo` the library is found via `SUDO_USER`'s home, so installing
it as root only will not help:

```bash
pipx install ite8291r3-ctl
```

**"ITE keyboard (048d:600b) not found on the USB bus"** — confirm the device is
actually there. If `lsusb` lists it and this still fails, that is a bug worth
reporting:

```bash
lsusb | grep -i 048d
```

**"...has no attribute 'usb_channel'" or "an API this build does not know how to
drive"** — `ite8291r3-ctl` changed its API between releases. 0.3 and 0.4 are both
supported; anything newer may not be. `doctor` prints the version beside the
keyboard check. Report it, and pin a known-good release meanwhile:

```bash
pipx install --force ite8291r3-ctl==0.4
```

**Colours apply but revert, or writes fail intermittently** — something else is
holding the device. One owner per device:

```bash
systemctl status kbctrld     # stop it if running
```

### The UI looks stale after an update

Hard-reload the page: `Ctrl+Shift+R`.

---

## Diagnostic scripts

Two standalone scripts ship alongside the app. Neither is needed for normal use
— they exist so you can answer hardware questions on *your* machine instead of
trusting what was observed on the development one. Both write only to the chin
bar, and the chin bar is volatile: a full power cycle clears anything they do.

### `lb_mode_probe.py` — which chin bar effects does your bar actually run?

```bash
sudo python3 lb_mode_probe.py
```

It runs each mode twice, red then blue, holding each for about four seconds, and
asks you two questions after each: did it **animate**, and did the **colour
change** between the two. At the end it prints a table to paste back.

This matters because the chin bar controller (ITE 8233, `048d:7001`) has no
published effect support — `tuxedo-drivers` implements static for it and returns
`-ENOSYS` for everything else. Every other mode in the UI was confirmed by
running exactly this probe. On the development machine:

```
  static     static,   colour follows palette
  breathing  animated, colour follows palette
  wave       animated, colour IGNORED
  clash      animated, colour follows palette
  catchup    animated, colour IGNORED
```

If your bar disagrees, that is a firmware difference worth reporting — it is the
single most useful thing a second machine can tell us about this device.

There is also a second pass for modes that misbehave, which re-tries them with a
different colour source and direction byte:

```bash
sudo python3 lb_mode_probe.py --variants
```

That is how Flash was ruled out (it switches the bar off at every source and
direction) and how Breathing was fixed — it only animates when driven from the
8-entry colour list, not from a single palette slot.

### `compat_probe.py` — is a different Uniwill laptop close enough?

The installer refuses to run on anything but a HYDROC-16 G1, and it should:
EC register layouts differ between chassis, and the same address that sets a
power limit here could mean something else on another board.

"Refuses to install" is not the same as "cannot work", though. Other
Uniwill/Tongfang machines share the EC and the ACPI accessors — the kernel
driver's own DMI table already covers TUXEDO, Schenker, Machenike and AiStone
boards. This gathers the evidence to decide, without writing anything.

On a working HYDROC-16, make a reference:

```bash
sudo python3 compat_probe.py --save hydroc16.json
```

On the other machine, compare:

```bash
sudo python3 compat_probe.py --compare hydroc16.json
```

It needs only `acpi_call` — not the kernel module, and not an install. It
**never writes**, and it skips the fan tachometer registers, which are the one
read known to stall this EC. It reports the DMI identity, whether the ACPI
accessors answer at all, `PROJECT_ID`, the EC ROM ID, and the capability bytes,
then says whether the layouts look like the same family.

A good result is a reason to investigate further and add the board to the DMI
allowlist deliberately. It is **not** a reason to bypass the guard.

### `lb_set_color.py` — set the chin bar from a script

A minimal one-shot setter with no dependencies on the rest of the app — useful
for confirming the bar responds at all without starting the daemon, and for
driving it from your own scripts:

```bash
sudo python3 lb_set_color.py FF8800          # solid colour
sudo python3 lb_set_color.py FF8800 40       # solid colour, brightness 40/100
sudo python3 lb_set_color.py --breathe 5 100 8CBF73   # speed, brightness, colour
sudo python3 lb_set_color.py --off
sudo python3 lb_set_color.py --test          # walk through colours interactively
```

---

## GPU mode — iGPU / dGPU / Dynamic

Set this in the **BIOS**, not in software. Three modes:

| Mode | What actually happens |
|---|---|
| **Dynamic** (default) | Both GPUs present. Panel on the Intel iGPU, the RTX renders and hands frames over. External outputs work. Best battery. |
| **dGPU only** | Panel routed to the RTX. **The Intel GPU disappears from the PCI bus entirely** — no PRIME, no Intel VAAPI, and the RTX drives your desktop at idle. Best latency and performance, worst battery. |
| **iGPU only** | RTX removed. **External displays stop working** — on this chassis they are wired to the discrete GPU. Longest battery life. |

**This is a real hardware switch, not a software preference.** It is worth being
clear about the difference, because it is easy to conflate: `envycontrol` and
similar tools change which GPU the graphics stack *renders* on. They cannot move
the display mux or remove a GPU from the bus, on this or any laptop. Selecting
iGPU-only here genuinely powers down the discrete GPU, which is why the battery
saving should be larger than any software-only approach — though nobody has yet
put a number on it, and it would be worth measuring rather than assuming.

**Where it lives.** The mode is a single byte, mirrored in two EFI variables, and
the BIOS applies it at POST:

| Mode | `UniWillVariable[0x62]` | `TpvSetup[0x01]` |
|---|---|---|
| iGPU only | `0x01` | `0x01` |
| dGPU only | `0x02` | `0x02` |
| Dynamic | `0x04` | `0x04` |

There is no ACPI method and no runtime switch — a reboot is required however you
set it, including from Windows, where Control Center writes these same variables
and asks you to restart. See [DESIGN.md](DESIGN.md) §7.7 for how this was
established (read-only, by diffing every EFI variable across the three modes).

**A caution if you go looking.** Unlike everything else this project touches, EFI
variables are **not** volatile. The "power cycle restores factory defaults" rule
that makes EC experimentation safe does not apply to them. The BIOS menu is the
supported way to change this.

---

## Safety

**EC settings are volatile.** Everything this writes lives in EC RAM and clears
on a full power cycle. If you get the machine into a state you don't like,
**shut down fully and boot again** — the EC returns to factory defaults. That is
a genuinely good safety property and worth remembering.

**The one exception is firmware variables.** GPU mode lives in EFI variables,
which are non-volatile — a power cycle will not undo a change there. Nothing in
this application writes them, and the BIOS menu is the supported way to change
GPU mode.

**The model guard is not decoration.** EC register layouts differ between
chassis. The same address that sets a power limit here could mean something else
entirely on another board. Don't bypass the DMI check.

**Power limits are bounded.** PL1/PL2 are clamped to 15–125 W and PL4 to 250 W.
Below about 25 W the machine is usable but sluggish; the UI warns you.

**Writes are verified.** Every EC write is read back and confirmed. The firmware
accepts some writes it then ignores — the daemon catches that and reports a
failure rather than claiming success.

---

## The LPP cooling dock

If you have the Eluktronics **LPP** (Liquid Propulsion Package) external liquid
cooler, HydroControl can drive its fan and pump. It is a Bluetooth LE device,
not part of the laptop, so it is handled by a separate small daemon and is
entirely optional — the UI shows a **Cooling dock** section only when that
daemon is running.

```bash
sudo pacman -S python-bleak                    # must be importable BY ROOT
sudo systemctl enable --now hydroc-lpp
```

Already using LibreLPP? Your settings migrate as a file copy — `hydroc-lpp`
reads its `state.json` format directly:

```bash
sudo install -Dm644 ~/.config/lpp/state.json /etc/hydroc/lpp.json
```

**One owner per device.** If you already run [LibreLPP](https://github.com/Sebastian-Alexis/LibreLPP),
stop it first — two processes cannot hold the same BLE connection:

```bash
systemctl --user disable --now lpp-daemon
```

**Disable it, do not just stop it.** If both are enabled they will race for the
BLE connection at every boot, and which one wins is a coin toss.

The dock forgets its settings when it loses power, the same way the EC does, so
`hydroc-lpp` saves what you set and re-sends it on every reconnect. Bluetooth
writes here are unacknowledged, so the values shown are what was *sent* — the
dock reports nothing back that we know how to read yet.

Not finding the dock? It must be powered on and in range. To target it by
address instead of by name:

```bash
bluetoothctl devices                  # find its MAC
sudo systemctl edit hydroc-lpp        # add: Environment=HYDROC_LPP_MAC=XX:XX:...
```

---

## Licence

**MIT** — see [LICENSE](LICENSE). Use it, fork it, ship it; keep the copyright
and permission notice with any substantial portion you redistribute.

One carve-out: **`uniwill-laptop/` is GPL-2.0-or-later** and cannot be
relicensed, because it derives from TUXEDO Computers' `tuxedo-drivers`. That is
a one-way boundary — MIT code may be used inside a GPL work, but nothing may be
copied out of `uniwill-laptop/` into `hydroc/` or `kbctrl/`.

`ite8291r3-ctl` and `acpi_call` are GPL-2.0 and are installed separately by you;
they are not redistributed here.

The LPP Bluetooth protocol was established by
[LibreLPP](https://github.com/Sebastian-Alexis/LibreLPP) (MIT, Copyright (c)
2024 sra). Our implementation is written against those protocol facts rather
than copied from that code, but the knowledge is theirs — see
[DESIGN.md](DESIGN.md) §3.5.

Hardware details throughout — register addresses, packet layouts, bit
assignments — are facts about the machine and carry no licence. Where they came
from a published driver, that driver is credited in [DESIGN.md](DESIGN.md).

---

## Feedback

Useful things to include:

```bash
sudo python3 -m hydroc.cli doctor
sudo python3 -m hydroc.cli state --json
uname -r
```

Plus what you expected versus what happened. If a control did nothing, the
server's terminal output matters — it logs the per-setting result of every
apply. For anything involving the chin bar, include the table from
`lb_mode_probe.py` (see [Diagnostic scripts](#diagnostic-scripts)).

Particularly interested in:

- Whether the **charging modes** do anything observable. On the development
  machine none of them visibly caps charging, and we don't know why. Long-run
  battery observations are genuinely valuable.
- Whether **PL2/PL4** behave as expected under sustained load.
- Anything that differs from the development machine — same model, but firmware
  revisions vary.
