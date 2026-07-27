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

import stations as stationlib  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY = ROOT / "backend" / "history.csv"
PRICES = ROOT / "backend" / "station_prices.csv"
FIELDS = ["date", "rbob_usd_gal", "usd_cad", "wholesale_cad_l",
          "retail_model", "retail_survey", "retail_actual", "margin"]


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
            r["retail_actual"] = f"{price:.3f}"
            break
    else:
        rows.append({"date": day, "retail_actual": f"{price:.3f}"})
        rows.sort(key=lambda r: r["date"])

    with HISTORY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    n = sum(1 for r in rows if (r.get("retail_actual") or "").strip())
    print(f"logged {price:.3f} $/L for {day} (regional benchmark, {n} observed)")


def log_station(price: float, day: str, sid: str, label: str) -> None:
    rows: list[dict] = []
    if PRICES.exists():
        with PRICES.open() as f:
            rows = [r for r in csv.DictReader(f) if r.get("date")]

    for r in rows:
        if r["date"] == day and r["station_id"] == sid:
            r["price"], r["source"] = f"{price:.3f}", "logged"
            break
    else:
        rows.append({"date": day, "station_id": sid,
                     "price": f"{price:.3f}", "source": "logged"})
    rows.sort(key=lambda r: (r["date"], r["station_id"]))

    with PRICES.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "station_id", "price", "source"])
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
    dt.date.fromisoformat(args.date)

    if args.station:
        sid = resolve(args.station, known)
        if sid is None:
            return 2
        log_station(args.price, args.date, sid, known[sid].label)
    else:
        log_regional(args.price, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
