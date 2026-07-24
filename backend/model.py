"""Wholesale -> Ontario pump price, and the forward price model.

Everything here is in dollars per litre until the very end, where prices are
converted to tenths of a cent for the JSON the ESP32 eats.
"""

from __future__ import annotations

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

# Refining + wholesale + retail margin + freight. This is the one number you do
# NOT guess: calibrate_margin() re-derives it from prices you actually logged.
DEFAULT_MARGIN = 0.16

# Rockets and feathers: retail chases a wholesale increase much faster than it
# passes a decrease along. These are the fraction of the remaining gap closed
# per day. Tune against real outcomes (see README phase 4).
PASSTHROUGH_UP = 0.60
PASSTHROUGH_DOWN = 0.25

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


def calibrate_margin(pairs: list[tuple[float, float]], window: int = 21,
                     fallback: float = DEFAULT_MARGIN) -> float:
    """pairs = [(observed_retail, smoothed_wholesale), ...] oldest first.

    Median (not mean) so one mistyped pump price can't drag the whole model.
    Needs ~5 observations before it beats the hand-set default.
    """
    recent = pairs[-window:]
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
