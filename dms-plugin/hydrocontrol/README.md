# HydroControl for DMS

A bar widget and flyout for DMS (the Dux / Dank Material Shell), on Hyprland or
niri. The bar shows the CPU temperature or package power; the flyout carries
what you reach for often:

- **Live** — CPU and GPU temperature, package power with a one-minute trace,
  fan speeds
- **Power mode** — your presets, or the machine's own modes
- **Battery** — charging mode, charge limit, Power Saver
- **Fans** — firmware control or a curve, and which preset curve
- **Graphics** — the current mode; switching opens the full app, because it is
  a firmware write that needs a reboot

Per-key lighting, the chin bar, power-limit sliders, the curve editor, display
modes and the cooling dock stay in the full app (the ↗ button opens it).

## How it works

It is a client of `hydroc-server`, the root daemon on `127.0.0.1:8781`, and
runs as you. It changes nothing itself; every change is a request the daemon
validates exactly as it does for the app.

- Changes carry the token from `/run/hydroc/token`, which the daemon hands to
  the user at the keyboard. Without it the flyout is read-only.
- The bar polls every 5 s (configurable); the flyout reads once a second only
  while open. The daemon shares one EC read between every client.

## Install

Needs hydroc-server v0.9.10 or later running.

```bash
ln -s /path/to/HydroControl/dms-plugin/hydrocontrol ~/.config/dms/plugins/hydroControl
```

```bash
dms ipc call plugins enable hydroControl
```

Then add **HydroControl** to the bar in Settings → DankBar layout.
