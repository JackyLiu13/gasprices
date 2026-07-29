"""Append one station to backend/stations.csv.

    python3 backend/add_station.py "Esso 10016 Bayview Ave & Major Mackenzie Dr E Richmond Hill"
    python3 backend/add_station.py "9240 Leslie St" --brand Shell
    python3 backend/add_station.py "..." --id shell-leslie-9240 --label "SHELL LESLIE"
    python3 backend/add_station.py "..." --suggest      # print the row, write nothing

One line in, six fields out. Everything has a default derived from what you
typed, because this is used standing at a pump: the brand comes off the front if
it is there, the city off the back if it names one you already track, the id and
label from the street, and the role is `tracked`. Anything you do pass wins.

Exists because the dashboard's express input needs a way to add the station you
are standing at without you editing a CSV at the pump — and because doing that
silently would be worse than not doing it at all. An id is permanent the moment
station_prices.csv references one, so this prints the id it will use and refuses
to reuse an existing one; the caller shows it to you before you commit.

Adding a station here does not log a price. That stays log_price.py's job, so
validation lives in one place.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import schema  # noqa: E402
import stations as stationlib  # noqa: E402

from paths import STATIONS  # noqa: E402

FIELDS = schema.STATION_FIELDS
ROLES = ("home", "regular", "favourite", "tracked")

# ui.h's header line. stations.csv says the same thing in its own comment; this
# is the check that enforces it, because a long label silently clips on the
# panel and you would only find out by looking at the device.
LABEL_MAX = 18

# Street-type words and directions. Dropped from ids and labels because they are
# the least distinguishing part of an address: nothing here is told apart by
# whether it is on a Rd or an Ave.
NOISE = {"st", "rd", "ave", "dr", "blvd", "cres", "way", "pkwy", "hwy", "highway",
         "street", "road", "avenue", "drive", "boulevard", "parkway",
         "e", "w", "n", "s", "east", "west", "north", "south"}

# Brands that are two words, or that no station in the registry uses yet. The
# registry itself is the primary source — this is what lets the first Costco be
# recognised before there is a Costco row to learn from.
KNOWN_BRANDS = ("Petro-Canada", "Canadian Tire", "Fas Gas", "Pioneer", "Esso",
                "Shell", "Costco", "Ultramar", "Husky", "Mobil", "Sunoco",
                "Chevron", "7-Eleven", "Circle K", "Race Trac", "Gale's")

# Cities near enough to plausibly appear. Same idea: the registry is checked
# first, this catches the ones you have not logged in yet.
KNOWN_CITIES = ("Richmond Hill", "Markham", "Vaughan", "Thornhill", "Aurora",
                "Newmarket", "Stouffville", "Unionville", "King City", "Maple",
                "Concord", "Woodbridge", "Toronto", "North York", "Scarborough")

# Local squashes, applied only when the full words do not fit in LABEL_MAX. The
# spellings are taken from labels already in stations.csv so a new station reads
# like its neighbours rather than inventing a second abbreviation for one street.
ABBREV = {"MAJOR MACKENZIE": "MAJMAC", "ELGIN MILLS": "ELGINMILLS",
          "MACKENZIE": "MAC", "BAYVIEW": "BAYVW", "WOODBINE": "WOODBN",
          "STOUFFVILLE": "STFVL"}

# What a brand is called on the panel, where it differs from the brand itself.
# `PETROCAN` is what the six existing Petro-Canada labels already say.
BRAND_LABEL = {"PETRO-CANADA": "PETROCAN", "CANADIAN TIRE": "CDNTIRE"}


def slug(text: str) -> str:
    """Lowercase, hyphens, no runs — the shape every existing id already has."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def titlecase(text: str) -> str:
    """`bayview ave e` -> `Bayview Ave E`, leaving deliberate case alone.

    A word that already contains a capital was typed that way on purpose
    (`McCowan`, `HWY`), so it is passed through untouched.
    """
    return " ".join(w if any(c.isupper() for c in w) else w[:1].upper() + w[1:]
                    for w in text.split())


def known(kind: str) -> tuple[str, ...]:
    """Brands or cities: what the registry already uses, then the fallbacks.

    Longest first, so `Petro-Canada` is matched before `Petro` would be and
    `Richmond Hill` before a bare `Richmond`.
    """
    seen = {getattr(s, kind) for s in stationlib.load_stations().values()}
    both = {v for v in seen if v} | set(KNOWN_BRANDS if kind == "brand"
                                        else KNOWN_CITIES)
    return tuple(sorted(both, key=len, reverse=True))


def parse(text: str) -> dict:
    """One free-text line -> {brand, address, city}.

    Deliberately only strips things it can *recognise*: a brand off the front, a
    known city off the back. What is left is the address. Guessing a city from a
    street name would put a station in the wrong place in the ladder and there
    would be nothing on screen to show it had happened.
    """
    rest = " ".join(text.replace(",", " ").split())
    brand = city = ""

    for b in known("brand"):
        if rest.lower().startswith(b.lower() + " "):
            brand, rest = b, rest[len(b):].strip()
            break
    for c in known("city"):
        if rest.lower().endswith(" " + c.lower()):
            city, rest = c, rest[: -len(c)].strip()
            break

    return {"brand": brand, "address": titlecase(rest), "city": city}


def street_words(address: str) -> list[str]:
    """The distinguishing words of an address, in order, number last.

    An intersection keeps both sides — `Bayview Ave & Major Mackenzie Dr E`
    becomes `bayview major mackenzie`, because which corner it is on is exactly
    what tells it apart from the other station on Bayview.

    `Hwy 7` is the one case where a street-type word is the street name: the
    number after it belongs to it, not to the building. Splitting them gives
    `4641` and nothing else, and every Highway 7 station collapses to the same
    label. The registry already writes these `HWY7` / `hwy7-4641`.
    """
    words, number = [], ""
    for part in address.split("&"):
        toks = part.split()
        i = 0
        while i < len(toks):
            w, low = toks[i], toks[i].lower().strip(".")
            nxt = toks[i + 1].rstrip(".") if i + 1 < len(toks) else ""
            if low in ("hwy", "highway") and nxt.isdigit():
                words.append(f"hwy{nxt}")
                i += 2
                continue
            if w.rstrip(".").isdigit():
                number = number or w
            elif low not in NOISE and re.search(r"[a-z]", w, re.I):
                words.append(low)
            i += 1
    return words + ([number] if number else [])


def suggest_id(brand: str, address: str) -> str:
    """brand + the distinctive part of the address, e.g. shell-leslie-9240.

    Street number last, matching the existing registry: `9240 Leslie St` becomes
    `leslie-9240`, so ids sort by street and stay readable in a diff.
    """
    return "-".join(p for p in [slug(brand), *map(slug, street_words(address))] if p)


def suggest_label(brand: str, address: str) -> str:
    """A header label that fits LABEL_MAX, e.g. SHELL LESLIE.

    Three steps, in this order: full words, then the local abbreviations, then
    drop whole words from the right. Never a mid-word truncation — a label cut
    inside a street number reads as a different street number on the panel,
    which is worse than not showing the number at all.
    """
    head = BRAND_LABEL.get(brand.upper(), brand.upper().split("-")[0])
    words = [w.upper() for w in [*head.split(), *street_words(address)] if w]

    def fits(ws):
        return len(" ".join(ws)) <= LABEL_MAX

    if not fits(words):
        joined = " ".join(words)
        for long, short in sorted(ABBREV.items(), key=lambda kv: -len(kv[0])):
            if fits(joined.split()):
                break
            joined = joined.replace(long, short)
        words = joined.split()

    while len(words) > 1 and not fits(words):
        words.pop()
    return " ".join(words)[:LABEL_MAX]


def suggest(text: str, overrides: dict) -> dict:
    """The full row this input would produce. Overrides win over every default."""
    row = parse(text) if text else {"brand": "", "address": "", "city": ""}
    row.update({k: v for k, v in overrides.items() if v})
    # Falls back to wherever most of the registry already is, rather than a
    # constant: the default should follow the data, not a line written once.
    if not row["city"]:
        cities = [s.city for s in stationlib.load_stations().values() if s.city]
        row["city"] = max(set(cities), key=cities.count) if cities else ""
    row.setdefault("role", "tracked")
    row["id"] = overrides.get("id") or suggest_id(row["brand"], row["address"])
    row["label"] = overrides.get("label") or suggest_label(row["brand"], row["address"])
    return {k: row.get(k, "") for k in FIELDS}


def add(row: dict) -> int:
    registry = stationlib.load_stations()
    if not row["address"]:
        print("an address, at least — everything else has a default",
              file=sys.stderr)
        return 2
    if row["id"] in registry:
        print(f"{row['id']} already exists — ids are permanent, pick another",
              file=sys.stderr)
        return 2
    if row["role"] not in ROLES:
        print(f"role must be one of {', '.join(ROLES)}", file=sys.stderr)
        return 2
    if len(row["label"]) > LABEL_MAX:
        print(f"label {row['label']!r} is {len(row['label'])} chars — the panel "
              f"header fits {LABEL_MAX}", file=sys.stderr)
        return 2
    # Both or neither. One alone is not half a location, it is a row that looks
    # located and sorts nowhere — the failure parse_coord exists to make loud.
    if bool(row["lat"]) != bool(row["lon"]):
        print("give --lat and --lon together, or neither", file=sys.stderr)
        return 2
    for what, lo, hi in (("lat", -90.0, 90.0), ("lon", -180.0, 180.0)):
        try:
            stationlib.parse_coord(row[what], lo, hi, what, row["id"])
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
    if row["role"] == "home" and any(s.role == "home" for s in registry.values()):
        # baseline() takes the cheapest `home`, so a second one does not break
        # anything — it just quietly changes what "savings" is measured against.
        print("a home station is already set; savings are measured against it",
              file=sys.stderr)
        return 2

    # Appended raw rather than rewritten through DictWriter: stations.csv opens
    # with a comment block explaining the roles, and a rewrite would drop it.
    line = ",".join(row[k] for k in FIELDS)
    text = STATIONS.read_text()
    STATIONS.write_text(text + ("" if text.endswith("\n") else "\n")
                        + line + schema.CSV_LINETERMINATOR)
    print(f"added {row['id']}  {row['label']}  {row['address']}, {row['city']}")
    print("  log a price against it to give it an offset — until then it has none")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="",
                    help="the whole thing: [brand] address [city]")
    ap.add_argument("--address", help="override the address parsed from text")
    ap.add_argument("--brand", help="override the brand parsed from text")
    ap.add_argument("--city", help="override; defaults to the registry's commonest")
    ap.add_argument("--role", choices=ROLES, help="default: tracked")
    ap.add_argument("--id", help="default: derived from brand + address")
    ap.add_argument("--label", help=f"panel header, <= {LABEL_MAX} chars")
    # Optional, and left blank when not given rather than guessed: this script
    # parses an address, it does not know where that address is. geocode_stations.py
    # fills the blanks in afterwards, for one row or for all of them.
    ap.add_argument("--lat", help="WGS84 decimal degrees, optional")
    ap.add_argument("--lon", help="WGS84 decimal degrees, optional")
    ap.add_argument("--suggest", action="store_true",
                    help="print the row as JSON, write nothing")
    args = ap.parse_args()

    row = suggest(args.text, {k: (v or "").strip() for k, v in
                              (("brand", args.brand), ("address", args.address),
                               ("city", args.city), ("role", args.role),
                               ("id", args.id), ("label", args.label),
                               ("lat", args.lat), ("lon", args.lon))})
    if not args.text and not args.address:
        ap.print_help()
        return 2
    if any("," in v for v in row.values()):
        print("no commas — this is a CSV with no quoting anywhere in it",
              file=sys.stderr)
        return 2
    if args.suggest:
        print(json.dumps(row))
        return 0
    return add(row)


if __name__ == "__main__":
    raise SystemExit(main())
