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

| Input | Source | Difficulty |
|-------|--------|-----------|
| RBOB gasoline futures | Yahoo `RB=F` (unofficial, no key) | easy |
| USD/CAD | frankfurter.app, er-api.com fallback | easy |
| Local pump price | **you** | the actual problem |

Wholesale → pump is a fixed stack ([model.py](backend/model.py)):

```
wholesale = RBOB_usd_per_gal * USDCAD / 3.785411784
retail    = (wholesale + margin + 0.10 fed + 0.09 ON + carbon) * 1.13 HST
```

Verify those tax constants before trusting output — rates move, and the federal
consumer carbon charge on gasoline was removed in April 2025.

`margin` is the one number you don't guess. Log what you actually pay:

```bash
python3 backend/log_price.py 1.489
```

After 5 observations `calibrate_margin()` takes the median implied margin from
your own receipts and the guessed constant stops mattering. This is also what
anchors the *level* — the model gives direction for free, but only your logged
prices tell it where the pump actually sits.

The forward model is deliberately boring: today's pump price hasn't finished
absorbing where wholesale already is, so each day closes a fraction of the gap
toward equilibrium — 60 % on the way up, 25 % on the way down. Rockets and
feathers. No futures curve needed, and it answers the only question the engine
asks: is a cheaper day coming, and how soon?

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
runs at 05:30 and 16:30 Toronto time and commits `docs/data.json`. Test locally:

```bash
python3 backend/build.py --dry-run
RBOB_OVERRIDE=3.25 USDCAD_OVERRIDE=1.41 python3 backend/build.py --dry-run  # offline
```

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
  model.py          tax stack, passthrough model, margin calibration
  sources.py        RBOB, FX, local price (stdlib only)
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
- FX is applied at today's rate across the whole RBOB history. Over a month
  USD/CAD drifts ~1 %, well under a cent per litre.
- GitHub disables scheduled workflows after 60 days of repo inactivity.
- The local price is still the weak link. Manual logging works and needs no
  infrastructure; if you'd rather scrape a GTA next-day tracker, that parser
  goes in `sources.local_retail_hint()` and nothing else changes.
