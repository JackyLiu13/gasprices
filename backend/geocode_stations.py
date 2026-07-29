"""Fill in the lat/lon columns of backend/stations.csv, by looking them up.

    python3 backend/geocode_stations.py --dry-run   # show what it would write
    python3 backend/geocode_stations.py             # fill the blanks
    python3 backend/geocode_stations.py --only esso-beaver-creek
    python3 backend/geocode_stations.py --refresh   # redo rows that already have one

A one-off, in the same sense as backfill.py: you run it when you add stations,
not on a schedule, and never in CI. Coordinates are needed by exactly one
caller — a phone sorting the registry by distance — and they do not change.

Uses OpenStreetMap's Nominatim: no key, no account, stdlib `urllib`, so the
zero-dependency rule survives. Their usage policy asks for at most one request
a second and a User-Agent that identifies the caller; both are honoured below.
Do not move this into build.py or the Lambda — that would put a courtesy-limited
public service on the critical path of publishing a price, for data that was
already correct yesterday.

**A blank stays blank unless the lookup is unambiguous.** An intersection like
"10016 Bayview Ave & Major Mackenzie Dr E" is exactly the input a geocoder is
worst at, and a coordinate that is confidently 400 m wrong is worse here than no
coordinate at all: it does not fail, it just quietly offers you the wrong
station at the pump, and you find out when the offset model does. So a result
outside the bounding box below is refused and reported, and you fill that row in
by hand from a map.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import schema  # noqa: E402
import stations as stationlib  # noqa: E402

from paths import STATIONS  # noqa: E402

ENDPOINT = "https://nominatim.openstreetmap.org/search"

# Nominatim's policy: identify yourself, and no more than 1 request/second.
USER_AGENT = "gasprices/1 (+https://github.com/jackyliu13/gasprices)"
MIN_INTERVAL_S = 1.1

# Everything this project tracks is in Richmond Hill or Markham. Anything the
# geocoder returns outside this box is not a near miss, it is a different place
# with a similar street name — "Yonge St" exists for 50 km, and "Main St" is in
# every town in Ontario. Generous enough to cover Vaughan and north Toronto so a
# station added slightly out of area still resolves.
BBOX = {"lat_lo": 43.70, "lat_hi": 44.15, "lon_lo": -79.70, "lon_hi": -79.15}

# ~1.1 m. Further precision would be implying the geocoder knows which pump.
PRECISION = 5


def query(address: str, city: str) -> str:
    """The search string: the address, and nothing else.

    Prepending the brand is the obvious idea and it is wrong, in a way worth
    recording because it fails silently. "Esso, 10579 Yonge St" and "Esso, 12891
    Yonge St" both resolve to *the same point* — Nominatim matches the brand as
    a POI name on Yonge St and stops caring about the house number. The two
    addresses are 2.5 km apart. So do the Shell pair at 8656 and 11151 Yonge:
    5.6 km apart, returned 5 m apart.

    Nothing about those results looks wrong. They are in the right city, on the
    right street, inside the bounding box — they are just a different station,
    which at the pump means logging a price against the wrong one. The plain
    address resolves all four correctly and distinctly.
    """
    return ", ".join([p for p in (address, city, "Ontario, Canada") if p])


# Two gas stations are never this close. A result landing on top of one already
# in the file means the geocoder matched something other than the street number
# — the failure described in query() — so refuse it rather than write it.
COLLISION_M = 60.0


_last_request = 0.0


def lookup(query: str, timeout: float = 20.0) -> tuple[float, float] | None:
    """One Nominatim search. None when there is no usable answer.

    `bounded=1` with a viewbox asks the service to restrict the search rather
    than merely prefer the area; the box is re-checked on the result anyway,
    because that parameter has been advisory in some versions.

    The rate limit is enforced here rather than by the caller's loop, so that
    adding a fallback query cannot quietly double the request rate against a
    free service.
    """
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()

    params = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "countrycodes": "ca",
        "bounded": "1",
        "viewbox": f"{BBOX['lon_lo']},{BBOX['lat_hi']},"
                   f"{BBOX['lon_hi']},{BBOX['lat_lo']}",
    }
    url = f"{ENDPOINT}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hits = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"    lookup failed: {e}", file=sys.stderr)
        return None
    if not hits:
        return None
    try:
        lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return lat, lon


def in_box(lat: float, lon: float) -> bool:
    return (BBOX["lat_lo"] <= lat <= BBOX["lat_hi"]
            and BBOX["lon_lo"] <= lon <= BBOX["lon_hi"])


def read_rows() -> tuple[list[str], list[str], list[dict]]:
    """(leading comment lines, header fields, data rows).

    Split rather than round-tripped through DictReader/DictWriter because the
    comment block at the top of stations.csv is documentation people read, and
    a rewrite that dropped it would be a silent loss every time this runs.
    """
    lines = STATIONS.read_text().splitlines()
    comments = [ln for ln in lines if ln.lstrip().startswith("#")]
    body = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(body)
    return comments, list(reader.fieldnames or schema.STATION_FIELDS), list(reader)


def write_rows(comments: list[str], fields: list[str], rows: list[dict]) -> None:
    out = list(comments) + [",".join(fields)]
    out += [",".join((r.get(k) or "") for k in fields) for r in rows]
    STATIONS.write_text("\n".join(out) + schema.CSV_LINETERMINATOR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written, touch nothing")
    ap.add_argument("--refresh", action="store_true",
                    help="also redo rows that already have coordinates")
    ap.add_argument("--only", metavar="ID", help="one station id")
    args = ap.parse_args()

    comments, fields, rows = read_rows()
    for col in ("lat", "lon"):
        if col not in fields:
            print(f"stations.csv has no {col} column — add it to the header first",
                  file=sys.stderr)
            return 2

    todo = [r for r in rows
            if (args.refresh or not (r.get("lat") or "").strip())
            and (not args.only or r["id"] == args.only)]
    if args.only and not todo:
        print(f"no station {args.only!r} needing coordinates", file=sys.stderr)
        return 2
    if not todo:
        print("every station already has coordinates — nothing to do")
        return 0

    print(f"{len(todo)} station(s) to locate, ~{MIN_INTERVAL_S:.0f}s apart\n")
    # Every coordinate already in the file, so a new one can be checked against
    # rows this run is not touching as well as against its own results.
    taken: dict[str, tuple[float, float]] = {
        r["id"]: (float(r["lat"]), float(r["lon"])) for r in rows
        if (r.get("lat") or "").strip() and (r.get("lon") or "").strip()
        and r not in todo
    }

    found = refused = missed = 0
    for r in todo:
        q = query(r.get("address", ""), r.get("city", ""))
        print(f"  {r['id']}\n    {q}")

        hit = lookup(q)
        if hit is None:
            print("    no result — fill this row in by hand")
            missed += 1
            continue
        lat, lon = hit
        if not in_box(lat, lon):
            # Not a near miss. See BBOX.
            print(f"    refused {lat:.5f},{lon:.5f} — outside Richmond Hill/Markham")
            refused += 1
            continue
        clash = next((sid for sid, pt in taken.items()
                      if stationlib.meters_between(pt, (lat, lon)) < COLLISION_M), None)
        if clash:
            print(f"    refused {lat:.5f},{lon:.5f} — same spot as {clash}")
            refused += 1
            continue

        taken[r["id"]] = (lat, lon)
        r["lat"], r["lon"] = f"{lat:.{PRECISION}f}", f"{lon:.{PRECISION}f}"
        print(f"    {r['lat']},{r['lon']}")
        found += 1

    print(f"\n{found} located, {refused} refused, {missed} not found")
    if args.dry_run:
        print("[dry-run] stations.csv untouched")
        return 0
    if not found:
        return 1

    write_rows(comments, fields, rows)
    # Reload through the real parser: this script writes the file that
    # everything else reads, so a row it just produced had better load.
    stationlib.load_stations()
    print(f"wrote {STATIONS}")
    if refused or missed:
        print(f"{refused + missed} row(s) still blank — they log fine, they "
              f"just won't sort by distance until you add them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
