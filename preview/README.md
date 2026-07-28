# preview — browser preview and layout editor for the ST7789 panel

A self-contained sub-project: a local web app that shows a pixel-faithful preview
of the 320×172 panel across every test state, and lets the layout be adjusted by
dragging, with the result flowing back into the firmware as generated code.

```bash
python3 preview/server.py       # then open http://127.0.0.1:8765/preview/
```

No `npm install`, no build step, no framework, no `node_modules`. The whole thing
is ES modules the browser loads directly and one stdlib Python server, matching
the rest of the repo's zero-dependency rule.

## Why it exists

The screen layout used to live as magic numbers inside imperative draw calls in
[`ui.h`](../firmware/gasprices/ui.h) — `lcd.setCursor(6, 22)`,
`fillRect(0, 62, LCD_W, 38)`. Changing it meant editing C, recompiling,
reflashing, and squinting at a 1.47" panel: roughly a 40-second loop for a
two-pixel nudge.

`make -C tests ui` already shortened that by rendering to ASCII on the host, and
it earned its keep — it caught the sparkline dot being drawn half off-panel, and
a reason string silently truncating at 21 characters. But it rendered text into a
*character grid*, not real glyphs. It could not show what the panel actually
looks like, and it could not be dragged.

## Why you can trust it

The failure mode for a second renderer is obvious: it quietly drifts from the
first, and then the preview is worse than useless because it is confidently
wrong. So it is not allowed to drift.

```
tests/vectors.csv ──┬─> tests/test_verdict.cpp        engine (C)
                    ├─> backend/test_verdict.py       engine (Python)
                    ├─> preview/verdict.js            engine (JS)
                    ├─> tests/test_ui.cpp  ──ui.h──>  golden images (*.ppm)
                    └─> preview/render.js  ─────────> framebuffer
                                                          │
                        preview/tests/compare_render.mjs  ← 0 differing pixels
```

`render.js` is a port of `ui.h`, and `tests/stubs/Adafruit_ST7789.h` is a port of
Adafruit_GFX's real primitives — Bresenham line, Bresenham circle, classic-font
`drawChar`, transparent-background text. Every frame is rendered twice, once
through each, and CI requires the two to be **identical, pixel for pixel**:

```bash
make -C tests golden && node preview/tests/compare_render.mjs
```

`render.js` targets a plain `Uint16Array`, not the Canvas API. In the browser it
blits with `putImageData`; in node it is compared against binary PPM files. That
is why the golden diff needs no `node-canvas`, no puppeteer and no headless
browser.

**What this does not model:** ST7789 gamma, backlight, viewing angle. Positions,
clipping and glyph shapes are faithful. Colour *choices* still need one look at
real hardware.

## Editing the layout

[`firmware/gasprices/layout.json`](../firmware/gasprices/layout.json) is the
source of truth. `layout.h` is generated from it and is what `ui.h` reads.

```
layout.json ──> preview/tools/gen_layout.py ──> layout.h ──> ui.h reads L_*
     ▲
     └── the drag editor writes this back over PUT
```

In the browser: click a case to open it large, then drag an element, or select it
and use the arrow keys — 1px, `shift` for 10px, `alt` to resize. Snap guides
catch the panel edges and neighbouring elements. Then **Save layout.json** and
**Generate layout.h**, and the next `arduino-cli compile` picks it up.

`gen_layout.py --check` runs in CI and fails if `layout.h` has drifted from
`layout.json`, so a hand-edit of the generated file cannot silently survive.

### This is a layout tweaker, not Figma

Only geometry and text size live in `layout.json`. What stays in `ui.h`, on
purpose:

- **conditional visibility** — the CHEAPEST tag, the level bar when the level is
  unknown
- **computed values** — right-alignment, centring, bar fill fraction, sparkline
  scaling
- **dynamic colours** — the verdict colour, green-when-cheapest

Expressing those as data is what would turn this into a freeform UI builder,
which is a much larger and much less useful thing. One consequence worth naming:
the divider's colour is *not* editable, even though it is static. Making one
element's colour data while every other element's stays in C would be the first
step down that road.

## Warnings the editor raises

- **anything drawn outside the panel** — the bug this whole harness exists to
  catch. Shown as a red box, a `CLIPPED` badge in the gallery, and a warning.
- **text overlapping text** — always a mistake. Text over a *filled shape* is not
  flagged, because that is the design: the verdict name is supposed to sit inside
  the verdict bar. Warning on every overlap would cry wolf on every frame.

## Files

| File | |
|---|---|
| `index.html` | preview gallery + editor page |
| `render.js` | framebuffer renderer, a port of `ui.h`; runs in browser and node |
| `verdict.js` | port of `verdict.h`, held to `vectors.csv`'s `expect_*` columns |
| `states.js` | `vectors.csv` → render states |
| `editor.js` | drag, nudge, snap, overlap and overflow checks |
| `server.py` | stdlib server: static files, `PUT` layout.json, `POST /generate` |
| `glcdfont.js` | **generated** — the vendored 5×7 font |
| `tools/gen_font.py` | `glcdfont.c` → `tests/stubs/glcdfont.h` + `glcdfont.js` |
| `tools/gen_layout.py` | `layout.json` → `layout.h` (+ `--check`) |
| `tests/compare_render.mjs` | the golden pixel diff |

Files this project owns outside its own directory, because the firmware and the
host harness own them first:

| File | |
|---|---|
| `firmware/gasprices/layout.json` | source of truth for the layout |
| `firmware/gasprices/layout.h` | **generated** |
| `tests/stubs/glcdfont.h` | **generated**, vendored font |
| `tests/stubs/Adafruit_ST7789.h` | real RGB565 pixels + Adafruit's primitives |
| `tests/test_ui.cpp` | `--ppm` writes the golden images |
| `tests/vectors.csv` | gained the `ui_*` display columns |

## The server writes to your working tree

`preview/server.py` binds to `127.0.0.1` only, refuses cross-origin writes,
accepts `PUT` for exactly one path, and runs one generator with a fixed argv. It
edits files and runs code — do not put it on a network.
