# gasprices

A glanceable "should I fill up today?" indicator for Richmond Hill, ON.
An ESP32-C6 pulls one small JSON file twice a day, runs a verdict engine, and
shows the answer on an SSD1306 plus the onboard RGB LED.

```
GitHub Action (cron, 2x/day)          ESP32-C6
  RBOB futures + USD/CAD                 HTTP GET data.json
  -> Ontario tax stack                   -> verdict engine (verdict.h)
  -> forward price model         --->    -> OLED + RGB LED
  -> docs/data.json (~400 bytes)         -> deep sleep
```

The split matters: scraping and modelling change often, firmware shouldn't.
Everything hard happens in CI, where you can fix it without a USB cable.

## The decision logic

Two signals, both cheap to compute on-device.

**Level** — how cheap is today against the last 30 days?

```
levelPct = (today - window_lo) / (window_hi - window_lo)   // 0 = cheapest
```

**Direction** — is a cheaper day coming?

```
tomorrowJump = pred[0] - today
daysToWait   = first d where pred[d] <= today - threshold   (0 = don't wait)
save         = today - min(pred[0..k])
```

Evaluated top-down, first match wins ([verdict.h](firmware/gasprices/verdict.h)):

| # | Condition | Verdict | LED |
|---|-----------|---------|-----|
| 0 | data older than 36 h | `STALE` | dim purple |
| 1 | tank is LOW | `FILL_NOW` | green |
| 2 | `tomorrowJump >= threshold` and `levelPct <= 60` | `FILL_NOW` | green |
| 3 | `daysToWait > 0` | `WAIT` | red |
| 4 | `levelPct <= 20` | `GREAT` | green |
| 5 | `levelPct >= 80` | `EXPENSIVE` | red |
| 6 | otherwise | `NEUTRAL` | amber |

`threshold` defaults to 1.5 ¢/L — below that, a special trip costs more in
detour than it saves. The tank state scales it: a full tank needs 3.0 ¢/L to
bother, and an empty one skips the question entirely, because running dry beats
saving three cents.

Two consequences worth knowing, both deliberate and both covered by tests:

- Rule 2 beats rule 3. If tomorrow jumps 2.3 ¢ but day 3 is cheaper, you still
  fill today — you have to drive in the meantime.
- Rule 2's `levelPct <= 60` guard means a jump on top of an already-high price
  gives `EXPENSIVE`, not `FILL_NOW`. Chasing a rising market is how you end up
  paying the top.

**Prices are integers everywhere** — tenths of a cent per litre, so `$1.489/L`
is `1489`. Pump prices have exactly three decimals, so this is lossless, and it
sidesteps float comparisons on a chip with no hardware FPU.

### The engine exists twice, on purpose

[`verdict.h`](firmware/gasprices/verdict.h) (C, runs on the device) and
[`verdict.py`](backend/verdict.py) (runs in CI) implement the same rules. The
device keeps answering when the backend is down; the backend lets you replay a
month of history in a second instead of reflashing. They're pinned together by
[`tests/vectors.csv`](tests/vectors.csv), which both test suites read — if they
ever drift, CI fails.

```bash
make -C tests test          # C engine
python3 backend/test_verdict.py   # Python engine
make -C tests ui            # renders the real OLED layout in your terminal
```

`make -C tests ui` compiles `ui.h` against stub display headers and asserts
nothing is drawn off the 128×64 panel:

```
== cheaper_in_two_days (tank=HALF)
    +---------------------+
    |RICHMOND HILL      2h|
    |$$11..552200  LVL 59%|
    |$$11..552200  2d 3.0c|
    |########WAIT#########|
    |Save 3.0c in 2 days  |
    |.  .  .              |
    +---------------------+
```

(Doubled glyphs are size-2 text. Reason strings are hard-capped at 21
characters — one panel line — and the test fails if one grows past it.)

## The data

| Input | Source | Cadence |
|-------|--------|---------|
| RBOB gasoline futures | Yahoo `RB=F` (unofficial, no key) | daily |
| USD/CAD | frankfurter.app, er-api.com fallback | daily |
| GTA pump price | [Ontario Fuels Price Survey](https://data.ontario.ca/en/dataset/fuels-price-survey-information) (official open data) | weekly, Mondays |
| Your pump price | `log_price.py` | whenever you fill up |

The GTA number comes from Ontario's official weekly survey rather than
GasBuddy: it's a published open dataset under the Open Government Licence, goes
back to 1990, needs no key, and has no bot-protection to fight. Richmond Hill
sits on Yonge Street — roughly the line the survey uses to split Toronto East
from Toronto West — so `sources.py` averages the two markets.

The catch is cadence. It updates Mondays, so it anchors the *level* weekly
while the RBOB model carries the *direction* daily. `build.py` rolls the newest
survey point forward to today through the same passthrough model rather than
pretending a four-day-old number is today's price.

Wholesale → pump is a fixed stack ([model.py](backend/model.py)):

```
wholesale = RBOB_usd_per_gal * USDCAD / 3.785411784
retail    = (wholesale + margin + 0.10 fed + 0.09 ON + carbon) * 1.13 HST
```

Verify those tax constants before trusting output — rates move, and the federal
consumer carbon charge on gasoline was removed in April 2025.

`margin` is the one number you don't guess, and **it is not a constant**.
Measured against a year of the Ontario survey, the GTA margin ran ~27 ¢/L in
late 2025 and fell to ~15 ¢/L by mid-2026:

```
2025-07-28 .. 2025-10-20   median 0.2743
2025-10-27 .. 2026-01-19   median 0.2821
2026-01-26 .. 2026-04-20   median 0.2596
2026-04-27 .. 2026-07-20   median 0.1515
```

So calibration uses a **90-day trailing window**, and it's time-based rather
than row-based on purpose: backfilled survey rows are weekly while live rows are
daily, so "last 21 rows" would mean five months in one case and three weeks in
the other. Using the full-year median instead would run the model ~11 ¢/L high.
On the current data the 90-day window fits the survey to a median error of
1.87 ¢/L, against 3.40 ¢/L for the full-span median.

Log what you actually pay — your own station beats a regional average:

```bash
python3 backend/log_price.py 1.819
```

Priority is always: your logged price → Ontario survey → our model.
Calibration never uses the model's own output, or the margin would be fitting
to a price the margin produced.

The forward model is deliberately boring: today's pump price hasn't finished
absorbing where wholesale already is, so each day closes a fixed fraction of the
gap toward equilibrium.

That fraction started as a rockets-and-feathers asymmetry (60 % up, 25 % down)
and **the data rejected it** — for an instructive reason. Measured against a
*trailing* margin, upward gaps appeared to close only 40 % per week. But a
trailing margin lags a drifting one, biasing the target **+1.74 ¢/L high** and
manufacturing upward gaps that were never real. Against a centered, unbiased
margin the asymmetry disappears entirely and full convergence wins both ways:

| 7-day weight | MAE up | MAE down |
|---|---|---|
| 0.0 (price won't move) | 4.18 ¢/L | 3.76 ¢/L |
| 1.0 (fully at equilibrium) | **3.45 ¢/L** | **3.55 ¢/L** |

So the rates are now symmetric at 0.50/day. Weekly data can't resolve the daily
rate further — anything above ~0.35/day looks complete after 7 days.

**Be honest about what this buys you.** Beating "assume no move" by ~0.5 ¢/L at a
7-day horizon is a weak edge, and when today's price already sits at
equilibrium, `pred[]` comes back flat and carries no directional information at
all. When that happens `daysToWait` never fires and the verdict is driven
entirely by the level signal — which is the honest answer, not a bug. Real
forward skill would need the RBOB futures curve, which this doesn't model.

### JSON contract

```json
{
  "today_cad": 1489,
  "pred": [1512, 1499, 1471, 1468, 1470],
  "window_lo": 1441, "window_hi": 1573,
  "hist": [1520, 1515, "... 28 days for the sparkline"],
  "epoch": 1784920505,
  "verdict_hint": "FILL_NOW"
}
```

`epoch` (not the ISO string) drives the staleness check — no date parsing on the
device. `verdict_hint` is for you; the firmware computes its own.

## Setup

**1. Backend.** Push to GitHub, enable Pages on `main` / `/docs`. The Action
runs at 05:30 and 16:30 Toronto time and commits `docs/data.json`.

Seed the history first, or the window is empty, level% is meaningless, and the
device says NEUTRAL for a month while it learns:

```bash
python3 backend/backfill.py          # a year of real GTA prices, one shot
python3 backend/build.py --dry-run
RBOB_OVERRIDE=3.25 USDCAD_OVERRIDE=1.41 python3 backend/build.py --dry-run  # offline
```

`backfill.py` deliberately does not interpolate weekly points into fake daily
ones — linear interpolation between two weekly prices can never move
`window_lo`/`window_hi`, so it would add rows without adding information. Real
daily rows accumulate from `build.py` going forward.

**2. Firmware.** Arduino IDE, ESP32 core ≥ 3.0 (C6 support landed there).
Board: *ESP32C6 Dev Module*. Libraries: ArduinoJson v7, Adafruit SSD1306,
Adafruit GFX. Then:

```bash
cp firmware/gasprices/config.h.example firmware/gasprices/config.h
```

Fill in WiFi and your Pages URL. `config.h` is gitignored.

**3. Wiring.** SSD1306 over I²C — VCC→3V3, GND→GND, SDA→GPIO6, SCL→GPIO7
(check your board's silkscreen; the C6 can route I²C anywhere). The RGB LED is
onboard. The BOOT button (GPIO9) cycles the tank state; hold it >1 s to force a
refresh.

Set `USE_DEEP_SLEEP 1` for battery. The SSD1306 holds its framebuffer while the
ESP32 sleeps, so the last verdict stays on screen the whole time — but if it's
going to sit on one image for hours, watch for burn-in.

## Calibrating

The build order that actually works:

1. **Run the backend for a week.** Log every fill-up. Compare `retail_model`
   against real pump prices and confirm the margin converges.
2. **Flash the firmware** once the JSON looks sane. LED-only is fine at first;
   the sketch runs without an OLED.
3. **After ~30 days**, tune against your own history:

```bash
python3 backend/backtest.py            # would following it have beaten filling when low?
python3 backend/backtest.py --sweep    # grid over save_threshold x horizon
python3 backend/backtest.py --oracle   # perfect foresight = ceiling on the rules
```

`--oracle` is the diagnostic that matters. It feeds the engine the actual future
instead of predictions. If oracle barely beats the baseline, better forecasting
won't help you — the *rules* are wrong. If oracle wins big but the live run
doesn't, the *model* is wrong. Fix the one the data points at.

Whatever the sweep picks, change it in **both** `Config` in
[verdict.py](backend/verdict.py) and `gp_default_config()` in
[verdict.h](firmware/gasprices/verdict.h) — `tests/vectors.csv` assumes the
defaults, so CI will tell you if you only did one.

## Layout

```
firmware/gasprices/
  gasprices.ino     wifi, fetch, sleep scheduling, button
  verdict.h         the decision engine (no Arduino deps — compiles on host)
  ui.h              SSD1306 layout
  config.h.example  copy to config.h
backend/
  build.py          orchestrator: fetch -> model -> docs/data.json
  backfill.py       one-shot seed from Ontario's weekly survey
  model.py          tax stack, passthrough model, margin calibration
  sources.py        RBOB, FX, Ontario survey, local price (stdlib only)
  verdict.py        Python mirror of verdict.h
  backtest.py       replay history, sweep thresholds
  log_price.py      record what you actually paid
tests/
  vectors.csv       shared truth for both engines
  test_verdict.cpp  C engine
  test_ui.cpp       renders the OLED layout to your terminal
  stubs/            fake Adafruit headers for host builds
```

## Known rough edges

- Yahoo's chart endpoint is unofficial and unversioned. If it starts failing,
  drop a replacement behind `sources.rbob_history()` — EIA has a free keyed API
  with the same shape. `build.py` exits non-zero rather than publish a bad
  number, so a broken source leaves the last good file in place.
- `build.py` applies today's FX across its one-month RBOB window (~1 % drift,
  under a cent per litre). `backfill.py` uses real historical FX, because over a
  year that shortcut would cost several cents.
- The Ontario survey is a regional average of two large markets, so it misses
  station-level spread and intra-week spikes. Until enough daily rows
  accumulate, the sparkline is coarse and `window_lo`/`window_hi` understate
  true daily volatility. Logging your own fills is the fix.
- GitHub disables scheduled workflows after 60 days of repo inactivity.
- The local price is still the weak link. Manual logging works and needs no
  infrastructure; if you'd rather scrape a GTA next-day tracker, that parser
  goes in `sources.local_retail_hint()` and nothing else changes.
