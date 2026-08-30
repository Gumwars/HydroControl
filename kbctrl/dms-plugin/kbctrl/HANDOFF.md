# HANDOFF — kbctrl web-prototype → DMS plugin

For: OpenDesign review of the `Web-Prototype.zip` export (`index.html`) against the
production target. Target is **not a web page**: it is a Quickshell/QML plugin
rendered inside DankMaterialShell (DMS). The popup is a layer-shell window; the
pill lives on the bar. Several CSS-only techniques in the export have **no QML
equivalent** and were re-expressed with DMS-native equivalents (listed below).

Delivered: `KbCtrlWidget.qml` now implements the prototype's shell, three tabs,
status pills/banners, effect list with animated tiles, the per-key editor, and
the lightbar preview, using only DMS `Theme.*` tokens + `qs.Widgets` components.

---

## Issues OpenDesign must resolve (ranked)

### 1. Keyboard matrix is a placeholder — data-driven, real layout
The prototype renders a generic ANSI `KEYBOARD` array (25 quarter-units, 6 rows,
no numpad). The product keyboard (Eluktronics HYDROC-16, ITE 8291) has a
**19-column matrix including a numpad** and a staggered bottom row with wide
gaps (Ctrl/Fn/Win/Alt…Space…Alt/Ctrl + arrows + numpad cluster). The widget
renders the matrix from the daemon's `layout` (per-key `[label, row, col,
width]`), so the geometry, row heights, and gap layout differ from the export.

→ **Resolve:** either restyle the matrix generically (any 19-col grid) or update
the export to show a full-size keyboard with numpad. The 760px Keys-tab width
was designed around the ANSI matrix and is the right width for the real one too.

### 2. oklch / `color-mix()` token math
The entire palette is `oklch()` + `color-mix(in oklch, …)`. QML colors are sRGB
`Qt.rgba()`; there is no oklch or color-mix. I mapped surface/primary/status
roles to DMS `Theme.*` tokens (which already implement the "DMS family" the
critique describes: translucent surface, blue primary, green/amber/red status),
and kept RGB values only as **data color** on keys/swatches as intended.

→ **Resolve:** state the palette in hex/sRGB so it can be diffed against DMS
`Theme.*` in light **and** dark mode (the export ships both; DMS derives the
theme automatically).

### 3. `prefers-reduced-motion`
QML has no media queries. Reduced-motion is honored via DMS
`SettingsData.animationSpeed === AnimationSpeed.None`, which disables all tile
animations, the lightbar cycle, and the loading spinner. Behavior parity is fine;
the mechanism differs.

### 4. Popup shell chrome (blur, caret, shadow, resize animation)
`backdrop-filter` blur, the caret/arrow to the pill, the drop shadow, and the
animated **width transition** (620→760px on tab switch) are owned by DMS
(`DankPopout` + `PluginPopout`): blur/shadow/positioning come from the shell and
cannot be overridden per plugin, and the 760px Keys-tab resize snaps rather than
eases. The 620/760 widths themselves are honored.

→ **Resolve:** confirm the snap-resize is acceptable, or design a fixed-width
popup (760) with a nested content column.

### 5. In-popout toast stack
The export's bottom-right toast is a product surface. DMS shows toasts globally
via `ToastService` (system OSD). I moved all feedback there with the same copy
and success/error styling. Non-idiomatic to replicate in-window toasts.

### 6. CSS-only key-cap effects
- `:hover` / `:active` transforms and the selection `::after` glow ring →
  replaced by a 2px primary border + 1px lift (no shadow glow in QML).
- Diagonal-stripe "unlit" swatch (`repeating-linear-gradient`) → flat off-tint.
- Conic-gradient "random" dot → small `Canvas` rainbow wedges.
- Lightbar "Off" dashed border → flat surface + solid border.
- Custom thin scrollbar → DMS `DankScrollbar` styling.

### 7. Effect tile animations
CSS keyframe animations (`background-position`, `hue-rotate`, `steps(1)`
conic-rotate, ripple/fireworks) were re-implemented as QML `NumberAnimation`s.
Behavior: only the **selected** effect's tile animates; speed scales with the
effect's speed param. Deviations: no `hue-rotate` (rainbow uses a white band
sweep), "random" cycles discrete colors on a timer (no gradient rotation),
ripple/fireworks geometry simplified. The overall "one animated tile" flourish
is preserved.

### 8. Loading state
The shimmer skeleton → DMS `DankSpinner` + "Contacting kbctrld…". Loading is
brief (a socket round-trip), so the richer skeleton is not worth porting.

### 9. Accessibility mapping
`aria-selected`/`aria-label`/`role` and `h1/p` hierarchy don't map 1:1 to QML.
DMS components participate in the QML keyboard-focus system; tab buttons expose
`tabClicked`/hover/pressed states natively.

---

## Already faithful (no action needed)
- Header: app icon + status dot (ok/warn/danger/loading) + "Keyboard lighting" +
  `state-pill · effect · %` detail line + close button.
- Degraded-state banners: "Keyboard not found…" (warn) and "kbctrld is not
  reachable…" (danger) with the exact export copy.
- Three tabs (Effects / Keys / Lightbar) with icons; selected = primary-container.
- Effects: 9 rows (tile + name + blurb + check), "Parameters" divider,
  speed 0–10 / brightness 0–50 / color chips (9, incl. rainbow "random" dot) /
  direction (none/right/left/up/down) / reactive switch; Apply / Apply & Save /
  Reset palette; hint copy preserved verbatim.
- Keys: "Recently edited" chip strip (≤12, mini-swatch, remove ×, live re-color),
  `Editing: label · row r, col c` line, Current/New swatch + RGB sliders 0–255
  with mono readouts, Set key / Set all keys / Push / Push & Save, hint copy.
- Lightbar: animated firmware color-cycle strip + dim overlay + Off/Dim/On pill +
  `N / 50` mono caption, brightness 0–50, Off/Dim(12)/Mid(25)/Full presets,
  Apply lightbar, hint copy.
- Bar pill (horizontal + vertical): status dot + keyboard icon + effect label.
- Mono numerics for all readouts; RGB only as data color; copy preserved verbatim.

## Open questions for OpenDesign
1. Is the 19-col + numpad matrix acceptable as the Keys-tab focal point, or does
   the export need a full-layout revision?
2. Width transition on tab switch: confirm snap-resize (620→760) is fine.
3. If the dark/light palette matters beyond DMS `Theme.*`, provide hex tokens.