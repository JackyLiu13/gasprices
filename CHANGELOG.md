# Changelog

One entry per update, newest first. Every change to this project gets a version
number and a line saying what changed — see the rule in
[`CLAUDE.md`](CLAUDE.md#versioning).

Versions are plain integers. They are not semver: this is one device, one feed
and one person, so "did the number go up" is the only question a version needs
to answer here.

---

## v0 — 2026-07-28 — baseline

The state at which versioning starts, as of `2ca90f2` plus this file. Nothing
below is new work — it is the inventory later entries are a delta from. The
next change to the project is v1.

**Device.** Waveshare ESP32-C6-LCD-1.47 fetching one ~3 KB JSON file twice a
day and deciding locally. Shows the cheapest tracked station, its saving versus
the home station, a `CHEAPEST` tag, and cycles all 17 stations on a short button
press. Working and flashed.

**Backend.** stdlib-only Python. RBOB futures + USD/CAD + Ontario's weekly
retail survey → tax stack → passthrough model → `docs/data.json`, served from
GitHub Pages. Fails closed: a bad run leaves the previous file in place rather
than publishing a wrong price.

**The verdict engine exists three times** — `verdict.h` (C, on-device),
`verdict.py`, `preview/verdict.js` — pinned together by the 14 shared cases in
`tests/vectors.csv`.

**Browser preview and layout editor** (`preview/`), all five phases of
`PREVIEW_PLAN.md`. `layout.h` is generated from `layout.json`; `render.js` is
pixel-identical to `ui.h` across all 15 golden frames, both checked in CI.

**Analytics dashboard** (`dashboard/`), all six phases of `DASHBOARD.md`.
Margin series, forecast error, dispersion, over a derived SQLite read model.
`forecasts.csv` records what the model actually said and is never reconstructed.

**AWS pipeline** (`template.yaml`, `backend/lambda_handlers.py`). Scheduled
build plus a Function URL for logging a price, both on Lambda in `ca-central-1`,
committing through the GitHub API so git stays the store and the firmware never
notices the backend moved. ~4 s round trip, $0/month.

**Known state, measured not assumed:**

- All 17 stations have exactly **one** observation each, so every offset is
  `confident: false`. A second price at any station is the highest-value change
  available.
- `history.csv` is 65 rows over 422 days and **56 are Mondays**, because
  Ontario's survey is weekly. Day-of-week effects and price cycles are not
  weakly supported — they are unmeasurable at this resolution.
- `backtest.py --sweep` holds price flat on 357 of 422 days and oscillates
  ~1 ¢/L between adjacent horizons. Its printed thresholds are noise; the
  config stays at `save_threshold=15`, `horizon=5`.
- Where you fill up beats when by roughly 5× (9 ¢/L spread vs ~0.5–0.8 ¢/L
  timing edge).

**Still open:** both schedulers run in parallel — the `schedule:` block in
`.github/workflows/update.yml` has not been removed yet, pending a few days of
comparison against the Lambda.
