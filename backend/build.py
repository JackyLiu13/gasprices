"""Build docs/data.json — the one file the ESP32 fetches.

    python3 backend/build.py                 # fetch, update history, write JSON
    python3 backend/build.py --dry-run       # print, touch nothing
    LOCAL_PRICE_OVERRIDE=1.489 python3 backend/build.py

Design rule: never publish a bad file. If a source fails, this exits non-zero
and leaves the previous docs/data.json in place. A day-old price is fine; the
firmware knows how to say "stale". A wrong price is not fine.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import forecast_log  # noqa: E402
import model  # noqa: E402
import schema  # noqa: E402
import sources  # noqa: E402
import stations  # noqa: E402
from schema import HISTORY_FIELDS as FIELDS  # noqa: E402
from verdict import Input, Tank, evaluate  # noqa: E402

from paths import DATA_DIR as ROOT  # noqa: E402  (kept for relative_to() logging)
from paths import DATA_JSON as OUT  # noqa: E402
from paths import HISTORY  # noqa: E402
from paths import STATIONS_JSON  # noqa: E402

WINDOW_DAYS = 30      # rolling window for window_lo / window_hi
SPARK_DAYS = 28       # how much history the OLED sparkline gets
HORIZON = 5           # len(pred)
STATION = "Richmond Hill, ON"


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    with HISTORY.open() as f:
        return [r for r in csv.DictReader(f) if r.get("date")]


def save_history(rows: list[dict]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("w", newline="") as f:
        w = schema.writer(f, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def fnum(row: dict, key: str) -> float | None:
    v = (row.get(key) or "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def best_retail(row: dict) -> float | None:
    """What we believe the pump actually was, most trustworthy source first:
    a price you logged, then Ontario's weekly survey, then our own model."""
    return (fnum(row, "retail_actual")
            or fnum(row, "retail_survey")
            or fnum(row, "retail_model"))


def observed_retail(row: dict) -> float | None:
    """Real measurements only — never our own model, or calibration would be
    fitting the margin to a price the margin produced."""
    return fnum(row, "retail_actual") or fnum(row, "retail_survey")


def write_stations_json(sts: dict) -> None:
    """Publish the registry for clients that locate a station rather than price it.

    Deliberately NOT folded into data.json. The ESP32 parses that file on every
    fetch, twice a day, and has no use for a coordinate: measured, merging the
    19 lat/lon pairs in takes data.json from 4387 to 5086 bytes (+16%) for a
    field the firmware never reads. This file is fetched by a phone instead, on
    demand, and only changes when the registry does.

    Written unconditionally rather than only when the registry changed: it is
    derived entirely from stations.csv, so rewriting it is idempotent and git
    sees a diff only when there is one. Sorted by id so that diff stays readable.

    Note this carries no prices. It is public (GitHub Pages), it needs no
    authentication to be useful, and keeping it to "where the stations are"
    means it never has to be reasoned about as anything but a map.
    """
    payload = {
        "generated": dt.date.today().isoformat(),
        "confident_at": stations.CONFIDENT_OBSERVATIONS,
        "stations": [
            {"id": s.id, "label": s.label, "brand": s.brand,
             "address": s.address, "city": s.city, "role": s.role,
             # null, not omitted: a client sorting by distance has to be able to
             # tell "no coordinate on file" from a key it forgot to read.
             "lat": s.lat, "lon": s.lon}
            for s in sorted(sts.values(), key=lambda s: s.id)
        ],
    }
    STATIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATIONS_JSON.write_text(json.dumps(payload, indent=1) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--horizon", type=int, default=HORIZON)
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    history = load_history()

    # 1. Upstream inputs. Any failure here aborts before we touch anything.
    rbob = sources.rbob_history(days=WINDOW_DAYS + 10)
    fx = sources.usd_cad()
    rbob_today = rbob[-1][1]

    # FX is applied at today's rate across the whole RBOB series. Over a month
    # USD/CAD drifts ~1%, i.e. under a cent per litre — well inside the noise
    # this model already carries, and it saves a second historical API.
    # (backfill.py does use historical FX; over a year the drift is not small.)
    wholesale_series = [model.wholesale_cad_per_litre(px, fx) for _, px in rbob]
    wholesale_today = wholesale_series[-1]
    wholesale_smooth = model.ema(wholesale_series[-model.WHOLESALE_EMA_DAYS * 3:])

    # Smoothed wholesale as of each market close, so a survey row dated last
    # Monday is paired with last Monday's wholesale rather than today's.
    ema_at: dict[str, float] = {}
    k = 2.0 / (model.WHOLESALE_EMA_DAYS + 1.0)
    running: float | None = None
    for (date, _), w in zip(rbob, wholesale_series):
        running = w if running is None else w * k + running * (1.0 - k)
        ema_at[date] = running
    market_days = sorted(ema_at)

    def wholesale_on(date: str) -> float | None:
        earlier = [d for d in market_days if d <= date]
        return ema_at[earlier[-1]] if earlier else None

    # 2. Refresh the Ontario weekly survey. It only moves on Mondays, so most
    #    runs are a no-op, but it re-anchors the level every week for free.
    survey: dict[str, float] = {}
    try:
        for date, px in sources.ontario_retail_survey(limit=60):
            survey[date] = px
    except sources.FetchError as e:
        print(f"warning: Ontario survey unavailable ({e})", file=sys.stderr)

    for row in history:
        if not (row.get("retail_survey") or "").strip() and row["date"] in survey:
            row["retail_survey"] = f"{survey[row['date']]:.4f}"
    known = {r["date"] for r in history}
    for date, px in survey.items():
        if date not in known and date <= today:
            history.append({"date": date, "retail_survey": f"{px:.4f}"})
    history.sort(key=lambda r: r["date"])

    # A survey row is useless for calibration without the matching wholesale.
    for row in history:
        if not (row.get("wholesale_cad_l") or "").strip():
            w = wholesale_on(row["date"])
            if w is not None:
                row["wholesale_cad_l"] = f"{w:.4f}"

    # 3. Calibrate the margin against real measurements only.
    obs: list[tuple[str, float, float]] = []
    for row in history:
        px, w = observed_retail(row), fnum(row, "wholesale_cad_l")
        if px and w:
            obs.append((row["date"], px, w))
    margin = model.calibrate_margin(obs)

    # 4. Equilibrium price implied by where wholesale already is.
    target = model.retail_from_wholesale(wholesale_smooth, margin)

    # 5. Today's level. Take the freshest real observation and roll it forward
    #    to today through the same passthrough model, rather than treating a
    #    four-day-old survey number as if it were today's price.
    local = sources.local_retail_hint()
    anchor_date, anchor_px = None, None
    for row in history:
        px = observed_retail(row)
        if px:
            anchor_date, anchor_px = row["date"], px

    if local is not None:
        today_retail, level_src = local, "observed"
    elif anchor_px is not None:
        lag = (dt.date.fromisoformat(today) - dt.date.fromisoformat(anchor_date)).days
        today_retail = (anchor_px if lag <= 0
                        else model.predict(anchor_px, target, horizon=lag)[-1])
        level_src = f"anchored({anchor_date}, +{lag}d)"
    else:
        today_retail, level_src = target, "modelled"

    # 5. Forward curve.
    pred = model.predict(today_retail, target, horizon=args.horizon)

    # 5b. Log what every candidate predictor believes, in REGIONAL BENCHMARK
    #     space — before the station rebasing below, because a forecast recorded
    #     in "cheapest station" space silently changes meaning the day a
    #     different station takes the lead, and would be unscorable afterwards.
    #
    #     Only DEFAULT_VARIANT reaches the payload. The others cost nothing on
    #     device and mean a candidate has a real track record before anyone
    #     proposes promoting it.
    forecast_rows = []
    for name, fn in model.VARIANTS.items():
        for i, p in enumerate(fn(today_retail, target, args.horizon), start=1):
            target_date = (dt.date.fromisoformat(today) + dt.timedelta(days=i))
            forecast_rows.append({
                "made_on": today,
                "target_date": target_date.isoformat(),
                "horizon": i,
                "variant": name,
                "predicted": f"{p:.4f}",
                "basis": level_src,
            })

    # 6. Upsert today's row, then take the rolling window from history.
    prior_today = next((r for r in history if r["date"] == today), {})
    row_today = {
        "date": today,
        "rbob_usd_gal": f"{rbob_today:.4f}",
        "usd_cad": f"{fx:.4f}",
        "wholesale_cad_l": f"{wholesale_smooth:.4f}",
        "retail_model": f"{today_retail:.4f}",
        "retail_survey": prior_today.get("retail_survey", "")
                         or (f"{survey[today]:.4f}" if today in survey else ""),
        # Never drop a price logged earlier today just because this run had no
        # LOCAL_PRICE_OVERRIDE set.
        "retail_actual": (f"{local:.3f}" if local is not None
                          else prior_today.get("retail_actual", "")),
        "margin": f"{margin:.4f}",
    }
    history = [r for r in history if r["date"] != today] + [row_today]
    history.sort(key=lambda r: r["date"])

    # Date-based, not row-based: backfilled survey rows are weekly, so slicing
    # the last 30 *rows* would quietly reach back seven months and blow the
    # window wide enough to make level% meaningless.
    def since(days: int) -> list[dict]:
        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=days)).isoformat()
        return [r for r in history if r["date"] >= cutoff]

    window_vals = [v for v in (best_retail(r) for r in since(WINDOW_DAYS)) if v]
    lo, hi = min(window_vals), max(window_vals)
    spark = [model.to_tenths(v) for v in
             (best_retail(r) for r in since(SPARK_DAYS)) if v]

    # 7. Stations. The regional series above is the benchmark; each station is
    #    that benchmark plus its own stable offset.
    benchmark = {r["date"]: b for r in history if (b := best_retail(r))}
    sts = stations.load_stations()

    # A price whose station id is not in the registry is skipped by every
    # consumer without a word — see stations.orphans. Not fatal (the rest of the
    # feed is fine and refusing to publish over it would be worse), but it means
    # observations you logged are not reaching the model, which you want to hear.
    for sid, n in stations.orphans(sts).items():
        print(f"warning: {n} price(s) reference {sid!r}, which is not in "
              f"stations.csv — they are being ignored", file=sys.stderr)

    stations.compute_offsets(sts, benchmark, today)
    stations.predict_all(sts, today_retail)

    best = stations.cheapest(sts)
    baseline = stations.baseline(sts)

    # A home station with no logged price has no offset, so it cannot be priced
    # and drops silently out of baseline selection — leaving the panel labelling
    # some other station HOME. Say so rather than quietly measuring savings
    # against the wrong place.
    homes = [s for s in sts.values() if s.role == "home"]
    if homes and (baseline is None or baseline.role != "home"):
        print(f"warning: home station {homes[0].id} has no offset yet, so savings "
              f"are measured against {baseline.label if baseline else 'nothing'}. "
              f"Fix with: python3 backend/log_price.py <price> -s {homes[0].id}",
              file=sys.stderr)

    # Everything the device shows is priced at the station you'd actually drive
    # to. Rebasing the whole window by one offset (rather than splicing per-day
    # cheapest prices) keeps the series smooth, so level% still means "how cheap
    # is this station against its own recent range" instead of jumping every
    # time a different station takes the lead.
    shift = best.offset if best and best.offset is not None else 0.0
    if best:
        today_retail += shift
        pred = [p + shift for p in pred]
        lo, hi = lo + shift, hi + shift
        spark = [s + model.to_tenths(shift) for s in spark]

    save_vs_regular = 0.0
    if best and baseline and baseline.id != best.id:
        save_vs_regular = max(0.0, (baseline.predicted or 0) - (best.predicted or 0))

    station_rows = stations.summary(sts)

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "schema": 2,
        "station": STATION,
        "updated": now.isoformat().replace("+00:00", "Z"),
        "epoch": int(now.timestamp()),
        "today_cad": model.to_tenths(today_retail),
        "pred": [model.to_tenths(p) for p in pred],
        "window_lo": model.to_tenths(lo),
        "window_hi": model.to_tenths(hi),
        "hist": spark,
        "best": None if not best else {
            "id": best.id,
            "label": best.label,
            "price": model.to_tenths(best.predicted),
            # The device re-prices the whole window when you cycle stations, by
            # shifting it by (that station's offset - this one). Without this it
            # would have to guess what today_cad is relative to.
            "offset": model.to_tenths(best.offset or 0.0),
            "save": model.to_tenths(save_vs_regular),
            "n": best.observations,
            "confident": best.confident,
        },
        "regular": None if not baseline else {
            "id": baseline.id,
            "label": baseline.label,
            "price": model.to_tenths(baseline.predicted),
            "offset": model.to_tenths(baseline.offset or 0.0),
        },
        "stations": station_rows,
        # Diagnostics — the firmware ignores these, you won't.
        "meta": {
            "level_source": level_src,
            "days_of_history": len(history),
            "observed_days": len(obs),
            "margin_cad_l": round(margin, 4),
            "wholesale_cad_l": round(wholesale_today, 4),
            "target_cad_l": round(target, 4),
            "rbob_usd_gal": round(rbob_today, 4),
            "usd_cad": round(fx, 4),
            "benchmark_cad_l": round(today_retail - shift, 4),
            "station_shift_cad_l": round(shift, 4),
            "stations_priced": len(station_rows),
        },
    }

    # 8. Run the engine here too, so a broken rule shows up in CI logs before it
    #    ships to a device you'd have to walk over to.
    v = evaluate(Input(today=payload["today_cad"], pred=payload["pred"],
                       window_lo=payload["window_lo"], window_hi=payload["window_hi"],
                       age_minutes=0, tank=Tank.HALF))
    payload["verdict_hint"] = v.verdict.value
    payload["reason_hint"] = v.reason

    text = json.dumps(payload, indent=1) + "\n"
    if args.dry_run:
        print(text)
        print(f"[dry-run] verdict={v.verdict.value} ({v.reason}) "
              f"level={v.level_pct}% source={level_src}", file=sys.stderr)
        return 0

    save_history(history)
    forecast_log.record(forecast_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    write_stations_json(sts)
    where = f" @ {best.label}" if best else ""
    saving = (f", save {save_vs_regular * 100:.1f}c vs {baseline.label}"
              if best and baseline and save_vs_regular > 0 else "")
    print(f"{OUT.relative_to(ROOT)}: {model.to_tenths(today_retail)}{where} "
          f"({level_src}) -> {v.verdict.value}: {v.reason}{saving}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
