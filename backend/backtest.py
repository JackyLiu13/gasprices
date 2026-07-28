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
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import model  # noqa: E402
from verdict import Config, Input, Tank, Verdict, evaluate  # noqa: E402

from paths import HISTORY  # noqa: E402

CAPACITY_L = 50.0
DAILY_L = 7.0        # ~2,500 km/month in a small car
LOW_FRAC = 0.25      # baseline refills here; also the TANK_LOW boundary
FULL_FRAC = 0.60
WINDOW_DAYS = 30


def load_series() -> list[tuple[str, float, float | None]]:
    """[(date, retail, wholesale_smooth|None), ...] — observed price preferred.

    The price falls back the same way build.best_retail does: logged, then
    Ontario's weekly survey, then the model. Leaving the survey out (as this
    used to) threw away 61 of 64 rows and left the backtest below its own
    minimum, so it exited before simulating anything — a silent no-op that
    looked like "not enough history yet".
    """
    if not HISTORY.exists():
        print("no history.csv yet — run build.py for a few days first", file=sys.stderr)
        raise SystemExit(2)
    out = []
    with HISTORY.open() as f:
        for r in csv.DictReader(f):
            if not r.get("date"):
                continue
            price = ((r.get("retail_actual") or "").strip()
                     or (r.get("retail_survey") or "").strip()
                     or (r.get("retail_model") or "").strip())
            if not price:
                continue
            w = (r.get("wholesale_cad_l") or "").strip()
            out.append((r["date"], float(price), float(w) if w else None))
    return out


def to_daily(series) -> list[tuple[str, float, float | None]]:
    """Expand the series onto a one-row-per-calendar-day grid.

    The simulation below burns DAILY_L per row and slices a 30-row window. Both
    are only true if a row is a day — and history.csv is weekly for its first
    sixty rows. Left alone, the backtest quietly modelled a car that drove
    2,500 km a week and a "30-day" window covering seven months, which is why
    it reported the engine and the baseline as identical: at that spacing they
    genuinely cannot differ.

    Prices are held flat between observations rather than interpolated. A
    straight line between two Monday surveys would invent midweek movement the
    verdict engine would then take credit for predicting.
    """
    if not series:
        return []
    out = []
    start, end = dt.date.fromisoformat(series[0][0]), dt.date.fromisoformat(series[-1][0])
    known = {d: (px, w) for d, px, w in series}
    px = w = None
    day = start
    while day <= end:
        iso = day.isoformat()
        if iso in known:
            px, w = known[iso]
        if px is not None:
            out.append((iso, px, w))
        day += dt.timedelta(days=1)
    return out


def margins_asof(series) -> list[float]:
    """The margin the live model would have held on each day of the series.

    predictions() used to price its target with DEFAULT_MARGIN (0.15) while
    build.py calibrates against a 90-day median. Measured against a margin the
    model never held, the backtest scores a predictor that was never shipped.

    Calibrated strictly from rows at or before each index. Using the whole
    series would hand every simulated day a margin derived partly from its own
    future — the backtest would then flatter a model that could not have known
    what it was being credited with.
    """
    out: list[float] = []
    obs: list[tuple[str, float, float]] = []
    for date, px, w in series:
        if w is not None:
            obs.append((date, px, w))
        out.append(model.calibrate_margin(obs))
    return out


def tank_state(litres: float) -> Tank:
    frac = litres / CAPACITY_L
    if frac <= LOW_FRAC:
        return Tank.LOW
    return Tank.FULL if frac >= FULL_FRAC else Tank.HALF


def predictions(series, i: int, horizon: int, oracle: bool,
                margins: list[float] | None = None) -> list[int]:
    if oracle:
        return [model.to_tenths(p) for _, p, _ in series[i + 1: i + 1 + horizon]]
    _, today_px, wholesale = series[i]
    if wholesale is None:
        return []
    margin = margins[i] if margins else model.DEFAULT_MARGIN
    target = model.retail_from_wholesale(wholesale, margin)
    return [model.to_tenths(p) for p in model.predict(today_px, target, horizon)]


def simulate(series, cfg: Config, oracle: bool, follow: bool,
             trace: list | None = None) -> tuple[float, int, int]:
    """Returns (avg $/L paid, number of fills, days run dry).

    Pass a list as `trace` to collect a per-day record of what happened — the
    dashboard charts the fill timeline from it. It is opt-in so the sweep, which
    runs this 70 times, does not build 70 traces it will not read.
    """
    litres = CAPACITY_L
    spent = 0.0
    bought = 0.0
    fills = 0
    dry = 0
    margins = margins_asof(series) if (follow and not oracle) else None

    for i in range(WINDOW_DAYS, len(series) - cfg.horizon - 1):
        date, price, _ = series[i]
        window = [p for _, p, _ in series[i - WINDOW_DAYS: i + 1]]
        state = tank_state(litres)
        v = None

        if litres < DAILY_L:                      # can't make it through the day
            dry += 1
            fill_now = True
        elif not follow:
            fill_now = state is Tank.LOW          # baseline strategy
        else:
            v = evaluate(Input(today=model.to_tenths(price),
                               pred=predictions(series, i, cfg.horizon, oracle, margins),
                               window_lo=model.to_tenths(min(window)),
                               window_hi=model.to_tenths(max(window)),
                               age_minutes=0, tank=state), cfg)
            fill_now = v.verdict in (Verdict.FILL_NOW, Verdict.GREAT) and state is not Tank.FULL

        filled = False
        if fill_now:
            added = CAPACITY_L - litres
            if added > 1.0:
                spent += added * price
                bought += added
                litres = CAPACITY_L
                fills += 1
                filled = True

        if trace is not None:
            trace.append({
                "date": date,
                "price": price,
                "litres": round(litres, 1),
                "tank": state.value,
                "verdict": v.verdict.value if v else "",
                "level_pct": v.level_pct if v else -1,
                "filled": filled,
            })

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

    observed = load_series()
    series = to_daily(observed)
    need = WINDOW_DAYS + 12
    if len(series) < need:
        print(f"need ~{need} days of history, have {len(series)}. "
              "Keep the Action running (and keep logging pump prices).", file=sys.stderr)
        return 2

    print(f"{len(series)} days: {series[0][0]} .. {series[-1][0]} "
          f"({len(observed)} observed, {len(series) - len(observed)} held flat between)\n")

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
