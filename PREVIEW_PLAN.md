# Plan: browser preview + layout editor for the ST7789 panel

> Status: **built.** Phases A–E all landed; it lives in
> [`preview/`](preview/README.md) as a self-contained sub-project. This file is
> kept as the design record. Where the build differs from the plan:
>
> - **Files moved.** The plan said `web/` and a top-level `tools/`; everything the
>   project owns is under `preview/` instead, so it reads as one thing you can
>   take in at a glance. The exceptions are the files the firmware and the host
>   harness own first — `layout.json`/`layout.h`, `tests/stubs/`, `test_ui.cpp`,
>   `vectors.csv` — which stayed where they were.
> - **layout.json landed in Phase B, not D.** `render.js` reads it from the start,
>   which means the Phase B pixel diff *is* the proof that layout.json's numbers
>   match `ui.h`'s literals. Phase D then only had to switch the C side over, with
>   the same diff proving that change was also zero-pixel. Both halves end up
>   verified instead of one.
> - **Verification 1 needed restating.** "ASCII output unchanged" cannot hold while
>   also replacing the approximated primitives: Bresenham draws different pixels
>   than DDA, by design. What is unchanged is every *text* row; the sparkline
>   rows shift, which is the fidelity fix landing. `dump()` shades from a
>   graphics-only plane so real glyph pixels don't fog the terminal view.
> - **The divider's colour stayed in C.** The plan listed it as a tunable, but
>   making one element's colour data while every other element's stays in C is
>   inconsistent, and inconsistency here is the first step towards the freeform
>   builder the plan deliberately didn't choose.
> - **A JS engine port was needed.** Not in the plan, but the renderer draws
>   strings the engine produces, so it cannot render a frame without one. It is
>   held to `vectors.csv`'s `expect_*` columns like the other two.
> - **Collision warnings are text-vs-text only.** "Any two boxes overlap" fires on
>   every frame, because the verdict name is supposed to sit inside the verdict
>   bar. See `preview/README.md`.
> - **`vectors.csv` gained one behaviour change**, not just columns:
>   `flat_window_no_signal` now describes a dearer-than-usual station, which
>   covers the negative-`bestSave` branch that nothing exercised before.

## Context

The screen layout currently lives as magic numbers inside imperative draw calls
in [`firmware/gasprices/ui.h`](firmware/gasprices/ui.h) — `lcd.setCursor(6, 22)`,
`fillRect(0, 62, LCD_W, 38)`. Changing it means editing C, recompiling,
reflashing, and squinting at a 1.47" panel: roughly a 40-second loop for a
2-pixel nudge.

`make -C tests ui` already shortens that by rendering to ASCII on the host, and
it has earned its keep — it caught the sparkline dot being drawn half off-panel,
and a reason string silently truncating at 21 characters. But it renders text
into a **character grid**, not real glyphs. The stub's own header admits it can
only catch "geometry that runs off the edge". It cannot show what the panel
actually looks like, and it cannot be dragged.

**Goal:** a local web app that (a) shows a pixel-faithful preview of the panel
across every test state, and (b) lets the layout be adjusted by dragging, with
the result flowing back into the firmware as generated code.

## What exists today (verified, not assumed)

| Fact | Detail |
|---|---|
| Panel | 1.47" ST7789, 172×320 native, drawn landscape as **320×172** |
| Renderer | `ui.h`, ~230 lines, ~12 visual elements, all coordinates hardcoded |
| Host harness | `tests/test_ui.cpp` + `tests/stubs/Adafruit_ST7789.h`, ASCII output |
| Stub fidelity | Text goes to a 53×21 **char grid**; pixels only for graphics primitives |
| Shared states | `tests/vectors.csv`, 14 cases, already read by 3 test programs |
| Font source | `Adafruit_GFX_Library/glcdfont.c` — 5×7, 256 glyphs × 5 bytes, present locally |
| Toolchain | node **v22.18.0**, npm 11.6.1, python3 3.12.8. **No emcc** (emsdk ≈ 1 GB) |
| Web assets | none — this is greenfield |

Primitives `ui.h` actually uses — the entire surface that needs porting:
`fillScreen`, `fillRect`, `drawRect`, `drawFastHLine`, `drawFastVLine`,
`fillCircle`, `drawLine`, and text via
`setCursor`/`setTextSize`/`setTextColor`/`print`.

Two details confirmed by reading `Adafruit_GFX.cpp`, both of which the current
stub gets wrong and a faithful renderer must get right:

- `writeLine` is **Bresenham**; the stub uses a DDA approximation.
- `fillCircle` is a **Bresenham circle** via `fillCircleHelper`; the stub uses a
  distance test.
- `setTextColor(c)` with one argument sets background = foreground, which
  Adafruit treats as **transparent** — only set bits are drawn, no background
  fill.

## Architecture

```
tests/vectors.csv ──┬─> test_verdict.cpp   engine (C)
                    ├─> test_verdict.py    engine (Python)
                    ├─> test_ui.cpp        golden renderer (C++) ──> *.ppm
                    └─> web/render.js      JS renderer ──> framebuffer
                                                   │
                            tests/compare_render.mjs  ← must be 0 differing px

firmware/gasprices/layout.json ──> tools/gen_layout.py ──> layout.h
              ▲                                              │
              └──── web editor (drag)                        └──> ui.h reads L_*
```

**The framebuffer trick.** `render.js` targets a plain `Uint8Array`, *not* the
Canvas API. In the browser it blits via `putImageData` (scaled up with
`image-rendering: pixelated`); in node it is compared byte-for-byte against the
C++ output. So the golden-diff needs no `node-canvas`, no puppeteer, no headless
browser — and the project keeps its zero-dependency property.

C++ writes **binary PPM (P6)**: trivial to emit without libpng, trivial to parse
in node.

---

## Phase A — make the C++ stub render real pixels

Foundation for everything else. Without it there is no golden image.

- Vendor `glcdfont.c` as `tests/stubs/glcdfont.h` (256 glyphs × 5 column-bytes).
  Vendored rather than included from the Arduino library path, so CI and a fresh
  clone both work without an Arduino install.
- `tests/stubs/Adafruit_ST7789.h`: change `px_` from `char` to `uint16_t` RGB565,
  and make `emit()` rasterise real glyphs into `px_` **in addition to** writing
  the existing char grid. Keep `txt_`/`dump()` exactly as-is — the ASCII view is
  genuinely useful for reading strings in a terminal and has already caught
  bugs. Both outputs, one draw pass.
- **Replace the approximated primitives with Adafruit's real ones** (Bresenham
  line and circle, per above). This is the step that makes the preview match the
  *device*, not merely match itself.
- Add `writePPM(const char *path)` and a `--ppm <dir>` flag to `test_ui.cpp`.

**Boundary, stated honestly:** this proves JS == C++ == Adafruit's *geometry*.
It does not model ST7789 panel gamma, backlight, or viewing angle. Colour
choices still need one look at real hardware; positions and clipping do not.

## Phase B — JS renderer + golden diff

- `web/glcdfont.js` — generated from the same vendored font by
  `tools/gen_font.py`, so the two copies cannot drift.
- `web/render.js` — ES module exporting `render(state, layout) -> Uint8Array`.
  Ports the 8 primitives plus `drawChar`, mirroring `ui.h`'s draw order exactly.
- `tests/compare_render.mjs` — for each case: parse the C++ PPM, run `render.js`,
  compare. On mismatch report the case, the first differing pixel, and both
  colours. Exit non-zero.
- Wire into `.github/workflows/tests.yml` as a fourth job.

**This is the piece that makes the preview worth trusting.** Without it, a JS
port is just a second implementation quietly drifting from the first — exactly
the failure mode the shared `vectors.csv` was introduced to prevent for the
verdict engine.

### Station fields must come from the file, not be re-derived

`vectors.csv` has no station columns, so `test_ui.cpp` currently *synthesises*
them (`in.today % 2`, `in.today % 4`). If `render.js` re-implements that
synthesis, the diff tests the synthesis rather than the renderer.

Fix: append optional columns to `vectors.csv` — `label`, `station_idx`,
`station_count`, `is_cheapest`, `vs_best`, `save`. Appending is safe:
`test_verdict.cpp` and `test_ui.cpp` index fixed positions `c[0]..c[10]`, and
`test_verdict.py` uses `DictReader`. Nothing else needs touching.

## Phase C — the preview page

- `web/index.html` — no framework, no build step, no `npm install`.
- Gallery of all 14 `vectors.csv` cases, each a canvas at 2× with
  `image-rendering: pixelated`, captioned with case name and verdict.
- Click a case to open it large.
- A "clipped" badge on any case drawing outside 320×172 — port the stub's
  `clipped_` flag into `render.js`, since that check is what has historically
  caught the real bugs.

## Phase D — extract layout into data

The change that makes editing possible. `firmware/gasprices/layout.json` becomes
the source of truth; `tools/gen_layout.py` emits `firmware/gasprices/layout.h`.

Elements to extract, from the current `ui.h`:

| id | element | tunables |
|---|---|---|
| `header` | station label + rank + cheapest tag | x, y, size |
| `age` | age / OFF, right-aligned | right, y, size |
| `divider` | horizontal rule | y, colour |
| `price` | big price, size 4 | x, y, size |
| `level_text` | `LVL 87%`, right-aligned | right, y, size |
| `level_bar` | proportional bar | x, y, w, h |
| `verdict_bar` | filled rect | x, y, w, h |
| `verdict_text` | centred in bar | y, size |
| `reason` | one line of English | x, y, size |
| `tank` | `TANK HALF` | x, y, size |
| `savings` | SAVE / USUAL / +N, right-aligned | right, y, size |
| `sparkline` | line chart + today dot | x, y, w, h, dot_r |
| `message_title` / `message_detail` | boot + error screen | y, size |

Generated form — plain consts, no runtime cost, `ui.h` stays readable:

```c
// GENERATED by tools/gen_layout.py from layout.json — do not edit by hand.
typedef struct { int16_t x, y, w, h; uint8_t size; } GpElem;
static const GpElem L_price = { 6, 22, 0, 0, 4 };
```

`ui.h` then reads `L_price.x` instead of the literal `6`.

**Explicit boundary — this is a layout tweaker, not Figma.** Only geometry and
static styling move into data. What stays in C: conditional visibility (the
CHEAPEST tag), computed values (right-alignment, bar fill fraction, sparkline
scaling), and dynamic colours (verdict colour, green-when-cheapest). Expressing
those as data is what would turn this into the full freeform-builder option that
was deliberately not chosen.

## Phase E — the drag editor

- Absolutely-positioned overlay divs on the canvas, one per element, driven from
  `layout.json`. Drag to move; handles to resize where `w`/`h` apply.
- Arrow keys nudge 1px, shift+arrow 10px — a 320×172 panel is pixel-sensitive
  and dragging alone will be frustrating.
- Snap guides against panel edges and neighbouring element bounds.
- Live re-render on every change (instant, since `render.js` is pure JS).
- Collision/overflow warning when element bounds intersect or leave the panel.
- `web/server.py` — ~50 lines on `http.server`: serves `web/`, `GET`/`PUT`
  `layout.json`, and `POST /generate` to run `tools/gen_layout.py`. Stdlib only,
  matching the backend's existing no-dependency rule.

---

## Files

```
web/index.html                   preview + editor page
web/render.js                    framebuffer renderer (browser + node)
web/glcdfont.js                  GENERATED from the vendored font
web/states.js                    vectors.csv -> render states
web/editor.js                    drag/nudge/snap, layout.json read+write
web/server.py                    stdlib server, codegen trigger
tools/gen_layout.py              layout.json -> layout.h  (+ --check)
tools/gen_font.py                glcdfont.c -> glcdfont.h + glcdfont.js
tests/stubs/glcdfont.h           GENERATED, vendored font
tests/stubs/Adafruit_ST7789.h    real RGB565 pixels + Adafruit's primitives
tests/test_ui.cpp                gains --ppm
tests/compare_render.mjs         golden diff
tests/vectors.csv                gains optional station columns
firmware/gasprices/layout.json   source of truth
firmware/gasprices/layout.h      GENERATED
firmware/gasprices/ui.h          reads L_* instead of literals
```

## Verification

1. `make -C tests ui` — ASCII output unchanged from today, proving Phase A did
   not regress the existing harness.
2. `make -C tests golden && node tests/compare_render.mjs` — **0 differing
   pixels** across all 14 cases. This is the gate for the whole design.
3. `python3 tools/gen_layout.py --check` — fails if `layout.h` is out of sync
   with `layout.json`, so a hand-edit of the generated file cannot silently
   survive. Add to CI.
4. `python3 web/server.py`, open the page, confirm all 14 cases render and match
   the ASCII dump's geometry.
5. Drag `price` down 10px, regenerate, confirm `git diff
   firmware/gasprices/layout.h` shows only that change.
6. `arduino-cli compile --fqbn "esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB" firmware/gasprices`,
   upload, confirm the panel matches the browser for the live state.
7. `make -C tests test` — the verdict engine tests must be untouched by all of
   this.

## Sequencing

**A → B** are the foundation and should land together; a preview nobody trusts
is worse than no preview.

**C** is the first user-visible payoff and a natural stopping point — if the
editor never gets built, a faithful multi-state preview is still worth having.

**D → E** only pay off once the preview is trustworthy. D is the riskiest step
for the firmware, since it touches every draw call in `ui.h`, so it should be a
separate commit that changes rendering output by **exactly zero pixels** —
which is verifiable with the golden diff from B.
