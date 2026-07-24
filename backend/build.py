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

import model  # noqa: E402
import sources  # noqa: E402
from verdict import Input, Tank, evaluate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY = ROOT / "backend" / "history.csv"
OUT = ROOT / "docs" / "data.json"

WINDOW_DAYS = 30      # rolling window for window_lo / window_hi
SPARK_DAYS = 28       # how much history the OLED sparkline gets
HORIZON = 5           # len(pred)
STATION = "Richmond Hill, ON"

FIELDS = ["date", "rbob_usd_gal", "usd_cad", "wholesale_cad_l",
          "retail_model", "retail_actual", "margin"]


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    with HISTORY.open() as f:
        return [r for r in csv.DictReader(f) if r.get("date")]


def save_history(rows: list[dict]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
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
    """What we believe the pump actually was: a logged price beats the model."""
    return fnum(row, "retail_actual") or fnum(row, "retail_model")


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
    wholesale_series = [model.wholesale_cad_per_litre(px, fx) for _, px in rbob]
    wholesale_today = wholesale_series[-1]
    wholesale_smooth = model.ema(wholesale_series[-model.WHOLESALE_EMA_DAYS * 3:])

    # 2. Calibrate the margin against pump prices you actually logged.
    pairs: list[tuple[float, float]] = []
    for row in history:
        actual, w = fnum(row, "retail_actual"), fnum(row, "wholesale_cad_l")
        if actual and w:
            pairs.append((actual, w))
    margin = model.calibrate_margin(pairs)

    # 3. Equilibrium price implied by where wholesale already is.
    target = model.retail_from_wholesale(wholesale_smooth, margin)

    # 4. Today's level: a logged price wins; else carry yesterday forward one
    #    passthrough step; else (cold start) fall back to the model outright.
    local = sources.local_retail_hint()
    if local is not None:
        today_retail, level_src = local, "observed"
    else:
        prev = best_retail(history[-1]) if history else None
        if prev is not None:
            today_retail = model.predict(prev, target, horizon=1)[0]
            level_src = "carried"
        else:
            today_retail, level_src = target, "modelled"

    # 5. Forward curve.
    pred = model.predict(today_retail, target, horizon=args.horizon)

    # 6. Upsert today's row, then take the rolling window from history.
    row_today = {
        "date": today,
        "rbob_usd_gal": f"{rbob_today:.4f}",
        "usd_cad": f"{fx:.4f}",
        "wholesale_cad_l": f"{wholesale_smooth:.4f}",
        "retail_model": f"{today_retail:.4f}",
        "retail_actual": f"{local:.3f}" if local is not None else "",
        "margin": f"{margin:.4f}",
    }
    history = [r for r in history if r["date"] != today] + [row_today]
    history.sort(key=lambda r: r["date"])

    window_vals = [v for v in (best_retail(r) for r in history[-WINDOW_DAYS:]) if v]
    lo, hi = min(window_vals), max(window_vals)
    spark = [model.to_tenths(v) for v in
             [v for v in (best_retail(r) for r in history[-SPARK_DAYS:]) if v]]

    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "schema": 1,
        "station": STATION,
        "updated": now.isoformat().replace("+00:00", "Z"),
        "epoch": int(now.timestamp()),
        "today_cad": model.to_tenths(today_retail),
        "pred": [model.to_tenths(p) for p in pred],
        "window_lo": model.to_tenths(lo),
        "window_hi": model.to_tenths(hi),
        "hist": spark,
        # Diagnostics — the firmware ignores these, you won't.
        "meta": {
            "level_source": level_src,
            "days_of_history": len(history),
            "observed_days": len(pairs),
            "margin_cad_l": round(margin, 4),
            "wholesale_cad_l": round(wholesale_today, 4),
            "target_cad_l": round(target, 4),
            "rbob_usd_gal": round(rbob_today, 4),
            "usd_cad": round(fx, 4),
        },
    }

    # 7. Run the engine here too, so a broken rule shows up in CI logs before it
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"{OUT.relative_to(ROOT)}: {model.to_tenths(today_retail)} "
          f"({level_src}) -> {v.verdict.value}: {v.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
