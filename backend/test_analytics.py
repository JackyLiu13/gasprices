"""Fixtures for backend/analytics.py — the derivations the dashboard draws.

    python3 backend/test_analytics.py

These are hand-checked rather than golden: every expected number below can be
worked out on paper from the tax stack, which is the point. A golden file would
have locked in whatever the code did the day it was written.

The case that earns its keep is `test_rolling_margin_is_date_based`. Weekly rows
followed by daily rows is the shape that has already caused two real bugs here,
and a row-based window passes every other test in this file.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import analytics  # noqa: E402
import model  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want, tol: float | None = None) -> None:
    ok = (abs(got - want) <= tol) if tol is not None else (got == want)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


def row(date, wholesale=None, survey=None, actual=None, margin=None):
    return {"date": date,
            "wholesale_cad_l": "" if wholesale is None else f"{wholesale:.4f}",
            "retail_survey": "" if survey is None else f"{survey:.4f}",
            "retail_actual": "" if actual is None else f"{actual:.3f}",
            "retail_model": "",
            "margin": "" if margin is None else f"{margin:.4f}"}


def observed(r):
    """Mirrors build.observed_retail: measurements only, never model output."""
    for key in ("retail_actual", "retail_survey"):
        v = (r.get(key) or "").strip()
        if v:
            return float(v)
    return None


# --- margin ----------------------------------------------------------------

def test_margin_inverts_the_stack():
    # A pump price built from a known margin must imply that margin back.
    retail = model.retail_from_wholesale(0.60, 0.20)
    series = analytics.margin_series([row("2026-01-01", 0.60, survey=retail)], observed)
    check("margin_series inverts retail_from_wholesale", series[0][1], 0.20, 1e-9)


def test_margin_skips_model_only_rows():
    rows = [row("2026-01-01", 0.60, survey=1.70),
            row("2026-01-02", 0.60)]              # no observation at all
    rows[1]["retail_model"] = "1.7000"            # model output must not count
    series = analytics.margin_series(rows, observed)
    check("margin_series ignores model-only rows", len(series), 1)


def test_rolling_margin_is_date_based():
    """Weekly rows then daily rows — the spacing that breaks row-based windows.

    Six weekly observations, then one daily row 200 days later. A 90-day window
    anchored at the last row must see only that row's neighbourhood, so it has
    fewer than min_obs and emits nothing. A "last 6 rows" window would happily
    reach back seven months and emit a number.
    """
    rows = [row(f"2026-01-{d:02d}", 0.60, survey=1.70) for d in (5, 12, 19, 26)]
    rows += [row("2026-02-02", 0.60, survey=1.70), row("2026-02-09", 0.60, survey=1.70)]
    rows += [row("2026-08-28", 0.60, survey=1.90)]
    series = analytics.margin_series(rows, observed)
    rolled = analytics.rolling_margin(series, days=90, min_obs=5)
    dates = [d for d, _ in rolled]
    check("rolling_margin emits nothing for the isolated late row",
          "2026-08-28" in dates, False)
    check("rolling_margin emits once the window holds 5", len(dates) >= 1, True)


def test_rolling_margin_tracks_a_drift():
    """The 27 -> 15 c/L drift must show as a falling line, not a flat median."""
    rows = []
    for i in range(120):
        m = 0.27 - (0.12 * i / 119)
        d = dt.date(2026, 1, 1) + dt.timedelta(days=i)
        rows.append(row(d.isoformat(), 0.60,
                        survey=model.retail_from_wholesale(0.60, m)))
    series = analytics.margin_series(rows, observed)
    rolled = analytics.rolling_margin(series)
    check("rolling margin falls with the drift", rolled[-1][1] < rolled[0][1], True)
    check("rolling margin stays inside the true range",
          0.15 <= rolled[-1][1] <= 0.27, True)


# --- forecast accuracy -----------------------------------------------------

def test_forecast_error_scores_against_the_no_move_baseline():
    actuals = {"2026-01-01": 1.700, "2026-01-02": 1.720}
    forecasts = [{"made_on": "2026-01-01", "target_date": "2026-01-02",
                  "horizon": "1", "variant": "passthrough",
                  "predicted": "1.7100", "basis": "observed"}]
    out = analytics.forecast_error(forecasts, actuals)
    check("one bucket", len(out), 1)
    # predicted 1.710 vs actual 1.720 -> 1.0 c/L out. Baseline held 1.700 -> 2.0.
    check("mae in cents", out[0]["mae_cents"], 1.0, 1e-6)
    check("bias is signed", out[0]["bias_cents"], -1.0, 1e-6)
    check("baseline mae in cents", out[0]["baseline_mae_cents"], 2.0, 1e-6)
    check("edge is baseline minus model", out[0]["edge_cents"], 1.0, 1e-6)


def test_forecast_error_needs_both_ends():
    """A forecast whose origin price is unknown is unscorable against a baseline.

    Dropping it keeps the model and the no-move baseline on the identical
    sample; scoring the model on days the baseline cannot see would let a
    variant win by being lucky about which days it had data for.
    """
    actuals = {"2026-01-02": 1.720}          # no price for the origin date
    forecasts = [{"made_on": "2026-01-01", "target_date": "2026-01-02",
                  "horizon": "1", "variant": "passthrough",
                  "predicted": "1.7100", "basis": "observed"}]
    check("unscorable forecast dropped", analytics.forecast_error(forecasts, actuals), [])


def test_hold_variant_is_exactly_the_baseline():
    """The 'hold' variant must score identically to the built-in baseline.

    If these ever diverge, one of the two is computing a different sample, and
    every edge number on the dashboard is measured against the wrong floor.
    """
    actuals = {"2026-01-01": 1.700, "2026-01-02": 1.720, "2026-01-03": 1.680}
    forecasts = []
    for target, horizon in (("2026-01-02", 1), ("2026-01-03", 2)):
        forecasts.append({"made_on": "2026-01-01", "target_date": target,
                          "horizon": str(horizon), "variant": "hold",
                          "predicted": "1.7000", "basis": "observed"})
    for b in analytics.forecast_error(forecasts, actuals):
        check(f"hold edge is zero at h={b['horizon']}", b["edge_cents"], 0.0, 1e-9)


# --- stations --------------------------------------------------------------

def test_station_dispersion():
    bench = {"2026-01-01": 1.80, "2026-01-02": 1.75, "2026-01-03": 1.85}
    prices = [("2026-01-01", "a", 1.70, "logged"),      # -0.10
              ("2026-01-02", "a", 1.66, "logged"),      # -0.09
              ("2026-01-03", "a", 1.74, "logged")]      # -0.11
    out = analytics.station_dispersion(prices, bench, today="2026-01-03")
    check("offset is the median delta", out["a"]["median"], -0.10, 1e-9)
    check("mad is the median absolute deviation", out["a"]["mad"], 0.01, 1e-9)
    check("n counts observations", out["a"]["n"], 3)
    check("span in days", out["a"]["span_days"], 2)


def test_station_dispersion_ignores_undated_benchmark():
    """An observation with no benchmark for its date cannot become an offset.

    stations.compute_offsets has the same rule; if this drifts, the dashboard
    would show a station as better-calibrated than the model treats it.
    """
    bench = {"2026-01-01": 1.80}
    prices = [("2026-01-01", "a", 1.70, "logged"),
              ("2026-01-02", "a", 1.60, "logged")]      # no benchmark that day
    out = analytics.station_dispersion(prices, bench, today="2026-01-02")
    check("unmatched observation excluded", out["a"]["n"], 1)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"{name}:")
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all analytics checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
