# Plan: analytics dashboard for the data, the backend, and the prediction system

> Status: **built.** Phases A–F all landed; the UI lives in
> [`dashboard/`](dashboard/README.md) as a self-contained sub-project, with the
> backend groundwork in `backend/`. This file is kept as the design record.
> Where the build differs from the plan:
>
> - **The forecast log did not have to start empty.** The plan said accuracy
>   charts would say "nothing scored yet" for a week. But every commit of
>   `docs/data.json` *is* a forecast the model published on a known date, and
>   `meta.station_shift_cad_l` makes the conversion back to benchmark space
>   exact (schema 1 predates stations, so its shift is 0). `backfill_forecasts.py`
>   recovers 20 of them from git. This is not the forbidden self-referential
>   backfill — it re-derives nothing, it reads what was actually said.
> - **`backtest.py` needed a second, larger fix.** The plan caught that
>   `load_series()` ignored `retail_survey`. Fixing that made it run — and it
>   reported the engine and the baseline as *exactly identical*, +0.00 ¢/L. The
>   cause was the same row-vs-date bug one level down: `simulate()` burns a
>   day's fuel per **row**, and 60 of 64 rows are weekly. It was modelling a car
>   that drove 2,500 km a week through a "30-day" window covering seven months.
>   `to_daily()` puts it on a real calendar grid; the measured edge is +0.78 ¢/L
>   against a +1.15 ¢/L oracle ceiling.
> - **The margin is calibrated as-of each day, not once per series.** The first
>   cut calibrated over the whole history and handed every simulated day a
>   margin derived partly from its own future. `margins_asof()` fixes it.
> - **A `/api/replay` endpoint appeared.** The plan had the browser replaying
>   verdicts, which is right — but the *inputs* need `model.py`, which is not
>   ported to JS. The split is: Python supplies today/pred/window per day, and
>   `preview/verdict.js` — the engine the firmware's pixel test is held to —
>   decides the verdict.
> - **The tax stack turned up in SQL.** `v_margin` needed HST and the excise
>   total, which would have been a fourth copy. They are interpolated from
>   `model.py` at build time instead.
> - **Phase F's variants landed in Phase A**, because `forecasts.csv` needed a
>   `variant` column and adding it later would have meant a schema migration for
>   a file that had just been created.
> - **A UTF-8 fix was needed.** `SimpleHTTPRequestHandler` sends `text/html`
>   with no charset, so every em dash rendered as mojibake. `guess_type` now
>   declares it. `preview/server.py` has the same latent bug.

## Context

The device works. What was missing was any way to *see whether the model is any
good*. Judging the prediction engine meant reading `history.csv` in a text
editor, running `backtest.py` (which bailed before simulating anything), and
squinting at `git log -p docs/data.json` — the only existing time series of
model diagnostics.

Three holes, all confirmed by reading the code before writing any:

1. **Forecasts were never recorded.** `history.csv:retail_model` is that day's
   *level*, not a forecast. `build.py` computed `pred[0..4]` every run and threw
   it away. "How wrong were we at a 3-day horizon?" had no answer on disk. You
   cannot improve what you have never scored.
2. **The margin series was discarded.** `calibrate_margin` computes an implied
   margin per observation and keeps only the median. The 27 → 15 ¢/L drift that
   `CLAUDE.md` calls the most important calibration fact existed nowhere as a
   series.
3. **`backtest.py` could not run.** `load_series()` ignored `retail_survey`,
   yielding ~4 usable rows against `need = 42`. 61 survey rows sat unused, and
   the failure looked like "not enough history yet".

**Goal:** a local dashboard that makes the data, the backend's intermediates,
and the prediction system's *accuracy* visible; lets prices be logged and a
rebuild triggered from the page; and can hand a complete context bundle to an AI
agent. Plus the storage and scoring groundwork so more stations, more sources
and competing model variants can be added without the analysis rotting.

## What existed before this (verified, not assumed)

| Fact | Detail |
|---|---|
| Storage | 3 CSVs, no database. `history.csv` 64 rows, `station_prices.csv` 15, `stations.csv` 17 |
| History shape | Rows 1–60 **weekly**, 61–64 **daily**. `retail_survey` 61 filled, `retail_actual` **2**, `retail_model` **4** |
| Stations | 15 of 17 priced, all at `n=1`; two with no offset at all |
| Critical trap | `today_cad`, `pred`, `window_lo/hi`, `hist` in `data.json` are all **station-shifted** |
| Verdict engines | Three, pinned by `tests/vectors.csv`, incl. `preview/verdict.js` |
| Dependencies | **Zero**, in CI and everywhere else, on purpose |
| Schema duplication | `FIELDS` copy-pasted in `build.py`, `backfill.py`, `log_price.py` |

---

## Architecture

```
backend/history.csv        ─┐
backend/station_prices.csv ─┼─► backend/db.py ──► backend/analytics.db (gitignored, derived)
backend/stations.csv       ─┤                             │
backend/forecasts.csv      ─┘                             │
   ▲ appended by build.py, one row per variant per horizon│
   ▲ seeded once by backfill_forecasts.py from git history│
                                                          │
                                    dashboard/server.py ──┤ GET  /api/{overview,series,stations,
                                    (stdlib, localhost)   │       accuracy,sweep,replay,raw}
                                                          │ POST /api/price   → log_price.py
                                                          │ POST /api/refresh → build.py
                                                          │
                                    dashboard/index.html ─┘
                                      charts.js   vanilla SVG, every x-axis a date scale
                                      app.js      panel wiring, feature-detects the API
                                      bundle.js   "Copy for AI"
                                      ../preview/verdict.js   ← the device engine, not a copy
```

**CSVs stay canonical.** They are what git diffs, what the Action commits, and
what merges. The SQLite file is a cache: rebuilt by one command, gitignored,
deletable without loss. Nothing writes to it.

**No chart library.** SVG generated from data, matching the repo's
zero-dependency rule and `preview/render.js`'s precedent of writing the drawing
code rather than importing it.

---

## Phase A — make the model's work observable

- **`backend/schema.py`** — one definition of each CSV header, replacing the
  three copies of `FIELDS`.
- **`backend/forecasts.csv`** + **`forecast_log.py`** — every forward price the
  model commits to, in **regional benchmark space**, upserted on
  `(made_on, target_date, variant)`. Written by `build.py` *before* the station
  rebasing, because a forecast recorded in cheapest-station space changes
  meaning when a different station takes the lead.
- **`backend/analytics.py`** — pure, I/O-free derivations: `margin_series`,
  `rolling_margin` (mirroring `calibrate_margin`'s 90-day median exactly, and
  emitting nothing where the real code would use its fallback), `target_series`,
  `forecast_error` (against a no-move baseline, on the identical sample), and
  `station_dispersion` (median + MAD).
- **`backend/backfill_forecasts.py`** — one-shot recovery of published forecasts
  from git.
- **`backtest.py`** — survey fallback, `to_daily()` calendar grid,
  `margins_asof()`, and an opt-in per-day `trace`.
- **`log_price.py`** — future dates refused, overwrites announced.

**Boundary:** the forecast log records what was said; it never reconstructs it.

## Phase B — derived SQLite read model

`backend/db.py` builds `analytics.db` from the four CSVs with real date columns,
indexes, and views: `v_benchmark` (the `best_retail` coalesce, defined once in
SQL), `v_observed`, `v_margin`, `v_station_priced`, `v_forecast_scored`.
`--check` compares source mtimes; `ensure()` rebuilds on demand.

## Phase C — the dashboard

Seven panels, all in **regional benchmark space**, all on **date** axes:
price/target, margin scatter + rolling band, forecast accuracy vs no-move,
verdict replay, station ladder with MAD, threshold sweep heatmap, coverage strip.

The palette is the ST7789's, converted RGB565 → RGB888 the same way
`preview/render.js` does it, and `verdictColor()` mirrors `uiVerdictColor()` in
`ui.h` — so a green here means what a green means on the device.

**Sparse-data honesty:** every panel states its own `n` and says plainly when
the sample cannot support a conclusion.

## Phase D — write actions

`POST /api/price` and `POST /api/refresh` shell the existing CLIs rather than
reimplementing their file handling. Origin-checked, localhost-bound, fixed argv.
The write controls feature-detect: opened as a static file they hide themselves
and the page runs read-only off `docs/data.json`.

**Committing stays manual.** The Action publishes; a page that can push to
`main` is a page that can publish a wrong price.

## Phase E — Copy for AI, and `AGENT_NOTES.md`

`dashboard/bundle.js` produces a deterministic, size-bounded markdown bundle:
preamble pointing at `AGENT_NOTES.md`, an explicit **units and station-shift**
section, current state, engine config, the accuracy table, the station ladder,
and the raw CSVs date-sliced to the selected window.

[`AGENT_NOTES.md`](AGENT_NOTES.md) is the interpretation layer `CLAUDE.md`
deliberately doesn't carry: what "improve the prediction" means when the two
axes are worth 5× different amounts, what is load-bearing, and what to report
back.

## Phase F — scoring competing models

`model.VARIANTS` holds named predictors sharing `predict()`'s signature:
`passthrough` (shipped), `hold` (the no-move floor), `slow`, `fast`, and
`asymmetric` — the rockets-and-feathers model this project measured and
rejected, kept scored so the rejection stays evidence-based. `build.py` logs all
of them; only `DEFAULT_VARIANT` reaches `docs/data.json`.

**Boundary:** this does not make predictions near-perfect. The ceiling is set by
how much retail movement is predictable from wholesale, and the sweep panel now
reports what fraction of it the rules already capture (~68%). What this buys is
that every proposed improvement is falsifiable.

---

## Files

```
DASHBOARD.md                    this file
AGENT_NOTES.md                  NEW  how to interpret requests on this project

backend/schema.py               NEW  one definition per CSV header
backend/analytics.py            NEW  pure derivations
backend/forecast_log.py         NEW  read/write forecasts.csv
backend/db.py                   NEW  CSVs -> analytics.db
backend/backfill_forecasts.py   NEW  one-shot recovery from git history
backend/test_analytics.py       NEW  hand-checked fixtures
backend/forecasts.csv           NEW  the forecast log
backend/analytics.db            GENERATED, gitignored
backend/build.py                edit: append forecasts (additive), import FIELDS
backend/backtest.py             edit: survey fallback, daily grid, as-of margin, trace
backend/log_price.py            edit: future-date refusal, overwrite notice
backend/model.py                edit: VARIANTS registry
backend/backfill.py             edit: import FIELDS

dashboard/README.md             NEW
dashboard/server.py             NEW  stdlib, localhost, read API + 2 writes
dashboard/index.html            NEW  ST7789 palette
dashboard/charts.js             NEW  SVG primitives, date scales only
dashboard/app.js                NEW  panel wiring
dashboard/bundle.js             NEW  Copy for AI
                                imports ../preview/verdict.js — no fourth engine copy

.github/workflows/tests.yml     edit: analytics tests + db --check
.gitignore                      edit: backend/analytics.db
```

## Verification

1. `python3 backend/test_verdict.py` and `make -C tests test` — **14 passed**.
   The engine contract is untouched by all of this.
2. `python3 backend/test_analytics.py` — the pure derivations, including the
   weekly-then-daily spacing case that breaks row-based windows.
3. `python3 backend/db.py && python3 backend/db.py --check` — builds, then
   reports in sync. The SQL `v_margin` was cross-checked against
   `analytics.margin_series` over 55 rows: **worst disagreement 0.0**.
4. `python3 backend/build.py --dry-run` — payload **byte-identical** to the
   pre-change version apart from the run timestamp. The forecast log is
   additive; if `data.json` moves, a write leaked into the published path.
5. `python3 backend/backtest.py` — simulates instead of bailing:
   **+0.78 ¢/L** model, **+1.15 ¢/L** oracle, 421 days.
6. `python3 dashboard/server.py`, open the printed URL — every panel renders,
   the coverage strip shows the real sparsity, the station note independently
   names the two offset-less stations.
7. Log a price from the page, confirm `source=logged` in
   `backend/station_prices.csv`, confirm the ladder updates, then `git diff`.
   Cross-origin writes and future dates are both refused.
8. Open the page without the server — write controls absent, read-only panels
   still render from `docs/data.json`.
9. Copy for AI, paste into a fresh session, ask about the margin drift.
10. `make -C tests golden && node preview/tests/compare_render.mjs` — **15
    frames, 0 differing pixels.** Nothing here touches the panel, and that is
    provable.

## Sequencing

**A was the foundation** and landed first, so the forecast log starts
accumulating immediately — every day of delay is accuracy data you don't get
back. B → C → D is the dashboard proper. E is small and landed with C. F only
pays off once the log has depth.

The riskiest edit was `build.py`, because it must never publish a wrong number.
Its change is strictly additive — one file appended after the payload is already
built — and Verification 4 proves it by requiring byte-identical output.
