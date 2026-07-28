"""Derivations the model computes internally and then throws away.

build.py calculates an implied margin per observation and keeps only the median;
it calculates a five-day forward curve and keeps none of it. Those intermediates
are exactly what you need to see to judge whether the model is any good, so this
module recomputes them from what is on disk.

Pure functions, no I/O, no file paths — everything is passed in. That is what
makes them testable (backend/test_analytics.py) and what lets the same code
serve the dashboard, the tests, and any future scoring job.

Two rules inherited from the rest of the backend and easy to break here:

  * Windows are date-based, never row-based. history.csv is weekly for its first
    sixty rows and daily after that; "the last 30 rows" means seven months in one
    regime and a month in the other.
  * Prices are $/L in this module. Errors are reported in cents/L, because that
    is the unit the project argues in ("the model's edge is about 0.5 c/L").
"""

from __future__ import annotations

import datetime as dt
import statistics

import model


# --- small date helpers ----------------------------------------------------

def _d(date: str) -> dt.date:
    return dt.date.fromisoformat(date)


def since(dates: list[str], days: int, end: str | None = None) -> list[str]:
    """The subset of `dates` within `days` of `end` (default: the newest date)."""
    if not dates:
        return []
    last = _d(end or max(dates))
    cutoff = last - dt.timedelta(days=days)
    return [d for d in dates if cutoff <= _d(d) <= last]


# --- the margin series -----------------------------------------------------

def margin_series(rows: list[dict],
                  observed: callable) -> list[tuple[str, float]]:
    """[(date, implied_margin), ...] for every row with a real observation.

    `observed` is build.best_retail's stricter sibling — a callable taking a row
    and returning a measured price or None. Passing it in rather than
    re-implementing it keeps the "calibration never eats model output" rule in
    one place; if that rule changes, this follows automatically.
    """
    out = []
    for r in rows:
        px = observed(r)
        w = r.get("wholesale_cad_l")
        try:
            w = float((w or "").strip())
        except (ValueError, AttributeError):
            continue
        if px:
            out.append((r["date"], model.implied_margin(px, w)))
    return out


def rolling_margin(series: list[tuple[str, float]],
                   days: int = model.MARGIN_WINDOW_DAYS,
                   min_obs: int = 5) -> list[tuple[str, float]]:
    """The margin calibrate_margin() would have chosen on each date.

    Deliberately mirrors model.calibrate_margin: median over a trailing
    date-based window, and nothing at all until `min_obs` observations sit
    inside it. Drawing a line where the real code would have used the fallback
    would make the chart a lookalike rather than the calibration itself.
    """
    out = []
    for date, _ in series:
        cutoff = (_d(date) - dt.timedelta(days=days)).isoformat()
        window = [m for d, m in series if cutoff <= d <= date]
        if len(window) >= min_obs:
            out.append((date, statistics.median(window)))
    return out


def target_series(rows: list[dict],
                  margins: dict[str, float] | None = None
                  ) -> list[tuple[str, float]]:
    """Equilibrium price implied by that day's wholesale.

    If `margins` is given (date -> margin, e.g. from rolling_margin) each day is
    priced with the margin that was actually in force. Otherwise the row's own
    stored `margin` column is used, falling back to DEFAULT_MARGIN. Using one
    present-day margin for the whole series would redraw history under a
    calibration it never had.
    """
    out = []
    for r in rows:
        try:
            w = float((r.get("wholesale_cad_l") or "").strip())
        except ValueError:
            continue
        m = None
        if margins:
            m = margins.get(r["date"])
        if m is None:
            try:
                m = float((r.get("margin") or "").strip())
            except ValueError:
                m = model.DEFAULT_MARGIN
        out.append((r["date"], model.retail_from_wholesale(w, m)))
    return out


# --- forecast accuracy -----------------------------------------------------

def forecast_error(forecasts: list[dict],
                   actuals: dict[str, float]) -> list[dict]:
    """Score a forecast log against what actually happened.

    forecasts: rows shaped like schema.FORECAST_FIELDS.
    actuals:   date -> benchmark price ($/L), from build.best_retail.

    Returns one dict per (variant, horizon) with the model's error and the
    error of the only baseline that matters: assuming no move at all. A timing
    model that cannot beat "today's price is tomorrow's price" is costing you
    complexity for nothing, and the README already puts the real edge at about
    0.5 c/L — small enough that it has to be measured continuously, not once.

    Scored only where both the target date and the origin date have a real
    price, so the model and the baseline always face the identical sample.
    """
    buckets: dict[tuple[str, int], dict] = {}

    for f in forecasts:
        target = actuals.get(f["target_date"])
        origin = actuals.get(f["made_on"])
        if target is None or origin is None:
            continue
        try:
            predicted = float(f["predicted"])
            horizon = int(f["horizon"])
        except (ValueError, KeyError):
            continue

        key = (f.get("variant") or "passthrough", horizon)
        b = buckets.setdefault(key, {"errs": [], "base": [], "dates": []})
        b["errs"].append(predicted - target)
        b["base"].append(origin - target)
        b["dates"].append(f["made_on"])

    out = []
    for (variant, horizon), b in sorted(buckets.items()):
        errs, base = b["errs"], b["base"]
        out.append({
            "variant": variant,
            "horizon": horizon,
            "n": len(errs),
            # cents/L, the unit the project argues in
            "mae_cents": statistics.fmean(abs(e) for e in errs) * 100,
            "bias_cents": statistics.fmean(errs) * 100,
            "baseline_mae_cents": statistics.fmean(abs(e) for e in base) * 100,
            "first": min(b["dates"]),
            "last": max(b["dates"]),
        })
        out[-1]["edge_cents"] = (out[-1]["baseline_mae_cents"]
                                 - out[-1]["mae_cents"])
    return out


# --- station offsets -------------------------------------------------------

def station_dispersion(prices: list[tuple[str, str, float, str]],
                       benchmark: dict[str, float],
                       window_days: int = 180,
                       today: str | None = None) -> dict[str, dict]:
    """Per-station offset spread — the thing stations.py computes and discards.

    stations.compute_offsets keeps median(observed - benchmark) and the count.
    The count alone cannot tell you whether three observations agreed to within
    half a cent or disagreed by four, and that difference is the whole question
    of whether an offset is worth trusting. MAD (median absolute deviation) is
    the matching spread statistic for a median, and survives a single typo the
    way a standard deviation would not.
    """
    end = today or (max(benchmark) if benchmark else None)
    if end is None:
        return {}
    cutoff = (_d(end) - dt.timedelta(days=window_days)).isoformat()

    grouped: dict[str, list[tuple[str, float]]] = {}
    for date, sid, price, _src in prices:
        if date < cutoff or date > end:
            continue
        bench = benchmark.get(date)
        if bench is None:
            continue
        grouped.setdefault(sid, []).append((date, price - bench))

    out = {}
    for sid, obs in grouped.items():
        deltas = [d for _, d in obs]
        med = statistics.median(deltas)
        dates = sorted(d for d, _ in obs)
        out[sid] = {
            "median": med,
            "mad": statistics.median(abs(x - med) for x in deltas),
            "n": len(deltas),
            "first": dates[0],
            "last": dates[-1],
            "span_days": (_d(dates[-1]) - _d(dates[0])).days,
            "deltas": sorted(deltas),
        }
    return out


# --- coverage --------------------------------------------------------------

def coverage(rows: list[dict], fields: list[str]) -> list[dict]:
    """Which columns are actually populated, per row.

    The honest counterweight to every chart on the dashboard: history.csv has 64
    rows but only two logged pump prices, and a line drawn through that should
    say so.
    """
    return [{"date": r["date"],
             "filled": [bool((r.get(f) or "").strip()) for f in fields[1:]]}
            for r in rows]
