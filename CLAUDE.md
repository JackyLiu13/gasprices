# CLAUDE.md

Context for working in this repo. Read this before changing anything.

## What this is

A glanceable "should I fill up, and where?" gas-price indicator for Richmond
Hill / Markham, Ontario. A **Waveshare ESP32-C6-LCD-1.47** fetches one small
JSON file twice a day, runs a decision engine locally, and shows the answer on
its onboard 320×172 ST7789 plus the RGB LED.

```
GitHub Action (cron 2×/day)              ESP32-C6
  RBOB futures + USD/CAD                   HTTPS GET data.json
  + Ontario weekly retail survey    ──>    verdict engine (verdict.h)
  + your logged station prices             station cycling via button
  -> docs/data.json (~3 KB)                ST7789 + RGB LED
```

Live feed: `https://jackyliu13.github.io/gasprices/data.json`
(GitHub Pages, served from `docs/` on `main`).

The split is deliberate: scraping and modelling change often, firmware
shouldn't. Everything hard happens in CI where it can be fixed without a USB
cable.

## Commands

```bash
# Tests — run these before committing anything
make -C tests test              # C verdict engine, 14 shared cases
python3 backend/test_verdict.py # Python verdict engine, same 14 cases
make -C tests ui                # renders the LCD layout as ASCII, asserts nothing clips
make -C tests golden            # same states -> tests/out/*.ppm for the pixel diff

# Backend
python3 backend/build.py                    # fetch + model -> docs/data.json
python3 backend/build.py --dry-run          # print, touch nothing
RBOB_OVERRIDE=3.25 USDCAD_OVERRIDE=1.41 python3 backend/build.py --dry-run  # offline
python3 backend/backfill.py                 # one-shot seed from Ontario's survey
python3 backend/backtest.py --sweep         # tune thresholds against real history
python3 backend/log_price.py --list         # station ids
python3 backend/log_price.py 1.709 -s beaver  # log a station price
python3 backend/log_price.py 1.799            # log the regional benchmark

# Firmware — note the board options, they are not defaults
FQBN="esp32:esp32:esp32c6:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB"
arduino-cli compile --fqbn "$FQBN" firmware/gasprices
arduino-cli upload -p /dev/cu.usbmodem3101 --fqbn "$FQBN" firmware/gasprices
```

Serial is 115200. `arduino-cli monitor` does **not** reliably catch the boot
banner; reset via DTR/RTS with pyserial instead (see git history for the
snippet).

## Code map

```
firmware/gasprices/
  gasprices.ino     wifi, HTTPS fetch, JSON parse, RTC cache, button, sleep
  verdict.h         the decision engine — pure C, no Arduino deps, host-testable
  ui.h              ST7789 layout, 320×172
  config.h.example  copy to config.h (gitignored — holds the WiFi password)
firmware/i2c_scan/  standalone I2C scanner, for external displays
backend/            stdlib-only Python; no pip install anywhere in CI
  build.py          orchestrator: fetch -> model -> stations -> docs/data.json
  model.py          tax stack, passthrough model, margin calibration
  sources.py        RBOB, FX, Ontario survey
  stations.py       per-station offset model
  verdict.py        Python mirror of verdict.h
  backfill.py       seed history from Ontario's weekly survey
  backtest.py       replay history, sweep thresholds
  stations.csv      station registry (edit freely; ids must stay stable)
  station_prices.csv  observations
  history.csv       regional benchmark series
tests/
  vectors.csv       14 shared cases — the contract between both engines
  test_verdict.cpp  C engine
  test_ui.cpp       renders ui.h to ASCII, asserts nothing leaves the panel
  stubs/            fake Adafruit headers so ui.h compiles on the host
```

## Conventions that matter

**Prices are integers in tenths of a cent per litre.** `$1.489/L` is `1489`.
Pump prices have exactly three decimals so this is lossless, and it avoids float
comparisons on a chip with no hardware FPU. `gp_fmt_price` / `gp_fmt_cents`
format them.

**The verdict engine exists twice on purpose** — `verdict.h` (C, on-device) and
`verdict.py` (backend/CI). The device keeps answering when the backend is down;
the backend can replay a month of history in a second. They are pinned together
by `tests/vectors.csv`, which both test suites read. **If you change a rule,
change both, or CI fails.** `vectors.csv` assumes the default config.

**Never publish a bad file.** `build.py` exits non-zero and leaves the previous
`docs/data.json` in place rather than publishing a wrong number. A day-old price
is fine — the firmware knows how to say `STALE`. A wrong price is not.

**Windows are date-based, never row-based.** Backfilled survey rows are weekly
while live rows are daily, so "last N rows" silently means five months in one
case and three weeks in the other. This caused two real bugs (margin window,
`window_lo`/`window_hi`). Slice by date.

**Calibration never consumes the model's own output.** Only logged prices and
the Ontario survey feed margin/offset calibration, or the margin would be
fitting to a price the margin produced.

**Reason strings are capped at 21 characters** (`GP_REASON_MAX_CHARS`) — one
panel line. The test fails if one grows past it.

## Findings that shaped the design

These were measured, not assumed, and are easy to accidentally undo.

**Where you fill up beats when, by ~5×.** The Richmond Hill spread on
2026-07-27 was 9 ¢/L (170.9 at Beaver Creek vs 179.9 on Yonge) while the timing
engine was arguing over 1.7 ¢ across three days. The device therefore prices
everything at the cheapest tracked station. The timing verdicts
(`WAIT`/`EXPENSIVE`/`FILL NOW`) are the *minor* axis — keep them, don't
foreground them.

**The retail margin is not a constant.** It ran ~27 ¢/L in late 2025 and fell to
~15 ¢/L by mid-2026. Calibration uses a 90-day trailing window; a full-year
median runs the model ~11 ¢/L high.

**Rockets-and-feathers was wrong here.** The original 0.60-up/0.25-down
asymmetry was an artifact: a *trailing* margin lags a drifting one, biasing the
target +1.74 ¢/L and manufacturing upward gaps that never existed. Against a
centered margin the asymmetry vanishes and full convergence wins both ways.
Rates are now symmetric at 0.50/day.

**The timing model's real edge is small** — about 0.5 ¢/L over "assume no move"
at a 7-day horizon. When today's price already sits at equilibrium, `pred[]`
comes back flat and the verdict runs purely on the level signal. That is the
honest output, not a bug.

**Station offsets are stable even though prices are not.** What separates
stations is structural (brand, volume, competition) and holds for weeks; what
moves them together is regional and already modelled. So a station is one
number, `median(observed - benchmark)` over 180 days, and one logged price
roughly calibrates it. This is what makes manual logging viable for 17 stations.

## Gotchas

**`USB CDC On Boot` defaults to Disabled.** On this board the USB port *is* the
serial port, so the Serial Monitor stays completely blank while the sketch runs
fine — indistinguishable from a dead board. Always compile with `CDCOnBoot=cdc`.

**Flash is 8 MB, the IDE assumes 4.** Use `FlashSize=8M,PartitionScheme=default_8MB`
or the app partition is 1.2 MB and the sketch sits at 89% of it.

**GPIO6/7 are the LCD's SPI lines**, not I2C, and are not broken out. The
headers only expose GPIO 0-5, 9, 12, 13, 18, 19, 20, 23. Scanning 6/7 for I2C
reports ~120 phantom devices.

**Deep sleep does not keep the screen alive.** The ST7789 holds its own GRAM,
but the backlight is on GPIO22, which is not an RTC pin on the C6 — it drops on
sleep entry and the panel goes dark. Long battery life, not a persistent
display.

**The RBOB ticker is `RB=F`, not `RBOB=F`** (which 404s), and Yahoo's `range`
wants a period string (`3mo`), not a day count.

**GasBuddy cannot be automated.** `robots.txt` permits `/gasprices/`, but
Cloudflare 403s every automated request — GitHub Actions and `curl` both. Do not
build fingerprint-spoofing or headless-stealth workarounds; it is detection
evasion, brittle, and against their ToS. Station seed prices came from a one-off
look in a real browser; manual logging maintains them.

**The Action commits to `main`.** Rebase rather than clobber when it has pushed
while you were working; resolve `docs/data.json` conflicts by regenerating.

## State

Working end-to-end and flashed. The device shows the cheapest station, its
savings vs your usual one, a `CHEAPEST` tag, and cycles all 15 stations on a
short button press (800 ms → tank state, 2.5 s → force refresh).

Not done: station offsets currently rest on a **single seeded observation each**
(`confident: false`, the `?` on screen). Two stations — `esso-16th-ave-3010` and
`esso-elgin-mills-1485` — have no offset at all and need one logged price.

The browser preview and layout editor from [`PREVIEW_PLAN.md`](PREVIEW_PLAN.md)
is **built**, all five phases, under `preview/` (the plan says `web/`; the
directory on disk is `preview/`). See [`preview/README.md`](preview/README.md);
the plan file's header records where the build diverged from it.

Two rules that file enforces and that are easy to break by accident:

- **`firmware/gasprices/layout.h` is generated** from `layout.json` by
  `preview/tools/gen_layout.py`. Never hand-edit it; `--check` runs in CI.
- **`preview/render.js` must stay in step with `ui.h`.**
  `make -C tests golden && node preview/tests/compare_render.mjs` requires the
  two to agree on every pixel of all 15 frames. Run it after touching either, or
  after touching a drawing primitive in `tests/stubs/Adafruit_ST7789.h`.

Columns 0–10 of `vectors.csv` are the engine contract and are indexed by
position; 11–17 are display-only. Append, never insert.

See [`README.md`](README.md) for the decision logic and data model,
[`INSTALL.md`](INSTALL.md) for flashing.
