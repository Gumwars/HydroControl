# kbctrl — DankMaterialShell plugin

Bar widget that puts the kbctrl RGB controls on DankBar: effects, per-key
colors, and the lightbar, backed by the `kbctrld` daemon.

## Install

```
ln -sf /home/gumwars/kbctrl/dms-plugin/kbctrl ~/.config/DankMaterialShell/plugins/kbctrl
```

Then in DMS Settings → Plugins: **Scan for Plugins**, enable **kbctrl RGB**,
add it to your DankBar layout, and restart the shell (`dms restart`).

## Backend prerequisites

The plugin shells out to `python3 -m kbctrl.ctl` (the daemon IPC client), so:

1. **kbctrld must be running** (it owns the hardware):
   `sudo systemctl enable --now kbctrld.service`
2. **The daemon socket must be reachable from your session.** The daemon now
   binds `/run/kbctrl/kbctrl.sock` with mode `0666`, so a user-session plugin
   can connect. This needs the updated daemon: restart it with
   `sudo systemctl restart kbctrld.service` after updating the repo.

Override the interpreter/module path in the plugin's settings if the package
isn't at `/home/gumwars/kbctrl`.

## Features

Mirrors the TUI (`kbctrl-tui`):

- **Effects** — all 9 (breathing, wave, random, rainbow, ripple, marquee,
  raindrop, aurora, fireworks) with per-effect params: speed, brightness,
  color, direction, reactive. Apply, or Apply & Save to flash + profile.
- **Keys** — click a key on the matrix, edit R/G/B, set one or all keys, push
  to hardware (optionally save). Global brightness slider.
- **Lightbar** — brightness slider + Off/Dim/Mid/Full presets.
- Restore default palette, everywhere the TUI offers it.

The matrix scan tab (a one-time keymapping tool, not a control) is intentionally
not ported.

## Development

Reload without restarting the shell:

```
dms ipc call plugins reload kbctrl
```

## Layout of the plugin

```
plugin.json          manifest
KbCtrlWidget.qml     bar pill + popout UI
KbCtrlSettings.qml   command overrides
StartupCheck.qml     verifies the daemon is reachable before enabling
```