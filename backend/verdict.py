"""Python mirror of firmware/gasprices/verdict.h.

Same units (tenths of a cent per litre), same rules, same order. Kept in sync by
test_verdict.py, which runs both implementations against tests/vectors.csv.

Why two copies? The ESP32 runs the C version so it still gives an answer when the
backend is down or stale. The backend runs this one so you can replay a month of
history and tune thresholds in seconds instead of reflashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

PRED_MAX = 7


class Verdict(str, Enum):
    STALE = "STALE"
    FILL_NOW = "FILL_NOW"
    WAIT = "WAIT"
    GREAT = "GREAT"
    EXPENSIVE = "EXPENSIVE"
    NEUTRAL = "NEUTRAL"


class Tank(str, Enum):
    FULL = "FULL"
    HALF = "HALF"
    LOW = "LOW"


@dataclass
class Config:
    save_threshold: int = 15        # 1.5 c/L — below this, not worth a special trip
    low_pct: int = 20
    high_pct: int = 80
    jump_level_cap: int = 60
    max_age_minutes: int = 36 * 60
    horizon: int = 5
    patience_full_pct: int = 200
    patience_half_pct: int = 100


@dataclass
class Input:
    today: int
    pred: list[int] = field(default_factory=list)
    window_lo: int = 0
    window_hi: int = 0
    age_minutes: int = 0            # -1 = unknown
    tank: Tank = Tank.HALF


@dataclass
class Result:
    verdict: Verdict
    level_pct: int = -1
    tomorrow_jump: int = 0
    days_to_wait: int = 0
    save: int = 0
    threshold: int = 0
    urgent_override: bool = False
    reason: str = ""


def _cents(tenths: int) -> str:
    sign = "-" if tenths < 0 else ""
    a = abs(tenths)
    return f"{sign}{a // 10}.{a % 10}c"


def fmt_price(tenths: int) -> str:
    return f"${tenths // 1000}.{tenths % 1000:03d}"


def evaluate(inp: Input, cfg: Config | None = None) -> Result:
    cfg = cfg or Config()
    r = Result(verdict=Verdict.NEUTRAL, threshold=cfg.save_threshold)

    if inp.age_minutes >= 0 and inp.age_minutes > cfg.max_age_minutes:
        r.verdict = Verdict.STALE
        r.level_pct = -1
        r.reason = "Stale - check wifi"
        return r

    # Signal 1: level within the rolling window.
    span = inp.window_hi - inp.window_lo
    if span > 0:
        r.level_pct = max(0, min(100, ((inp.today - inp.window_lo) * 100) // span))

    mult = cfg.patience_full_pct if inp.tank is Tank.FULL else cfg.patience_half_pct
    r.threshold = (cfg.save_threshold * mult) // 100

    # Signal 2: direction over the prediction horizon.
    pred = inp.pred[: min(cfg.horizon, PRED_MAX)]
    if pred:
        r.tomorrow_jump = pred[0] - inp.today
        r.save = max(0, inp.today - min(pred))
        for d, p in enumerate(pred):
            if p <= inp.today - r.threshold:
                r.days_to_wait = d + 1
                break

    # Urgency override: running dry beats saving three cents.
    if inp.tank is Tank.LOW:
        r.urgent_override = True
        r.days_to_wait = 0
        r.verdict = Verdict.FILL_NOW
        r.reason = "Tank low: fill anyway"
        return r

    if r.tomorrow_jump >= r.threshold and 0 <= r.level_pct <= cfg.jump_level_cap:
        r.verdict = Verdict.FILL_NOW
        r.reason = f"Jumps {_cents(r.tomorrow_jump)} tomorrow"
    elif r.days_to_wait > 0:
        r.verdict = Verdict.WAIT
        plural = "" if r.days_to_wait == 1 else "s"
        r.reason = f"Save {_cents(r.save)} in {r.days_to_wait} day{plural}"
    elif 0 <= r.level_pct <= cfg.low_pct:
        r.verdict = Verdict.GREAT
        r.reason = "Bottom of range - go"
    elif r.level_pct >= cfg.high_pct:
        r.verdict = Verdict.EXPENSIVE
        r.reason = "High, no dip coming"
    else:
        r.verdict = Verdict.NEUTRAL
        r.reason = "Fair price"
    return r
