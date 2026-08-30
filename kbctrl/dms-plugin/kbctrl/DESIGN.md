# kbctrl — DankBar Widget · Design Handoff

**Product:** RGB control for the Eluktronics HYDROC-16 (ITE 8291 keyboard + ITE 8233 lightbar)
**Surface:** DankMaterialShell (DMS) bar widget with a popout panel
**Status:** v1.0 shipped and functional — this doc is the redesign brief for OpenDesign
**Source of truth (current build):** `dms-plugin/kbctrl/KbCtrlWidget.qml`
**Owner handoff:** gumwars

---

## 1. What this is

A pill on the DankBar that opens a popout panel for controlling the keyboard RGB:

- **Effects** — pick one of 9 animated effects, tune its parameters, apply.
- **Keys** — per-key color editing on a live keyboard-matrix grid.
- **Lightbar** — brightness for the laptop's light bar (firmware-fixed color cycle).

The widget talks to a root daemon (`kbctrld`) over a Unix socket; every action is
a round-trip that applies to real hardware and persists to a profile. The UI
is a thin client — **the hardware behavior must not change**, only the design.

---

## 2. Platform & build constraints (read before designing)

Everything must be buildable in **Qt Quick / QML** inside the DMS plugin system.
No HTML, no CSS, no web tech.

### Surface requirements
- **Two bar pills** are mandatory: `horizontalBarPill` (landscape bar) and
  `verticalBarPill` (side bar). Both must be designed.
- **One popout** opens from the pill. It uses DMS's `PopoutComponent` chrome —
  the shell owns the header row, optional detail line, close button, and
  dismiss-on-click-outside. We do **not** design custom window chrome.
- The popout is **fixed-size today: 620 × 660 px**, content scrolls vertically
  (`DankFlickable`). These bounds are negotiable but are the current contract.

### Component inventory available (do not invent new ones without checking)
| Component | Use for |
|---|---|
| `DankButton` | taps, tabs, effect chips, presets |
| `DankSlider` | numeric params (speed, brightness, R/G/B) |
| `DankDropdown` | enumerated params (color, direction) |
| `DankToggle` | boolean params (reactive) |
| `DankIcon` | material icons |
| `StyledText` | labels, headers, body |
| `DankFlickable` | scroll container |
| `PopoutComponent` | popout chrome + `closePopout()` |
| `Theme.*` | **all** color, spacing, type, radius tokens |

### Theming rules (DMS hard requirement)
- **Only `Theme.*` tokens.** No hardcoded colors, spacing, or font sizes —
  the widget must survive light/dark/auto theme and any accent color.
- The one exception: **key colors and any RGB preview are *data*** (the user's
  actual lighting), not theme colors. Contrast for text on a key is computed
  from luminance (`fgColor(r,g,b)` — dark text above ~128 luminance).

### Dynamic data the UI must render
- **Keyboard matrix** — JSON-driven rows of `[label, row, col, width]`, 6 rows,
  up to 85 keys, **variable key widths** (spacebar is wide, arrows are narrow).
  The grid must lay out from this data; it is never hardcoded.
- **Key colors** — map `"row,col" → [r, g, b]`, 0–255 each. Unset keys show
  the surface/outline "off" treatment.
- **Effects** — 9 items, each with a *contextual* subset of params:
  `speed (0–10)`, `brightness (0–50)`, `color` (9 named), `direction` (5 named),
  `reactive` (bool). Only show params the selected effect supports.
- **Lightbar** — single int 0–50; 0 = off.
- **Status** — `connected` / `no hardware` / `daemon off` / loading.

### States that must be designed
1. **Loading** — first open, status not yet returned.
2. **Connected** — normal operation.
3. **No hardware** — daemon up, but the keyboard isn't found (badge/detail).
4. **Daemon off** — IPC unreachable; widget shows degraded state, no toasts on apply.
5. **Off / unlit** — key with no color assigned; lightbar at 0.

---

## 3. Current build — honest inventory

Shipped, works, plain. Known weak spots, in order of visual impact:

| Area | What's there now | Why it looks unfinished |
|---|---|---|
| **Bar pill** | keyboard icon + effect name text | flat, no affordance it's clickable, no color signal |
| **Popout chrome** | default `PopoutComponent` header/detail | generic; detail line is a run-on string of state |
| **Section tabs** | 3 text buttons, active = filled `primary` | no iconography, no grouping |
| **Effects** | wrap `Flow` of 9 text chips | flat equal-weight list; no preview of what an effect looks like |
| **Params** | label + `DankSlider`/`DankDropdown`/`DankToggle` | no hierarchy; no live swatch for "color"; sliders have no icons/units |
| **Keys** | full matrix of small rounded rects, 9px labels | cramped; **no live preview** of the picked color before applying; selection is a thin border |
| **RGB picker** | three 0–255 sliders + Set key / Set all | no color swatch, no HSV alternative, no "what will this look like" |
| **Actions** | text buttons (Apply, Apply & Save, Push, Push & Save…) | confusing pair duplication; no primary/secondary hierarchy |
| **Lightbar** | slider + Off/Dim/Mid/Full preset chips | fine functionally, visually bare |

**Two interaction complaints to solve in the redesign:**
1. **No feedback loop.** You adjust R/G/B sliders and can't see the result until
   you click "Set key" on a tiny label that doesn't show the current pick color.
2. **Save semantics are unclear.** "Apply vs Apply & Save" / "Push vs Push & Save"
   read as the same thing. The real difference: *Save* also flashes the setting
   to keyboard firmware so it survives reboot. Needs clearer copy/hierarchy.

---

## 4. Design goals

1. **Say "RGB" at a glance.** The widget should feel colorful and alive —
   within the theme, via *data* color (key grid, swatches, preview), not by
   breaking the theme palette.
2. **Zero-surprise color editing.** The single most important interaction is
   picking a color and seeing it on the keyboard. Make that loop immediate and
   visible: **live preview of the working color** wherever a color is being set.
3. **State is visible.** Connected vs degraded must be readable from the pill
   and the popout without reading text.
4. **Two-tier actions.** Primary (apply now) vs secondary (apply **and** flash
   to firmware). Make the destructive/permanent one visually distinct and
   clearly labeled.
5. **Stay in the family.** Reuse DMS patterns (DankBar widgets, Control Center
   tiles, Settings rows) so it doesn't look like a stranger on the bar.

---

## 5. Surface spec

### 5.1 Bar pill

**Horizontal (default):** compact, one gesture target. Proposed anatomy:
- state dot or tinted icon (primary = ok, error = degraded, no dot while loading)
- short label: current effect name, or "RGB" with no label when space is tight

**Vertical (side bar):** stacked icon + label, centered, no wider than other pills.

Deliver both, plus hover/pressed states, and the degraded-state variant.

### 5.2 Popout chrome

Header: **"Keyboard lighting"** (or similar; "kbctrl — RGB" is a placeholder).
Detail line: one-line, structured state — e.g. `Connected · rainbow · 25%`
(replace the current run-on). Keep the shell-provided close button.

### 5.3 Section navigation

Three sections. Proposal: keep tabs (Effects / Keys / Lightbar) but give each
an icon + label, and let the **active section tint** use `primary`. Keys is the
heaviest section — consider making it the default tab.

### 5.4 Effects

- **Effect list** → 9 chips/tiles. Two suggested directions for the designer:
  (a) compact chip grid like today, or (b) a list where the selected effect
  shows a **mini animation preview** (a small swatch that cycles its colors).
  A preview strongly communicates "effect" without hardware access.
- **Params** → only the active effect's params, grouped under a "Parameters"
  divider. `Color` param deserves a **named-color swatch row** (dot + name)
  instead of a bare dropdown if it fits.
- **Actions** → primary "Apply", secondary "Apply & Save" (flash to firmware),
  and a quiet "Reset palette" as tertiary (text link, not a button).

### 5.5 Keys (the flagship)

- **Matrix grid** — real keyboard silhouette from the data: variable-width
  keys, 6 rows. Suggest row grouping + generous 6–8px gaps; labels ≥ 11px.
- **Selection** — clear affordance (accent border + elevation, not just a 2px ring).
- **Live preview** — the picked color must be visible immediately: a preview
  swatch next to the sliders, and optionally a live tint on the selected key
  **before** "Set key" commits it.
- **Pickers** — keep R/G/B sliders, but add a **swatch + optional HSV panel**.
  Deliver a clear "current vs new" swatch (old color / new color split) so the
  user sees the delta.
- **Selection affordance** — the "Editing key" line should name the *label*
  ("Editing: `Space`"), not raw `row,col` coordinates.
- **Actions** — "Set key" (enabled only when a key is selected), "Set all keys",
  then primary "Push to keyboard" and secondary "Push & Save" (flash).

### 5.6 Lightbar

- Slider (0–50) + preset chips (Off/Dim/Mid/Full) + primary "Apply".
- Needs an explicit **Off** visual identity (an "off" state for the section).

---

## 6. Copy & labels

Plain, imperative, short. Existing strings (all user-facing):

- Sections: **Effects · Keys · Lightbar**
- Effects: `breathing wave random rainbow ripple marquee raindrop aurora fireworks`
- Colors: `none red orange yellow green blue teal purple random`
- Directions: `none right left up down`
- Actions: **Apply / Apply & Save / Restore palette / Set key / Set all keys / Push / Push & Save / Apply lightbar**
- States: `connected` · `no hardware` · `daemon off`

Designer may reword, but **do not rename the nine effects** (they map 1:1 to
firmware effect IDs).

---

## 7. Accessibility

- Key text contrast computed from key color luminance (existing `fgColor`).
- Focusable controls (tabs, chips, sliders, keys) must show a focus state;
  the popout already requests keyboard focus when open.
- Touch: hit targets ≥ 32px on keys, standard DMS button heights elsewhere.
- Color must never be the *only* signal — pair state changes with text/icon.

---

## 8. What NOT to change (hardware contract)

- The nine effect names and their param ranges (`speed 0–10`, `brightness 0–50`,
  `color` 9, `direction` 5, `reactive` bool).
- Per-key colors are arbitrary RGB 0–255; save = flash to firmware.
- Lightbar is **brightness only** (firmware runs a fixed color cycle). There is
  no static-color or effect selection for it — do not design one.
- Apply actions are async (round-trip to the daemon); brief feedback
  (toast / spinner) is acceptable, but the UI must not block.

---

## 9. Open questions for OpenDesign

1. Pill anatomy: icon-only vs icon+label; how much label to show on narrow bars?
2. Effects: chip grid or preview list? Is a per-effect animated preview worth the
   space, or should the popout show one **live "current hardware" preview** at
   the top instead?
3. Keys tab: keep full 85-key matrix, or show it behind a toggle with a
   simplified "recently edited" mode?
4. RGB editing: sliders only, or HSV + hex entry as a secondary panel?
5. Popout size: is 620×660 right, or should the Keys tab get a wider canvas
   (e.g., 760px) with the other tabs staying compact?
6. Primary/secondary action styling for the flash-to-firmware distinction.

## 10. Deliverables

- Spec/mockups for: horizontal pill, vertical pill, all degraded states,
  all three tabs (including param-less effects), swatch/HSV picker, keyboard
  grid with selection + live preview.
- Annotated token usage (every color/spacing/type choice mapped to a `Theme.*`
  token), and any new tokens needed (proposed names + default values only —
  implementation lives with the dev).
- Copy pass on action labels, especially Apply/Apply & Save semantics.
- Final QML structure recommendation if it changes the component tree.