"""Log a price you actually saw. This is what calibrates everything.

    python3 backend/log_price.py 1.799                      # regional, today
    python3 backend/log_price.py 1.709 --station beaver     # a specific station
    python3 backend/log_price.py 1.709 -s beaver -d 2026-07-26
    python3 backend/log_price.py 1.709 -s beaver --ago 45   # saw it 45 min ago
    python3 backend/log_price.py 1.709 -s beaver -t 17:40   # or an exact time
    python3 backend/log_price.py --remove -s beaver -t 17:40   # undo a typo
    python3 backend/log_price.py --list                     # station ids

Without --station the price is treated as the regional benchmark (history.csv).
With --station it goes to station_prices.csv and refines that station's offset,
which is what lets one logged price keep predicting for weeks.

Station prices carry a time, so logging the same station twice in a day records
two observations instead of overwriting. The regional series does not: it is one
number per date, joined on date by everything downstream, and a time there would
buy nothing the survey can support.

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
from schema import DEFAULT_OBS_TIME  # noqa: E402
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
    """Exact id, else exact label, else unique substring of an id.

    Labels are matched because they are what a *person* sees — the panel header,
    the dashboard ladder, and the list a phone offers you after asking where you
    are. Requiring the caller to carry an id around so it can be translated back
    into the label it came from is work with no purpose. Exact and
    case-insensitive rather than fuzzy: labels contain spaces and the substring
    pass below already covers approximate typing.
    """
    if query in known:
        return query
    by_label = [sid for sid, s in known.items()
                if s.label.strip().lower() == query.strip().lower()]
    if len(by_label) == 1:
        return by_label[0]
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


def load_station_rows() -> list[dict]:
    """station_prices.csv as dicts, with the time column defaulted.

    Rows written before the column existed have no time. Reading them as
    DEFAULT_OBS_TIME here means the rewrite below fills them in, so the file
    converges on being explicit instead of carrying blanks forever.
    """
    if not PRICES.exists():
        return []
    with PRICES.open() as f:
        rows = [r for r in csv.DictReader(f) if r.get("date")]
    for r in rows:
        if not (r.get("time") or "").strip():
            r["time"] = DEFAULT_OBS_TIME
    return rows


def write_station_rows(rows: list[dict]) -> None:
    rows.sort(key=lambda r: (r["date"], r["time"], r["station_id"]))
    with PRICES.open("w", newline="") as f:
        w = schema.writer(f, STATION_PRICE_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in STATION_PRICE_FIELDS} for r in rows)


def observed_days(rows: list[dict], sid: str) -> int:
    """Distinct dates, not rows — what CONFIDENT_OBSERVATIONS counts."""
    return len({r["date"] for r in rows if r["station_id"] == sid})


def log_station(price: float, day: str, sid: str, label: str,
                source: str = "logged", time: str = DEFAULT_OBS_TIME) -> None:
    rows = load_station_rows()

    for r in rows:
        if (r["date"], r["time"], r["station_id"]) == (day, time, sid):
            warn_overwrite(r.get("price"), price, f"{label} on {day} {time}")
            r["price"], r["source"] = f"{price:.3f}", source
            break
    else:
        rows.append({"date": day, "time": time, "station_id": sid,
                     "price": f"{price:.3f}", "source": source})
    write_station_rows(rows)

    days = observed_days(rows, sid)
    samples = sum(1 for r in rows if r["station_id"] == sid)
    extra = "" if samples == days else f" across {samples} prices"
    print(f"logged {price:.3f} $/L at {label} ({sid}) for {day} {time}")
    print(f"  {days} day(s) observed for this station{extra}"
          + ("" if days >= stationlib.CONFIDENT_OBSERVATIONS
             else f" — {stationlib.CONFIDENT_OBSERVATIONS} makes the offset solid"))


def remove_station_price(day: str, time: str, sid: str, label: str) -> int:
    """Delete one observation. Returns an exit code.

    Keyed on the full (date, time, station) so removing a mistyped price cannot
    take a good one with it. Never touches history.csv — the regional series is
    one row per date and dropping one silently reshapes the benchmark every
    margin and forecast is measured against.
    """
    rows = load_station_rows()
    keep = [r for r in rows
            if (r["date"], r["time"], r["station_id"]) != (day, time, sid)]
    if len(keep) == len(rows):
        print(f"no observation for {label} at {day} {time}", file=sys.stderr)
        return 2

    gone = len(rows) - len(keep)
    write_station_rows(keep)
    print(f"removed {gone} observation(s) at {label} ({sid}) for {day} {time}")
    print(f"  {observed_days(keep, sid)} day(s) left for this station")
    return 0


def resolve_stamp(args) -> tuple[str, str] | None:
    """(date, time) from --date/--time/--ago, or None with a message printed.

    --ago is the field entry path: you notice the price, you type it in three
    stations later. Subtracting from the clock rather than asking for a time
    means it stays one number, and it rolls the date back with it when the
    subtraction crosses midnight — logging a 23:50 price at 00:10 is exactly
    when you would reach for this.
    """
    now = dt.datetime.now()
    if args.ago is not None:
        if args.ago < 0:
            print("--ago is minutes in the past, not the future", file=sys.stderr)
            return None
        if args.date or args.time:
            print("--ago sets the date and time; don't pass them too",
                  file=sys.stderr)
            return None
        seen = now - dt.timedelta(minutes=args.ago)
        return seen.date().isoformat(), seen.strftime("%H:%M")

    day = args.date or now.date().isoformat()
    try:
        when = dt.date.fromisoformat(day)
    except ValueError:
        print(f"{day!r} is not a date (want YYYY-MM-DD)", file=sys.stderr)
        return None
    if when > now.date():
        # A future-dated observation is either a typo or a guess. Either way it
        # would sit at the newest end of the calibration window and anchor
        # today's level to a price nobody has seen.
        print(f"{day} is in the future — a price you haven't seen yet "
              "would anchor the model", file=sys.stderr)
        return None

    if args.time is None:
        # Today with no time given is a price you are logging as you see it, so
        # the clock is the honest answer. A backdated row is not: nobody
        # remembers the minute three days later, and stamping it with the
        # current clock would invent a time. That reads as DEFAULT_OBS_TIME,
        # which is precisely what "unknown" is spelled as everywhere else.
        return day, (now.strftime("%H:%M") if when == now.date()
                     else DEFAULT_OBS_TIME)
    try:
        time = dt.time.fromisoformat(args.time).strftime("%H:%M")
    except ValueError:
        print(f"{args.time!r} is not a time (want HH:MM)", file=sys.stderr)
        return None
    return day, time


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("price", nargs="?", type=float)
    ap.add_argument("-s", "--station", help="station id or unique substring")
    ap.add_argument("-d", "--date", default=None)
    ap.add_argument("-t", "--time", help="HH:MM the price was seen (station only)")
    ap.add_argument("--ago", type=int, metavar="MINUTES",
                    help="saw it this many minutes ago; sets --date and --time")
    ap.add_argument("--remove", action="store_true",
                    help="delete the observation at --station/--date/--time")
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

    stamp = resolve_stamp(args)
    if stamp is None:
        return 2
    day, time = stamp

    if args.remove:
        if not args.station:
            # Without a station this would mean "remove a regional benchmark
            # row", which remove_station_price deliberately cannot do.
            print("--remove needs --station", file=sys.stderr)
            return 2
        sid = resolve(args.station, known)
        if sid is None:
            return 2
        return remove_station_price(day, time, sid, known[sid].label)

    if args.price is None:
        ap.print_help()
        return 2
    if not 0.5 <= args.price <= 3.5:
        print(f"{args.price} $/L doesn't look like a pump price — typo?", file=sys.stderr)
        return 2

    if args.station:
        sid = resolve(args.station, known)
        if sid is None:
            return 2
        log_station(args.price, day, sid, known[sid].label, args.source, time)
    else:
        log_regional(args.price, day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
