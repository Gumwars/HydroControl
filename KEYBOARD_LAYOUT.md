# Keyboard layout brief — paste this to OpenDesign

> Give everything below the line verbatim. Do **not** include the coordinates
> from `key_matrix.json` — those are LED addresses, not positions. The matrix
> is sparse (wide keys leave gaps) and its row 0 is the *bottom* row, so
> rendering it literally produces a gap-riddled, upside-down keyboard.

---

## Revise the per-key keyboard editor

The structure is right — six rows, correct key sequences, the surrounding
controls. The problem is layout geometry. Rebuild it on a fixed grid.

### Use a unit grid, and do not stretch it

- **1 unit (1u) = one standard keycap.** Make 1u roughly square — say 44×44px
  — with a small uniform gap between keys.
- **Every row is exactly 19u wide.** No exceptions. This is what makes keys
  align into vertical columns.
- **The keyboard has a fixed aspect ratio.** Do not stretch it to fill the
  container width. Centre it and let it be as wide as 19u makes it. The
  current version is stretched to roughly 4:1 per key, which no keycap is.
- Rows 2–6 split as **15u main block + 4u number pad**, edge to edge with no
  gutter between them (this is a laptop, not a full-size desk keyboard).

### Row 1 — function row · 20 keys × 0.95u

`Esc` `F1` `F2` `F3` `F4` `F5` `F6` `F7` `F8` `F9` `F10` `F11` `F12`
`ScrnCap` `PrtSc` `Del` `Home` `PgUp` `PgDn` `End`

Slightly narrower than 1u so twenty fit the 19u width, and slightly shorter in
height than the rows below. This row does **not** split into main + numpad.

### Row 2 — number row

Main 15u: `` ` `` `1` `2` `3` `4` `5` `6` `7` `8` `9` `0` `-` `=` all 1u,
then `Bksp` **2u**
Numpad 4u: `NumLk` `Num /` `Num *` `Num -` all 1u

### Row 3

Main 15u: `Tab` **1.5u**, then `Q` `W` `E` `R` `T` `Y` `U` `I` `O` `P` `[` `]`
all 1u, then `\` **1.5u**
Numpad 4u: `Num 7` `Num 8` `Num 9` 1u each, then `Num +` 1u — **double height,
spans rows 3 and 4**

### Row 4

Main 15u: `Caps` **1.75u**, then `A` `S` `D` `F` `G` `H` `J` `K` `L` `;` `'`
all 1u, then `Enter` **2.25u**
Numpad 4u: `Num 4` `Num 5` `Num 6` 1u each — the fourth column is occupied by
`Num +` continuing down from row 3

### Row 5

Main 15u: `LShift` **2.25u**, then `Z` `X` `C` `V` `B` `N` `M` `,` `.` `/`
all 1u, then `RShift` **2.75u**
Numpad 4u: `Num 1` `Num 2` `Num 3` 1u each, then `Num Enter` 1u — **double
height, spans rows 5 and 6**

### Row 6 — bottom row

Main 15u: `Ctrl` **1.25u**, `Fn` 1u, `Super` 1u, `Alt` **1.25u**,
`Space` **5.5u**, `Alt` 1u, `Ctrl` 1u, then the arrow cluster in 3u
Numpad 4u: `Num 0` **2u**, `Num .` 1u — the fourth column is occupied by
`Num Enter` continuing down from row 5

**Arrow cluster (3u total):** `←` full height 1u, then a 1u column split
horizontally — `↑` in the top half, `↓` in the bottom half — then `→` full
height 1u.

### Double-height keys

`Num +` and `Num Enter` each occupy one column across two rows. In the current
version they overflow off the right edge as slivers. They must sit inside the
numpad block, flush with the keys beside them.

### Interaction (already correct — keep it)

Click to select and paint · Shift+click multi-select · Alt+click select row ·
drag rectangle · Ctrl+A select all · Esc clear.

One thing to check: **selection state and key colour are two different visual
jobs.** A key painted near-black or near-white must still show clearly whether
it is selected. Use an outline or ring that reads against any fill, not a tint.

Legends must stay readable on any key colour.
