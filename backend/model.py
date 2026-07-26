"""Wholesale -> Ontario pump price, and the forward price model.

Everything here is in dollars per litre until the very end, where prices are
converted to tenths of a cent for the JSON the ESP32 eats.
"""

from __future__ import annotations

import datetime as dt
import statistics

LITRES_PER_US_GAL = 3.785411784

# --- Ontario tax stack, $/L unless noted -----------------------------------
# VERIFY THESE before trusting the output. Fuel tax rates move, and the federal
# consumer carbon charge on gasoline was removed in April 2025. If your modelled
# price is off by a constant, it is almost always one of these or MARGIN.
HST = 0.13                 # applied on top of everything, including the taxes
FEDERAL_EXCISE = 0.10
ONTARIO_GAS_TAX = 0.09
CARBON_CHARGE = 0.00

# Refining + wholesale + retail margin + freight. Cold-start guess only —
# calibrate_margin() re-derives it from real prices as soon as it has any.
#
# This is NOT a constant of nature. Measured against Ontario's weekly survey,
# the GTA margin ran ~27 c/L in late 2025 and fell to ~15 c/L by mid-2026. Any
# fixed value goes stale, which is why the calibration window below is short.
DEFAULT_MARGIN = 0.15

# Only look this far back when calibrating. Long enough to average out noise,
# short enough to track the drift above. Deliberately time-based, not row-based:
# backfilled survey rows are weekly while live rows are daily, so "last N rows"
# would silently mean five months of history in one case and three weeks in the
# other.
MARGIN_WINDOW_DAYS = 90

# Fraction of the gap to equilibrium closed per day.
#
# These started as a rockets-and-feathers asymmetry (up fast, down slow). That
# turned out to be wrong here, and wrong for an instructive reason: measured
# against a *trailing* margin, "up" gaps appeared to close only 40% per week.
# But a trailing margin lags a drifting one, biasing the target +1.74 c/L high
# and manufacturing upward gaps that were never real. Recomputed against a
# centered (unbiased) margin, the asymmetry vanishes and full convergence wins
# in both directions: 7-day MAE 3.45 c/L up / 3.55 down at weight 1.0, versus
# 4.18 / 3.76 for assuming no move at all.
#
# So: symmetric, and fast. Weekly survey data cannot resolve the daily rate any
# further — anything above ~0.35/day looks complete after 7 days — but a spot
# check (1.819 -> 1.799 over 2 days, against a target of 1.7987) is consistent
# with this. 0.5 closes 75% in two days and 99% in seven.
PASSTHROUGH_UP = 0.50
PASSTHROUGH_DOWN = 0.50

# Wholesale moves lead the pump by a couple of days; smooth to that timescale.
WHOLESALE_EMA_DAYS = 3


def to_tenths(dollars_per_litre: float) -> int:
    """$1.4894/L -> 1489 (tenths of a cent, the firmware's unit)."""
    return int(round(dollars_per_litre * 1000))


def wholesale_cad_per_litre(rbob_usd_per_gal: float, usd_cad: float) -> float:
    return rbob_usd_per_gal * usd_cad / LITRES_PER_US_GAL


def taxes_per_litre() -> float:
    return FEDERAL_EXCISE + ONTARIO_GAS_TAX + CARBON_CHARGE


def retail_from_wholesale(wholesale: float, margin: float = DEFAULT_MARGIN) -> float:
    """Equilibrium pump price implied by wholesale, if passthrough were instant."""
    return (wholesale + margin + taxes_per_litre()) * (1.0 + HST)


def implied_margin(retail: float, wholesale: float) -> float:
    """Invert the stack: what margin does an observed pump price imply?"""
    return retail / (1.0 + HST) - taxes_per_litre() - wholesale


def ema(values: list[float], days: int = WHOLESALE_EMA_DAYS) -> float:
    """Exponential moving average, oldest first. Approximates the pump's lag."""
    if not values:
        raise ValueError("ema() of empty series")
    k = 2.0 / (days + 1.0)
    out = values[0]
    for v in values[1:]:
        out = v * k + out * (1.0 - k)
    return out


def calibrate_margin(observations: list[tuple[str, float, float]],
                     days: int = MARGIN_WINDOW_DAYS,
                     fallback: float = DEFAULT_MARGIN) -> float:
    """observations = [(YYYY-MM-DD, observed_retail, smoothed_wholesale), ...].

    Median (not mean) so one mistyped pump price can't drag the whole model.
    Needs 5 observations inside the window before it beats the hand-set default.
    """
    if not observations:
        return fallback

    newest = dt.date.fromisoformat(max(d for d, _, _ in observations))
    cutoff = (newest - dt.timedelta(days=days)).isoformat()
    recent = [(r, w) for d, r, w in observations if d >= cutoff]
    if len(recent) < 5:
        return fallback
    return statistics.median(implied_margin(r, w) for r, w in recent)


def predict(today_retail: float, target_retail: float, horizon: int = 5,
            up: float = PASSTHROUGH_UP, down: float = PASSTHROUGH_DOWN) -> list[float]:
    """Forward pump prices, pred[0] = tomorrow.

    The model is deliberately boring: today's pump price hasn't finished
    absorbing where wholesale already is, so each day it closes a fixed fraction
    of the gap toward the equilibrium price — fast on the way up, slow on the
    way down. No futures curve required, and it captures the only thing the
    verdict engine actually needs: is a cheaper day coming, and how soon.
    """
    p = today_retail
    out: list[float] = []
    for _ in range(horizon):
        gap = target_retail - p
        p += gap * (up if gap > 0 else down)
        out.append(round(p, 4))
    return out
