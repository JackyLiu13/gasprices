# Changelog

One entry per update, newest first. Every change to this project gets a version
number and a line saying what changed — see the rule in
[`CLAUDE.md`](CLAUDE.md#versioning).

Versions are plain integers. They are not semver: this is one device, one feed
and one person, so "did the number go up" is the only question a version needs
to answer here.

---

## v2 — 2026-07-28 — stations get coordinates, and a dispatched price stops evaporating

**Axis: neither.** Capability, not calibration, and measured as such: offsets
were computed from the registry with and without the new columns, and all **19
stations agreed to 0.0** on offset, `n` and sample count. Nothing in the price
model reads a coordinate, which is the point.

**`stations.csv` gains optional `lat`/`lon`,** and its header moves into
`schema.py` where the other three CSVs already keep theirs — it was the last
one still defined in a script (`add_station.py`). Blank is a first-class value:
a station without coordinates logs exactly as before, so the 19 existing rows
stayed valid and filling them in was incremental rather than a migration.

**15 of 19 located** by `backend/geocode_stations.py`, a one-off against
OpenStreetMap's Nominatim (stdlib `urllib`, no key, never in CI). The four
blanks are three `Hwy 7 E` house numbers Nominatim does not carry and one
intersection; they are left blank and reported rather than approximated.

**The obvious query was silently wrong.** Prefixing the brand — `"Esso, 10579
Yonge St"` — makes Nominatim match the brand as a POI on that street and ignore
the house number: `esso-yonge-10579` and `esso-yonge-12891`, **2.5 km apart**,
both resolved to *the same point*, as did `shell-yonge-8656` and
`shell-yonge-11151`, **5.6 km apart, returned 5 m apart**. Nothing about those
results looks wrong — right city, right street, inside the bounding box. The
plain address resolves all four correctly. A result outside the Richmond
Hill/Markham box, or within 60 m of a station already placed, is now refused
rather than written, because a coordinate that is confidently 400 m wrong does
not fail — it just offers the wrong station at the pump.

The closest genuine pair is **94 m**: `esso-bayview-9999` and
`petrocan-major-mac-695`, on opposite corners of Bayview & Major Mackenzie. GPS
cannot separate those, so a client must confirm the station rather than
auto-pick it.

**`docs/stations.json`** publishes the registry for clients that locate a
station rather than price it. Kept out of `data.json` on measurement: folding
the coordinates in takes the device's feed from 4387 to 5086 bytes (**+16%**)
for a field the firmware never reads, on a file it fetches twice a day.

**The logging endpoint gains a lookup mode.** `{"lat":…, "lon":…}` with no price
writes nothing and returns the nearest located stations with distances;
`{"station":…, "price":…}` still logs exactly as before. This is what makes a
phone Shortcut short — the geo maths lives in `stations.nearest()` where
`test_stations.py` covers it, not in a visual scripting language. It returns a
*list*, never a single answer: standing between the Bayview & Major Mackenzie
pair, both come back at **47 m each**, so auto-picking would be a coin flip.
Standing in Toronto it returns nothing at all, because an empty list prompts you
to choose while a least-wrong guess writes a bad row.

`log_price.resolve()` now accepts a **label** as well as an id or id-substring.
A label is what a person sees — the panel header, the ladder, the list a phone
offers — and making the caller carry an id so it can be translated back into the
label it came from was work with no purpose.

**Fixed: a dispatched price could be logged and then thrown away.** The Action
ran `log_price.py`, then `build.py`, then committed — and `build.py` exits
non-zero rather than publish a wrong number, so an upstream outage at Yahoo
discarded the one input in that run that cannot be re-fetched later: the price
you were standing in front of. It now commits the price and reports the failed
rebuild separately, matching what `lambda_handlers.log_handler` already did.
The push also rebases-and-retries instead of failing on a race with a laptop.

---

## v1 — 2026-07-28 — express logging, and observations get a time

**Axis: station (where).** Measured offset movement: **zero across all 17
stations** — old and new `compute_offsets` were run against the same benchmark
and the same rows and agreed to 1e-10 on every station's offset and count. This
is capability, not calibration. It exists because every station still sits at
`n=1` and the entry form was the thing in the way.

**`station_prices.csv` gains a `time` column** (`HH:MM`, America/Toronto), and
the upsert key becomes `(date, time, station_id)`. Before this, a second price
at a station on the same day *overwrote the first*, so "does a station's price
move within a day?" was not merely unanswered but unanswerable — the evidence
was being discarded at write time. The 17 existing rows read `12:00`, which is
what "not recorded" is spelled as, not an observation made at noon.

**`n` now counts days, not rows,** and the offset is `median(per-day median
delta)`. Once a station can be priced four times in an afternoon, counting rows
would drive it past `CONFIDENT_OBSERVATIONS = 3` off one trip — and
`gasprices.ino` reads that same `n` to decide whether to draw the `?`. Four
prices in a day are four samples of one day of a number that moves over weeks.
The dashboard ladder shows the row count separately as `obs`.

**Express log card** in the dashboard: address (matched on address, brand, label
or city), a price in whatever form you typed it (`1.709`, `170.9`, `1709`), and
*minutes ago* subtracted from the browser clock. Entries stage in `localStorage`
and are written on submit, with one rebuild for the batch instead of one per
price.

**Adding a station is one line, everything derived.** `add_station.py "esso
10016 bayview ave & major mackenzie dr e richmond hill"` produces all six
columns: brand off the front, city off the back, address in between, id and
label from the street, role `tracked`, city falling back to whichever the
registry mostly uses. An unknown address in the express card opens the add form
already filled with exactly what would be written — you confirm it because an id
is permanent once a price references one, not because anything is missing. One
parser, in Python, called from the page; a second one in JS would drift and the
field it drifted on would be the id.

Labels fit `LABEL_MAX` by abbreviating with spellings already in `stations.csv`
(`MAJMAC`, `ELGINMILLS`, `PETROCAN`) and then dropping whole words — never a
mid-word cut. `Hwy 7` keeps its number, since splitting it collapsed every
Highway 7 station onto the same label and id. **Brand can come back blank on
purpose:** it is not derivable from an address, and a placeholder would put a
brand nobody checked into the registry, the ladder and the panel header.

**Removing an observation** is now possible: `log_price.py --remove`, exposed as
the `×` on the dashboard's *Recent observations* list. Keyed on the full
`(date, time, station)` so deleting a typo cannot take a good row with it. It
refuses to touch `history.csv`; the regional series is one row per date and
dropping one silently reshapes the benchmark everything is measured against.

**Fixed:** `lambda_handlers.log_handler` dated observations with
`dt.date.today()`, which is **UTC** in Lambda — anything logged after 8pm
Toronto landed on tomorrow, at the newest end of the calibration window,
anchoring the model to a day that had not happened. Now America/Toronto. The
endpoint also accepts an explicit `date`/`time`, both validated.

**One station, up close.** Click a row in the ladder: its logged prices against
the regional benchmark and against `benchmark + offset`, a fit through its daily
`observed − benchmark`, and an edit form. Two new statistics, both stated with
their `n`:

- **Offset drift** — OLS on the deltas, reported as c/L per 30 days with a **95%
  interval** from a *t* value, not 1.96 (at n=4 the normal approximation
  understates the interval 2.2×, which is how a three-week wobble reads as a
  trend). Refuses to fit below **4 days**: two points fit perfectly by
  construction and an r² of 1.00 off two sightings would be the most misleading
  number on the page. This is the test of the assumption the station model rests
  on — that an offset is structural and holds for weeks.
- **Predicts to ±X c/L** — leave-one-out. Each day is scored against the median
  of the *other* days, which is the offset the model would actually have carried
  in. Scoring against a median containing the scored day flatters every station
  and the thinnest samples most.

Not compute-heavy, measured rather than asserted: **0.004 ms** per station on
today's data, **2.2 ms** at the ceiling `OFFSET_WINDOW_DAYS` allows (180 days
logged daily), 42 ms for all 19 stations at that ceiling. The leave-one-out is
O(n²) in days and the window bounds it.

**Editing a station** — `backend/edit_station.py`, and the form in that panel.
Brand, address, city, role and label are a rewritten line. **Renaming an id is
not:** `station_prices.csv` references it by value, so `--rename` rewrites both
files together. Removing a station that has prices requires `--with-prices`.

**Fixed, found while building the above:** a station id in `station_prices.csv`
with no row in `stations.csv` was dropped **in total silence** —
`compute_offsets` skips it and `v_station_priced` inner-joins it away, so
hand-editing an id in one file lost observations with nothing anywhere to say
so. `stations.orphans()` now detects it, `build.py` warns, and the station panel
names the ids and the row counts.

New: `backend/add_station.py`, `backend/edit_station.py`,
`backend/test_stations.py`. New SQL view
`v_station_intraday`, empty until a day carries two prices at one station —
which no day yet does, so the station panel says intraday movement is
**unmeasured**, not measured at zero.

**What got worse:** a burst of logging no longer walks a station toward
`confident` as fast as raw row counting would have. That is the intended trade.
**Not verified:** whether intraday movement is large enough to be worth
modelling — there is no day with two prices at one station to look at yet.

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
