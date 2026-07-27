"""Per-station prices, modelled as a stable offset from the regional benchmark.

The idea that makes manual logging practical: you cannot log 17 stations every
day, but you don't need to. What separates stations is far more stable than
what moves them together. Beaver Creek sits ~9 c/L under the Toronto average
because of brand, volume and local competition — that's structural, and it
holds for weeks. The day-to-day swings are regional and already modelled.

So each station gets one number, its median offset from the benchmark:

    offset[s] = median(observed_price[s, d] - benchmark[d])

and its predicted price on any day is benchmark + offset. Log a station once
and it's roughly calibrated; log it a few times and it's solid. A station you
haven't logged in a month still tracks the market, it just carries whatever
offset you last measured.

Offsets are median-based and time-windowed for the same reasons the margin is
(see model.py): one mistyped price shouldn't move a station, and a station that
genuinely repositions should be allowed to drift.
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import statistics
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent
STATIONS = ROOT / "stations.csv"
PRICES = ROOT / "station_prices.csv"

# Offsets drift slowly (a station repositions, a new competitor opens), so this
# is much longer than the 90-day margin window. Still bounded, so a station
# that changes its pricing isn't anchored to last year forever.
OFFSET_WINDOW_DAYS = 180

# Below this many observations the offset is a guess from very little evidence.
# Still used — one observation beats none — but flagged so the UI can hedge.
CONFIDENT_OBSERVATIONS = 3


@dataclass
class Station:
    id: str
    brand: str
    address: str
    city: str
    role: str            # regular | favourite | tracked
    label: str
    offset: float | None = None       # $/L vs benchmark; None = never observed
    observations: int = 0
    last_seen: str = ""
    predicted: float | None = None    # filled in by predict_all()
    history: list[tuple[str, float]] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.observations >= CONFIDENT_OBSERVATIONS


def load_stations() -> dict[str, Station]:
    out: dict[str, Station] = {}
    if not STATIONS.exists():
        return out
    with STATIONS.open() as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    for r in csv.DictReader(rows):
        if not (r.get("id") or "").strip():
            continue
        out[r["id"]] = Station(
            id=r["id"].strip(),
            brand=(r.get("brand") or "").strip(),
            address=(r.get("address") or "").strip(),
            city=(r.get("city") or "").strip(),
            role=(r.get("role") or "tracked").strip(),
            label=(r.get("label") or r["id"]).strip(),
        )
    return out


def load_prices() -> list[tuple[str, str, float, str]]:
    """[(date, station_id, price, source), ...] oldest first."""
    if not PRICES.exists():
        return []
    out = []
    with PRICES.open() as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["date"], r["station_id"], float(r["price"]),
                            (r.get("source") or "").strip()))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort()
    return out


def compute_offsets(stations: dict[str, Station],
                    benchmark: dict[str, float],
                    today: str,
                    window_days: int = OFFSET_WINDOW_DAYS) -> None:
    """Fill in offset/observations/last_seen for each station, in place.

    `benchmark` maps date -> the regional price on that date. A station price
    is only usable if we know what the region was doing that day, otherwise the
    difference means nothing.
    """
    cutoff = (dt.date.fromisoformat(today)
              - dt.timedelta(days=window_days)).isoformat()

    diffs: dict[str, list[float]] = {}
    for date, sid, price, _src in load_prices():
        st = stations.get(sid)
        if st is None or date < cutoff:
            continue
        st.history.append((date, price))
        st.last_seen = max(st.last_seen, date)
        base = benchmark.get(date)
        if base is not None:
            diffs.setdefault(sid, []).append(price - base)

    for sid, vals in diffs.items():
        stations[sid].offset = statistics.median(vals)
        stations[sid].observations = len(vals)


def predict_all(stations: dict[str, Station], benchmark_today: float) -> None:
    """Today's expected price at every station with a known offset."""
    for st in stations.values():
        st.predicted = None if st.offset is None else benchmark_today + st.offset


def cheapest(stations: dict[str, Station],
             roles: tuple[str, ...] | None = None) -> Station | None:
    """Cheapest station with a prediction, optionally restricted by role."""
    pool = [s for s in stations.values()
            if s.predicted is not None and (roles is None or s.role in roles)]
    return min(pool, key=lambda s: s.predicted) if pool else None


def summary(stations: dict[str, Station]) -> list[dict]:
    """Compact per-station rows for data.json, cheapest first."""
    rows = []
    for st in sorted(stations.values(),
                     key=lambda s: (s.predicted is None, s.predicted or 0)):
        if st.predicted is None:
            continue
        rows.append({
            "id": st.id,
            "label": st.label,
            "city": st.city,
            "role": st.role,
            "price": int(round(st.predicted * 1000)),
            "offset": int(round((st.offset or 0) * 1000)),
            "n": st.observations,
            "seen": st.last_seen,
        })
    return rows
