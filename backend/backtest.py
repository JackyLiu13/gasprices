"""Replay history.csv through the verdict engine and ask the only question that
matters: would following it have beaten just filling up when the tank got low?

    python3 backend/backtest.py                # current thresholds
    python3 backend/backtest.py --sweep        # grid search save_threshold x horizon
    python3 backend/backtest.py --oracle       # perfect foresight = ceiling on the rules

The simulation drives a car: consume litres/day, and each morning consult the
verdict. If it says go and the tank isn't full, fill up at that day's price.
The baseline fills only when the tank hits the low mark. Both burn the same
fuel, so the comparison is a straight average $/L paid.

`--oracle` feeds the engine the actual future instead of predictions. If oracle
barely beats the baseline, no forecasting improvement will save you — the rules
are the problem. If oracle wins big but the live run doesn't, the model is.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import model  # noqa: E402
from verdict import Config, Input, Tank, Verdict, evaluate  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY = ROOT / "backend" / "history.csv"

CAPACITY_L = 50.0
DAILY_L = 7.0        # ~2,500 km/month in a small car
LOW_FRAC = 0.25      # baseline refills here; also the TANK_LOW boundary
FULL_FRAC = 0.60
WINDOW_DAYS = 30


def load_series() -> list[tuple[str, float, float | None]]:
    """[(date, retail, wholesale_smooth|None), ...] — observed price preferred."""
    if not HISTORY.exists():
        print("no history.csv yet — run build.py for a few days first", file=sys.stderr)
        raise SystemExit(2)
    out = []
    with HISTORY.open() as f:
        for r in csv.DictReader(f):
            if not r.get("date"):
                continue
            price = (r.get("retail_actual") or "").strip() or (r.get("retail_model") or "").strip()
            if not price:
                continue
            w = (r.get("wholesale_cad_l") or "").strip()
            out.append((r["date"], float(price), float(w) if w else None))
    return out


def tank_state(litres: float) -> Tank:
    frac = litres / CAPACITY_L
    if frac <= LOW_FRAC:
        return Tank.LOW
    return Tank.FULL if frac >= FULL_FRAC else Tank.HALF


def predictions(series, i: int, horizon: int, oracle: bool) -> list[int]:
    if oracle:
        return [model.to_tenths(p) for _, p, _ in series[i + 1: i + 1 + horizon]]
    _, today_px, wholesale = series[i]
    if wholesale is None:
        return []
    target = model.retail_from_wholesale(wholesale, model.DEFAULT_MARGIN)
    return [model.to_tenths(p) for p in model.predict(today_px, target, horizon)]


def simulate(series, cfg: Config, oracle: bool, follow: bool) -> tuple[float, int, int]:
    """Returns (avg $/L paid, number of fills, days run dry)."""
    litres = CAPACITY_L
    spent = 0.0
    bought = 0.0
    fills = 0
    dry = 0

    for i in range(WINDOW_DAYS, len(series) - cfg.horizon - 1):
        _, price, _ = series[i]
        window = [p for _, p, _ in series[i - WINDOW_DAYS: i + 1]]
        state = tank_state(litres)

        if litres < DAILY_L:                      # can't make it through the day
            dry += 1
            fill_now = True
        elif not follow:
            fill_now = state is Tank.LOW          # baseline strategy
        else:
            v = evaluate(Input(today=model.to_tenths(price),
                               pred=predictions(series, i, cfg.horizon, oracle),
                               window_lo=model.to_tenths(min(window)),
                               window_hi=model.to_tenths(max(window)),
                               age_minutes=0, tank=state), cfg)
            fill_now = v.verdict in (Verdict.FILL_NOW, Verdict.GREAT) and state is not Tank.FULL

        if fill_now:
            added = CAPACITY_L - litres
            if added > 1.0:
                spent += added * price
                bought += added
                litres = CAPACITY_L
                fills += 1

        litres -= DAILY_L

    return (spent / bought if bought else 0.0), fills, dry


def report(series, cfg: Config, oracle: bool) -> float:
    base, bfills, bdry = simulate(series, cfg, oracle, follow=False)
    strat, sfills, sdry = simulate(series, cfg, oracle, follow=True)
    delta = (base - strat) * 100  # cents per litre

    label = "oracle" if oracle else "model"
    print(f"  baseline (fill when low): {base:.4f} $/L  {bfills} fills, {bdry} dry")
    print(f"  verdict engine ({label:6}): {strat:.4f} $/L  {sfills} fills, {sdry} dry")
    print(f"  -> {delta:+.2f} c/L, about ${delta / 100 * DAILY_L * 365:+.0f}/year\n")
    return delta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--oracle", action="store_true")
    args = ap.parse_args()

    series = load_series()
    need = WINDOW_DAYS + 12
    if len(series) < need:
        print(f"need ~{need} days of history, have {len(series)}. "
              "Keep the Action running (and keep logging pump prices).", file=sys.stderr)
        return 2

    print(f"{len(series)} days: {series[0][0]} .. {series[-1][0]}\n")

    if not args.sweep:
        report(series, Config(), args.oracle)
        return 0

    print("save_threshold x horizon, cents/L saved vs baseline:\n")
    horizons = [3, 4, 5, 6, 7]
    print("  thr\\hz " + "".join(f"{h:>8}" for h in horizons))
    best = (-99.0, 0, 0)
    for thr in (5, 10, 15, 20, 25, 30, 40):
        cells = []
        for hz in horizons:
            cfg = Config(save_threshold=thr, horizon=hz)
            b, _, _ = simulate(series, cfg, args.oracle, follow=False)
            s, _, _ = simulate(series, cfg, args.oracle, follow=True)
            d = (b - s) * 100
            cells.append(f"{d:>8.2f}")
            if d > best[0]:
                best = (d, thr, hz)
        print(f"  {thr:>3}   " + "".join(cells))
    print(f"\nbest: save_threshold={best[1]} ({best[1] / 10:.1f} c/L), "
          f"horizon={best[2]} -> {best[0]:+.2f} c/L")
    print("Set these in verdict.py Config AND verdict.h gp_default_config().")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
