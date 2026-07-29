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
import math
import pathlib
import statistics
from dataclasses import dataclass, field

from schema import DEFAULT_OBS_TIME  # noqa: E402

from paths import STATIONS  # noqa: E402
from paths import STATION_PRICES as PRICES  # noqa: E402

# Offsets drift slowly (a station repositions, a new competitor opens), so this
# is much longer than the 90-day margin window. Still bounded, so a station
# that changes its pricing isn't anchored to last year forever.
OFFSET_WINDOW_DAYS = 180

# Below this many observations the offset is a guess from very little evidence.
# Still used — one observation beats none — but flagged so the UI can hedge.
#
# Counted in DAYS, not rows. Since observations carry a time, you can log the
# same station four times in an afternoon; that is four samples of one day's
# pricing, not four independent measurements of an offset that only moves over
# weeks. Counting rows would let a single trip light up `confident` on the
# device, which reads this same number out of data.json (gasprices.ino).
CONFIDENT_OBSERVATIONS = 3


@dataclass
class Station:
    id: str
    brand: str
    address: str
    city: str
    role: str            # regular | favourite | tracked
    label: str
    # WGS84 decimal degrees, or None where nobody has looked them up yet. Read
    # only by clients sorting the registry by distance — never by the offset
    # model. See schema.STATION_FIELDS.
    lat: float | None = None
    lon: float | None = None
    offset: float | None = None       # $/L vs benchmark; None = never observed
    observations: int = 0             # distinct DAYS observed — see below
    samples: int = 0                  # individual prices logged
    last_seen: str = ""
    predicted: float | None = None    # filled in by predict_all()
    history: list[tuple[str, float]] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.observations >= CONFIDENT_OBSERVATIONS


def parse_coord(raw: str | None, lo: float, hi: float, what: str, sid: str) -> float | None:
    """Parse one optional WGS84 coordinate.

    Blank is None, because a station with no coordinates is an ordinary station
    (schema.py). A *non-blank* value that will not parse is not ordinary: it
    means someone typed a coordinate and got it wrong, and quietly reading that
    as None would drop the station out of every proximity sort with nothing
    anywhere to notice. Raise instead — build.py fails, and the last good
    data.json stays published.

    What this does NOT catch is a swapped pair. At this latitude lat=-79.4 and
    lon=43.9 are both in range — a real point in the Southern Ocean — so the
    check that rejects it is geocode_stations.BBOX, upstream of the file, not
    anything here. See test_swapped_coordinates_are_not_caught_by_range.
    """
    s = (raw or "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        raise ValueError(f"{sid}: {what}={s!r} is not a number") from None
    if not lo <= v <= hi:
        raise ValueError(f"{sid}: {what}={v} is outside [{lo}, {hi}]")
    return v


def meters_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular approximation, in metres.

    Exact to well under a metre across a metro area, which is the only scale
    this is ever asked about. Haversine would be more correct over hundreds of
    km and no more correct here.
    """
    lat1, lon1 = a
    lat2, lon2 = b
    mean_lat = math.radians((lat1 + lat2) / 2)
    dx = math.radians(lon2 - lon1) * math.cos(mean_lat)
    dy = math.radians(lat2 - lat1)
    return math.hypot(dx, dy) * 6_371_000


# How far away a station can be and still plausibly be the one you are standing
# at. Generous: GPS in a covered forecourt drifts, and being offered a station
# 600 m away costs one glance, while not being offered the right one at all
# costs the log entirely.
NEAR_M = 1_000.0


def nearest(lat: float, lon: float, stations: dict[str, Station],
            limit: int = 3, within_m: float = NEAR_M) -> list[tuple[Station, float]]:
    """The closest located stations to a point, nearest first, with distances.

    Returns candidates for a human to choose from — deliberately not "the"
    station. Two of the registry's stations sit 94 m apart on opposite corners
    of Bayview & Major Mackenzie, which is inside consumer GPS error, so a
    single auto-picked answer would be wrong there roughly as often as right.
    Logging a price against the wrong station corrupts two offsets at once (the
    one that gains a price it never charged, and the one that loses the price it
    did), and with every station at n=1 there is nothing to average that out.

    Stations with no coordinates are skipped, not ranked last: "unknown
    distance" is not "far away", and pretending otherwise would put them in a
    list sorted by a number they do not have.
    """
    here = (lat, lon)
    scored = [(s, meters_between(here, (s.lat, s.lon)))
              for s in stations.values() if s.lat is not None and s.lon is not None]
    scored.sort(key=lambda pair: pair[1])
    return [(s, d) for s, d in scored if d <= within_m][:limit]


def load_stations() -> dict[str, Station]:
    out: dict[str, Station] = {}
    if not STATIONS.exists():
        return out
    with STATIONS.open() as f:
        rows = [ln for ln in f if not ln.lstrip().startswith("#")]
    for r in csv.DictReader(rows):
        if not (r.get("id") or "").strip():
            continue
        sid = r["id"].strip()
        out[sid] = Station(
            id=sid,
            brand=(r.get("brand") or "").strip(),
            address=(r.get("address") or "").strip(),
            city=(r.get("city") or "").strip(),
            role=(r.get("role") or "tracked").strip(),
            label=(r.get("label") or r["id"]).strip(),
            lat=parse_coord(r.get("lat"), -90.0, 90.0, "lat", sid),
            lon=parse_coord(r.get("lon"), -180.0, 180.0, "lon", sid),
        )
    return out


def load_prices() -> list[tuple[str, str, str, float, str]]:
    """[(date, time, station_id, price, source), ...] oldest first.

    A row with no time reads as DEFAULT_OBS_TIME rather than being dropped: the
    seed rows predate the column, and a hand-edited row that omits it is still a
    real price. What is lost is only when in the day it was seen.
    """
    if not PRICES.exists():
        return []
    out = []
    with PRICES.open() as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["date"], (r.get("time") or "").strip() or DEFAULT_OBS_TIME,
                            r["station_id"], float(r["price"]),
                            (r.get("source") or "").strip()))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort()
    return out


def orphans(stations: dict[str, Station] | None = None) -> dict[str, int]:
    """Station ids in station_prices.csv with no row in stations.csv.

    These are dropped everywhere and nowhere is it mentioned: compute_offsets
    skips them (`stations.get(sid)` is None), and db.py's v_station_priced
    inner-joins them away. A registry id edited by hand, or a row deleted from
    stations.csv while its prices stayed, loses observations in total silence.
    So this is checked and printed rather than left to be noticed.
    """
    known = stations if stations is not None else load_stations()
    missing: dict[str, int] = {}
    for _date, _time, sid, _price, _src in load_prices():
        if sid not in known:
            missing[sid] = missing.get(sid, 0) + 1
    return missing


def compute_offsets(stations: dict[str, Station],
                    benchmark: dict[str, float],
                    today: str,
                    window_days: int = OFFSET_WINDOW_DAYS) -> None:
    """Fill in offset/observations/last_seen for each station, in place.

    `benchmark` maps date -> the regional price on that date. A station price
    is only usable if we know what the region was doing that day, otherwise the
    difference means nothing.

    Prices are collapsed to one delta per day before the median is taken. The
    benchmark is a daily number, so several prices on one date are several
    samples of the *same* comparison; medianing them flat would let an afternoon
    spent logging one station outvote a month of single daily observations.
    """
    cutoff = (dt.date.fromisoformat(today)
              - dt.timedelta(days=window_days)).isoformat()

    diffs: dict[str, dict[str, list[float]]] = {}
    for date, _time, sid, price, _src in load_prices():
        st = stations.get(sid)
        if st is None or date < cutoff:
            continue
        st.history.append((date, price))
        st.samples += 1
        st.last_seen = max(st.last_seen, date)
        base = benchmark.get(date)
        if base is not None:
            diffs.setdefault(sid, {}).setdefault(date, []).append(price - base)

    for sid, by_day in diffs.items():
        daily = [statistics.median(v) for v in by_day.values()]
        stations[sid].offset = statistics.median(daily)
        stations[sid].observations = len(daily)


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


def baseline(stations: dict[str, Station]) -> Station | None:
    """The station savings are measured against.

    Your `home` station if one is marked — savings then mean "versus filling up
    where I normally would", which is the number worth acting on. Falls back to
    the cheapest `regular` so a registry with no home set still works.
    """
    return cheapest(stations, roles=("home",)) or cheapest(stations, roles=("regular",))


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
