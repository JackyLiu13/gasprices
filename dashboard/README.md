# dashboard — see whether the model is any good

```bash
python3 dashboard/server.py     # then open the URL it prints
```

No `npm install`, no build step, no framework, no `node_modules`. Vanilla ES
modules the browser loads directly and one stdlib Python server, matching the
rest of the repo's zero-dependency rule.

## Why it exists

The device works. Judging the *model* meant reading `history.csv` in a text
editor, running `backtest.py` (which exited before simulating anything), and
reading `git log -p docs/data.json` — which was, genuinely, the only time series
of model diagnostics that existed.

The deeper problem was that the interesting numbers were computed and discarded.
`calibrate_margin` derives an implied margin per observation and keeps only the
median. `build.py` computes a five-day forward curve every run and keeps none of
it, so "how wrong were we at a 3-day horizon?" had no answer anywhere on disk.

You cannot improve what you have never scored. So the backend now writes
`backend/forecasts.csv` — every forward price it commits to — and this page
scores it.

## Why you can trust it

```
backend/*.csv ──► backend/db.py ──► analytics.db ──► /api/* ──► the panels
                        │                                          ▲
backend/analytics.py ───┘  pure derivations, fixtures in           │
                           backend/test_analytics.py               │
                                                                   │
preview/verdict.js ────────────────────────────────────────────────┘
  the same engine module the firmware's pixel test is held to
```

- **The verdict replay runs the device's engine**, not a reimplementation.
  `preview/verdict.js` is pinned to `tests/vectors.csv` alongside `verdict.h`
  and `verdict.py`. A dashboard that agreed with the backend while the device
  did something else would defeat the purpose.
- **The SQL and the Python agree.** `v_margin` in `db.py` was cross-checked
  against `analytics.margin_series` across all 55 eligible rows — worst
  disagreement 0.0. The tax rates in the view are interpolated from `model.py`
  at build time, not typed in, so they cannot drift.
- **The colours are the panel's.** The palette is `ui.h`'s RGB565 constants
  converted the same way `preview/render.js` converts them, and `verdictColor()`
  mirrors `uiVerdictColor()`. Green here means what green means on the device.
- **Every x-axis is a real date scale.** `history.csv` is weekly for its first
  ~60 rows and daily after; plotting by index would draw seven months and three
  weeks at the same width. That mistake has caused two real bugs in this repo.

## What it does not model

- **It is not the device.** The panel preview lives in `preview/`; this shows the
  model behind it.
- **It cannot make a small sample bigger.** With two logged pump prices and most
  stations at `n=1`, the honest output is mostly "here is what little we know."
  Every panel states its own `n` and says when the sample can't carry a
  conclusion. That is the feature, not a placeholder.
- **The backtest holds prices flat between observations.** Interpolating between
  two Monday surveys would invent midweek movement the verdict engine would then
  take credit for predicting. 64 observed days become a 421-day grid, and the
  panel says so.

## The panels

| Panel | Answers |
|---|---|
| Now | what the device is currently saying, and how far the published feed is shifted |
| Price, target and wholesale | is the model tracking, and where does it diverge |
| Retail margin | is the calibration window still right; is the drift still moving |
| Forecast accuracy | does the timing engine beat "assume no move" — the honest scoreboard |
| Verdict replay | would the device have said something silly, and when |
| Station ladder | which station to go log next |
| Threshold sweep | are the config thresholds still right, and how much headroom is left |
| Coverage | how much of everything above is real measurement vs derived |

**Regional benchmark space, everywhere.** `docs/data.json` is rebased to the
cheapest tracked station; these charts undo that. The two are supposed to differ
by exactly `meta.station_shift_cad_l`.

## Copy for AI

Builds a self-contained context bundle — current state, engine config, accuracy
table, station ladder, and the raw CSVs date-sliced to the selected window — with
the two things that make or break an agent's reasoning stated up front: the
**units** (tenths of a cent in the JSON, dollars in the CSVs) and the
**station shift**. Pasting it into a fresh session should need no follow-up file
reads. See [`AGENT_NOTES.md`](../AGENT_NOTES.md).

## Two modes, one page

Served by `dashboard/server.py` you get the full API and the write controls.
Opened as a static file, the `/api/` calls 404, the write section hides itself,
and the page renders read-only from `docs/data.json` — so it can be dropped into
`docs/` for GitHub Pages without a second build.

## Files

| File | |
|---|---|
| `server.py` | stdlib server: read API, two writes, localhost only |
| `index.html` | the page; one inline `<style>`, ST7789 palette |
| `charts.js` | SVG primitives — line, scatter, bars, heatmap, strips |
| `app.js` | panel wiring, feature-detects the API |
| `bundle.js` | Copy for AI |

Files this project reads but does not own:

| File | Owner |
|---|---|
| `../preview/verdict.js` | the preview, and through it the firmware contract |
| `../backend/analytics.py`, `db.py`, `forecast_log.py` | the backend |
| `../docs/data.json` | `build.py` |

## The server writes to your working tree

`POST /api/price` runs `backend/log_price.py`; `POST /api/refresh` runs
`backend/build.py`, which rewrites `backend/history.csv`,
`backend/forecasts.csv` and `docs/data.json`. Both shell the existing CLIs so
validation stays in one place.

It binds to `127.0.0.1`, refuses cross-origin writes, and has exactly two write
routes with fixed argv. **Committing stays manual** — publishing is the GitHub
Action's job, and a page that can push to `main` is a page that can publish a
wrong price. Do not put this on a network.
