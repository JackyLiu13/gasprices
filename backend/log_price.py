"""Log a price you actually saw. This is what calibrates everything.

    python3 backend/log_price.py 1.799                      # regional, today
    python3 backend/log_price.py 1.709 --station beaver     # a specific station
    python3 backend/log_price.py 1.709 -s beaver -d 2026-07-26
    python3 backend/log_price.py --list                     # station ids

Without --station the price is treated as the regional benchmark (history.csv).
With --station it goes to station_prices.csv and refines that station's offset,
which is what lets one logged price keep predicting for weeks.

Station ids match on any unambiguous substring, so `-s beaver` finds
`esso-beaver-creek`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import schema  # noqa: E402
import stations as stationlib  # noqa: E402
from schema import HISTORY_FIELDS as FIELDS  # noqa: E402
from schema import STATION_PRICE_FIELDS  # noqa: E402

from paths import HISTORY  # noqa: E402
from paths import STATION_PRICES as PRICES  # noqa: E402


def warn_overwrite(existing: str | None, price: float, what: str) -> None:
    """Say so when a logged price replaces one that was already there.

    Silently overwriting is fine from the CLI, where you had to type the date to
    hit an old row. From the dashboard form the date defaults to today, so
    logging twice is a normal accident — and quietly replacing a number that
    calibration depends on is the kind of thing you want to hear about.
    """
    prev = (existing or "").strip()
    if prev and abs(float(prev) - price) > 1e-9:
        print(f"note: replacing {prev} with {price:.3f} for {what}", file=sys.stderr)


def resolve(query: str, known: dict) -> str | None:
    """Exact id, else unique substring match."""
    if query in known:
        return query
    hits = [sid for sid in known if query.lower() in sid.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"no station matching {query!r}. --list to see them.", file=sys.stderr)
    else:
        print(f"{query!r} is ambiguous: {', '.join(hits)}", file=sys.stderr)
    return None


def log_regional(price: float, day: str) -> None:
    rows: list[dict] = []
    if HISTORY.exists():
        with HISTORY.open() as f:
            rows = [r for r in csv.DictReader(f) if r.get("date")]

    for r in rows:
        if r["date"] == day:
            warn_overwrite(r.get("retail_actual"), price, f"{day} (regional)")
            r["retail_actual"] = f"{price:.3f}"
            break
    else:
        rows.append({"date": day, "retail_actual": f"{price:.3f}"})
        rows.sort(key=lambda r: r["date"])

    with HISTORY.open("w", newline="") as f:
        w = schema.writer(f, FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    n = sum(1 for r in rows if (r.get("retail_actual") or "").strip())
    print(f"logged {price:.3f} $/L for {day} (regional benchmark, {n} observed)")


def log_station(price: float, day: str, sid: str, label: str,
                source: str = "logged") -> None:
    rows: list[dict] = []
    if PRICES.exists():
        with PRICES.open() as f:
            rows = [r for r in csv.DictReader(f) if r.get("date")]

    for r in rows:
        if r["date"] == day and r["station_id"] == sid:
            warn_overwrite(r.get("price"), price, f"{label} on {day}")
            r["price"], r["source"] = f"{price:.3f}", source
            break
    else:
        rows.append({"date": day, "station_id": sid,
                     "price": f"{price:.3f}", "source": source})
    rows.sort(key=lambda r: (r["date"], r["station_id"]))

    with PRICES.open("w", newline="") as f:
        w = schema.writer(f, STATION_PRICE_FIELDS)
        w.writeheader()
        w.writerows(rows)

    n = sum(1 for r in rows if r["station_id"] == sid)
    print(f"logged {price:.3f} $/L at {label} ({sid}) for {day}")
    print(f"  {n} observation(s) for this station"
          + ("" if n >= stationlib.CONFIDENT_OBSERVATIONS
             else f" — {stationlib.CONFIDENT_OBSERVATIONS} makes the offset solid"))


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("price", nargs="?", type=float)
    ap.add_argument("-s", "--station", help="station id or unique substring")
    ap.add_argument("-d", "--date", default=dt.date.today().isoformat())
    ap.add_argument("--list", action="store_true", help="list station ids")
    # Provenance matters: a price you read off the pump and one copied from a
    # crowd-sourced listing deserve different levels of trust later, and the
    # column is the only place that distinction survives.
    ap.add_argument("--source", default="logged",
                    help="where the price came from (default: logged)")
    args = ap.parse_args()

    known = stationlib.load_stations()

    if args.list:
        for st in sorted(known.values(), key=lambda s: (s.city, s.role, s.id)):
            print(f"  {st.id:26} {st.role:9} {st.city:14} {st.address}")
        return 0

    if args.price is None:
        ap.print_help()
        return 2
    if not 0.5 <= args.price <= 3.5:
        print(f"{args.price} $/L doesn't look like a pump price — typo?", file=sys.stderr)
        return 2
    try:
        when = dt.date.fromisoformat(args.date)
    except ValueError:
        print(f"{args.date!r} is not a date (want YYYY-MM-DD)", file=sys.stderr)
        return 2
    if when > dt.date.today():
        # A future-dated observation is either a typo or a guess. Either way it
        # would sit at the newest end of the calibration window and anchor
        # today's level to a price nobody has seen.
        print(f"{args.date} is in the future — a price you haven't seen yet "
              "would anchor the model", file=sys.stderr)
        return 2

    if args.station:
        sid = resolve(args.station, known)
        if sid is None:
            return 2
        log_station(args.price, args.date, sid, known[sid].label, args.source)
    else:
        log_regional(args.price, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
