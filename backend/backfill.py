"""Seed history.csv from Ontario's official weekly retail price survey.

    python3 backend/backfill.py              # last 52 weeks
    python3 backend/backfill.py --weeks 26
    python3 backend/backfill.py --dry-run

Without this you start with an empty window: level% is meaningless, the margin
is a guess, and the device says NEUTRAL for a month while it learns. This fills
in a year of real GTA pump prices in one shot.

Two things it deliberately does NOT do:

  * It does not interpolate weekly points into fake daily ones. Linear
    interpolation between two weekly prices can never move window_lo/window_hi,
    so it would add rows without adding information — only the illusion of
    daily resolution. Real daily rows accumulate from build.py going forward.
  * It does not overwrite retail_actual. Prices you logged yourself always win;
    the survey lands in its own column.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import model  # noqa: E402
import schema  # noqa: E402
import sources  # noqa: E402
from schema import HISTORY_FIELDS as FIELDS  # noqa: E402

from paths import DATA_DIR as ROOT  # noqa: E402
from paths import HISTORY  # noqa: E402


def wholesale_by_date(rbob: list[tuple[str, float]],
                      fx: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """{date: (rbob, fx, wholesale_ema)} — FX forward-filled over weekends."""
    fx_days = sorted(fx)
    out: dict[str, tuple[float, float, float]] = {}
    last_fx = fx[fx_days[0]] if fx_days else None
    ema_val: float | None = None
    k = 2.0 / (model.WHOLESALE_EMA_DAYS + 1.0)

    for date, px in rbob:
        rate = fx.get(date)
        if rate is None:
            earlier = [d for d in fx_days if d <= date]
            rate = fx[earlier[-1]] if earlier else last_fx
        if rate is None:
            continue
        last_fx = rate

        w = model.wholesale_cad_per_litre(px, rate)
        ema_val = w if ema_val is None else w * k + ema_val * (1.0 - k)
        out[date] = (px, rate, ema_val)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    survey = sources.ontario_retail_survey(limit=args.weeks)
    start, end = survey[0][0], survey[-1][0]
    print(f"survey: {len(survey)} weekly points, {start} -> {end}")

    period = "2y" if args.weeks > 40 else "1y"
    rbob = sources.rbob_history(days=10000, period=period)
    fx = sources.fx_series(start, end)
    print(f"rbob:   {len(rbob)} closes | fx: {len(fx)} days")

    wholesale = wholesale_by_date(rbob, fx)
    market_days = sorted(wholesale)

    rows: list[dict] = []
    pairs: list[tuple[str, float, float]] = []
    for date, retail in survey:
        # The most recent market close at or before the survey date.
        earlier = [d for d in market_days if d <= date]
        if not earlier:
            continue
        px, rate, ema_val = wholesale[earlier[-1]]
        rows.append({
            "date": date,
            "rbob_usd_gal": f"{px:.4f}",
            "usd_cad": f"{rate:.4f}",
            "wholesale_cad_l": f"{ema_val:.4f}",
            "retail_model": "",
            "retail_survey": f"{retail:.4f}",
            "retail_actual": "",
            "margin": "",
        })
        pairs.append((date, retail, ema_val))

    if not rows:
        print("no overlap between survey dates and market data", file=sys.stderr)
        return 1

    margin = model.calibrate_margin(pairs)
    print(f"\ncalibrated margin (last {model.MARGIN_WINDOW_DAYS} days): "
          f"{margin:.4f} $/L, vs DEFAULT_MARGIN {model.DEFAULT_MARGIN:.4f}")

    # Margin drifts, so report the whole span too — a big gap between these
    # means the fixed default is stale and the window is doing real work.
    whole = model.calibrate_margin(pairs, days=100000)
    print(f"  full {len(pairs)}-point span median: {whole:.4f} $/L")

    # How well does the stack reproduce the survey, in the window that matters?
    newest = max(d for d, _, _ in pairs)
    cutoff = (dt.date.fromisoformat(newest)
              - dt.timedelta(days=model.MARGIN_WINDOW_DAYS)).isoformat()
    errs = sorted(abs(model.retail_from_wholesale(w, margin) - r)
                  for d, r, w in pairs if d >= cutoff)
    if errs:
        print(f"  fit over that window: median {errs[len(errs) // 2] * 100:.2f} c/L, "
              f"worst {errs[-1] * 100:.2f} c/L")

    # Keep anything already on file; the survey only fills gaps.
    existing: dict[str, dict] = {}
    if HISTORY.exists():
        with HISTORY.open() as f:
            for r in csv.DictReader(f):
                if r.get("date"):
                    existing[r["date"]] = r

    merged = {r["date"]: r for r in rows}
    for date, old in existing.items():
        row = merged.setdefault(date, {"date": date})
        for key in FIELDS:
            if (old.get(key) or "").strip():
                row[key] = old[key]        # never clobber logged data
    final = [merged[d] for d in sorted(merged)]

    if args.dry_run:
        print(f"\n[dry-run] would write {len(final)} rows to history.csv")
        for r in final[-5:]:
            print(f"  {r['date']}  survey={r.get('retail_survey', ''):8} "
                  f"actual={r.get('retail_actual', ''):8}")
        return 0

    with HISTORY.open("w", newline="") as f:
        w = schema.writer(f, FIELDS)
        w.writeheader()
        for r in final:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    print(f"\nwrote {len(final)} rows to {HISTORY.relative_to(ROOT)}")
    print(f"Set DEFAULT_MARGIN = {margin:.4f} in backend/model.py, "
          "then re-run build.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
