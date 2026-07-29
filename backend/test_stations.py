"""Fixtures for backend/stations.py — the offset model, and log_price's writes.

    python3 backend/test_stations.py

These write real CSVs into a temp directory and point the module paths at them,
because the behaviour worth testing is exactly what happens at the file: whether
a row with no time still reads, whether logging twice in a day appends or
overwrites, whether removing one row takes its neighbour with it. A test that
called compute_offsets on a hand-built list would pass while the CSV round trip
lost the column.

The case that earns its keep is `test_a_days_burst_counts_once`. Counting rows
instead of days is the natural mistake once observations carry a time, and it
would light up `confident` on the device — which reads this same n out of
data.json — after a single afternoon.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import add_station  # noqa: E402
import log_price  # noqa: E402
import schema  # noqa: E402
import stations as stationlib  # noqa: E402

FAILURES: list[str] = []

STATIONS_CSV = """\
# a comment line, as the real registry has
id,brand,address,city,role,label
a-station,Esso,1 Test Rd,Testville,home,A STATION
b-station,Shell,2 Test Rd,Testville,tracked,B STATION
"""


def check(name: str, got, want, tol: float | None = None) -> None:
    ok = (abs(got - want) <= tol) if tol is not None else (got == want)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}"
          + ("" if ok else f"  got {got!r}, want {want!r}"))
    if not ok:
        FAILURES.append(name)


@contextlib.contextmanager
def sandbox(prices_csv: str, stations_csv: str = STATIONS_CSV):
    """Point stations.py, log_price.py and add_station.py at throwaway CSVs.

    All three bind their paths at import time (`from paths import ...`), so the
    patch has to hit every name that was bound, not just paths.py.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        (d / "stations.csv").write_text(stations_csv)
        (d / "station_prices.csv").write_text(prices_csv)
        saved = (stationlib.STATIONS, stationlib.PRICES, log_price.PRICES,
                 add_station.STATIONS)
        stationlib.STATIONS = add_station.STATIONS = d / "stations.csv"
        stationlib.PRICES = log_price.PRICES = d / "station_prices.csv"
        try:
            yield d / "station_prices.csv"
        finally:
            (stationlib.STATIONS, stationlib.PRICES, log_price.PRICES,
             add_station.STATIONS) = saved


def offsets(bench: dict, today: str):
    sts = stationlib.load_stations()
    stationlib.compute_offsets(sts, bench, today)
    return sts


# --- reading ---------------------------------------------------------------

def test_missing_time_reads_as_noon():
    """Rows written before the column existed are prices, not garbage."""
    with sandbox("date,station_id,price,source\n"
                 "2026-01-01,a-station,1.70,seed\n"):
        rows = stationlib.load_prices()
        check("row survives without a time", len(rows), 1)
        check("time defaults to noon", rows[0][1], schema.DEFAULT_OBS_TIME)


def test_blank_time_reads_as_noon():
    with sandbox("date,time,station_id,price,source\n"
                 "2026-01-01,,a-station,1.70,seed\n"):
        check("empty time cell defaults", stationlib.load_prices()[0][1],
              schema.DEFAULT_OBS_TIME)


# --- offsets ---------------------------------------------------------------

def test_one_row_per_day_is_unchanged():
    """The no-op proof: with one price per day, collapsing changes nothing.

    Everything in the live registry looks like this today, so this is what
    guarantees the time column did not silently move a single offset.
    """
    bench = {"2026-01-01": 1.80, "2026-01-02": 1.75, "2026-01-03": 1.85}
    with sandbox("date,time,station_id,price,source\n"
                 "2026-01-01,12:00,a-station,1.70,seed\n"     # -0.10
                 "2026-01-02,12:00,a-station,1.66,seed\n"     # -0.09
                 "2026-01-03,12:00,a-station,1.74,seed\n"):   # -0.11
        st = offsets(bench, "2026-01-03")["a-station"]
        check("offset is the median delta", st.offset, -0.10, 1e-9)
        check("observations counts days", st.observations, 3)
        check("samples counts rows", st.samples, 3)
        check("three days is confident", st.confident, True)


def test_a_days_burst_counts_once():
    bench = {"2026-01-01": 1.80}
    with sandbox("date,time,station_id,price,source\n"
                 "2026-01-01,08:00,a-station,1.70,logged\n"
                 "2026-01-01,13:00,a-station,1.72,logged\n"
                 "2026-01-01,18:00,a-station,1.74,logged\n"):
        st = offsets(bench, "2026-01-01")["a-station"]
        check("one day, three prices -> one observation", st.observations, 1)
        check("samples still counts three", st.samples, 3)
        check("a single day is not confident", st.confident, False)
        check("offset is that day's median", st.offset, -0.08, 1e-9)


# --- writing ---------------------------------------------------------------

def test_two_prices_in_a_day_both_survive():
    """The whole point: the second price of the day must not replace the first."""
    with sandbox("date,time,station_id,price,source\n") as path:
        log_price.log_station(1.70, "2026-01-01", "a-station", "A", "logged", "08:00")
        log_price.log_station(1.74, "2026-01-01", "a-station", "A", "logged", "18:00")
        rows = stationlib.load_prices()
        check("two rows written", len(rows), 2)
        check("times preserved", [r[1] for r in rows], ["08:00", "18:00"])
        check("header carries the time column",
              path.read_text().splitlines()[0], ",".join(schema.STATION_PRICE_FIELDS))


def test_same_time_overwrites():
    """Same station, same minute is the same observation logged twice."""
    with sandbox("date,time,station_id,price,source\n"):
        log_price.log_station(1.70, "2026-01-01", "a-station", "A", "logged", "08:00")
        log_price.log_station(1.75, "2026-01-01", "a-station", "A", "logged", "08:00")
        rows = stationlib.load_prices()
        check("still one row", len(rows), 1)
        check("price replaced", rows[0][3], 1.75, 1e-9)


def test_remove_takes_exactly_one():
    with sandbox("date,time,station_id,price,source\n"
                 "2026-01-01,08:00,a-station,1.70,logged\n"
                 "2026-01-01,18:00,a-station,1.74,logged\n"
                 "2026-01-01,18:00,b-station,1.80,logged\n"):
        code = log_price.remove_station_price("2026-01-01", "18:00", "a-station", "A")
        rows = stationlib.load_prices()
        check("remove succeeded", code, 0)
        check("two rows left", len(rows), 2)
        check("the other station at the same minute is untouched",
              sorted(r[2] for r in rows), ["a-station", "b-station"])
        check("the surviving a-station row is the 08:00 one",
              [r[1] for r in rows if r[2] == "a-station"], ["08:00"])


def test_remove_of_a_missing_row_fails_loudly():
    """Exit 2, not a silent no-op: the caller is a UI showing you a delete."""
    with sandbox("date,time,station_id,price,source\n"
                 "2026-01-01,08:00,a-station,1.70,logged\n"):
        code = log_price.remove_station_price("2026-01-01", "09:00", "a-station", "A")
        check("missing row is an error", code, 2)
        check("nothing was deleted", len(stationlib.load_prices()), 1)


# --- add_station: one line in, six fields out ------------------------------

def test_parse_splits_brand_address_city():
    got = add_station.parse(
        "esso 10016 bayview ave & major mackenzie dr e richmond hill")
    check("brand off the front", got["brand"], "Esso")
    check("city off the back", got["city"], "Richmond Hill")
    check("address is what is left, title-cased", got["address"],
          "10016 Bayview Ave & Major Mackenzie Dr E")


def test_parse_leaves_out_what_it_cannot_recognise():
    """No brand and no known city means empty, not a guess.

    Inventing a city from a street name would file the station in the wrong
    place in the ladder with nothing on screen to say it had happened.
    """
    got = add_station.parse("500 Nowhere Blvd")
    check("no brand invented", got["brand"], "")
    check("no city invented", got["city"], "")
    check("address survives", got["address"], "500 Nowhere Blvd")


def test_parse_preserves_deliberate_case():
    check("a capital means you meant it",
          add_station.parse("shell 100 McCowan Rd")["address"], "100 McCowan Rd")


def test_highway_keeps_its_number():
    """`Hwy 7` is the street name; splitting it collapses every Highway 7 station."""
    check("hwy number stays with the street",
          add_station.suggest_id("Shell", "408 Hwy 7 E"), "shell-hwy7-408")
    check("and in the label",
          add_station.suggest_label("Petro-Canada", "4641 Hwy 7 E"),
          "PETROCAN HWY7 4641")


def test_intersection_keeps_both_corners():
    """Which corner it is on is what tells it from the other station on Bayview."""
    addr = "10016 Bayview Ave & Major Mackenzie Dr E"
    check("id names both streets", add_station.suggest_id("Esso", addr),
          "esso-bayview-major-mackenzie-10016")
    check("label abbreviates to fit", add_station.suggest_label("Esso", addr),
          "ESSO BAYVW MAJMAC")


def test_label_never_exceeds_the_header():
    """Every label must fit, and must never be cut mid-word."""
    for text in ("esso 10016 bayview ave & major mackenzie dr e richmond hill",
                 "canadian tire 500 yonge st aurora",
                 "petro-canada 6375 major mackenzie dr e markham",
                 "12345 some extremely long street name that goes on drive"):
        row = add_station.parse(text)
        label = add_station.suggest_label(row["brand"], row["address"])
        check(f"fits: {label!r}", len(label) <= add_station.LABEL_MAX, True)
        check(f"whole words: {label!r}", label.strip(), label)


def test_suggest_fills_every_field():
    """The point of the feature: nothing is left for you to type.

    Brand is the one exception, and deliberately so — it is not derivable from
    an address, and a placeholder would put a brand nobody verified into the
    registry, the ladder and the panel label. Blank says "not recorded"; the id
    and the label are still produced without it.
    """
    row = add_station.suggest("10016 bayview ave & major mackenzie dr e markham", {})
    for k in ("id", "address", "city", "role", "label"):
        check(f"{k} has a value", bool(row[k]), True)
    check("brand stays blank rather than invented", row["brand"], "")
    check("id survives without a brand", row["id"],
          "bayview-major-mackenzie-10016")
    check("role defaults to tracked", row["role"], "tracked")
    check("explicit city wins", row["city"], "Markham")


def test_overrides_beat_defaults():
    row = add_station.suggest("esso 9240 leslie st",
                              {"id": "my-id", "label": "MY LABEL", "role": "home"})
    check("id override", row["id"], "my-id")
    check("label override", row["label"], "MY LABEL")
    check("role override", row["role"], "home")


# --- coordinates -----------------------------------------------------------
#
# lat/lon are read by no part of the price model, so nothing else in this file
# would notice if they broke. What they *can* break is the registry itself:
# every caller goes through load_stations, so a column it cannot parse takes
# down build.py and the logging endpoint together.

LOCATED_CSV = """\
id,brand,address,city,role,label,lat,lon
a-station,Esso,1 Test Rd,Testville,home,A STATION,43.86839,-79.36225
b-station,Shell,2 Test Rd,Testville,tracked,B STATION,,
"""


def test_registry_without_coordinates_still_loads():
    """The 19 rows that predate the column must keep working, unchanged.

    STATIONS_CSV here has no lat/lon header at all — this is the actual
    on-disk shape of every registry written before this feature existed.
    """
    with sandbox(""):
        sts = stationlib.load_stations()
        check("no-coord registry loads", len(sts), 2)
        check("lat absent -> None", sts["a-station"].lat, None)
        check("lon absent -> None", sts["a-station"].lon, None)


def test_coordinates_round_trip():
    with sandbox("", LOCATED_CSV):
        sts = stationlib.load_stations()
        check("lat parsed", sts["a-station"].lat, 43.86839)
        check("lon parsed", sts["a-station"].lon, -79.36225)


def test_blank_coordinate_is_none():
    """Blank is a station without a coordinate, not a broken row — a registry
    where only some rows are located has to load."""
    with sandbox("", LOCATED_CSV):
        sts = stationlib.load_stations()
        check("blank lat -> None", sts["b-station"].lat, None)
        check("blank lon -> None", sts["b-station"].lon, None)


def test_unparseable_coordinate_is_loud():
    """Silently reading a typo as None would drop the station out of every
    proximity sort with nothing anywhere to notice."""
    bad = LOCATED_CSV.replace("43.86839", "43.86.839")
    with sandbox("", bad):
        try:
            stationlib.load_stations()
            check("typo raises", "loaded", "ValueError")
        except ValueError as e:
            check("typo names the station", "a-station" in str(e), True)


def test_out_of_range_coordinate_is_loud():
    bad = LOCATED_CSV.replace("-79.36225", "-279.36225")
    with sandbox("", bad):
        try:
            stationlib.load_stations()
            check("out-of-range raises", "loaded", "ValueError")
        except ValueError as e:
            check("range error names the column", "lon" in str(e), True)


def test_swapped_coordinates_are_not_caught_by_range():
    """Records a real limit rather than a wish.

    At this latitude a swapped pair (lat=-79.4, lon=43.9) is inside both valid
    ranges, so no range check can reject it — it is a legitimate point in the
    Southern Ocean. The guard that actually catches this is the bounding box in
    geocode_stations.py, which is upstream of the file. A hand-edited swap is
    therefore NOT detected here, and this test exists so that stays a known
    property instead of a surprise.
    """
    swapped = LOCATED_CSV.replace("43.86839,-79.36225", "-79.36225,43.86839")
    with sandbox("", swapped):
        sts = stationlib.load_stations()
        check("swap loads, undetected", sts["a-station"].lat, -79.36225)
        import geocode_stations  # noqa: PLC0415 — only this test needs it
        check("but the bbox would refuse it",
              geocode_stations.in_box(-79.36225, 43.86839), False)


def test_add_station_carries_coordinates():
    with sandbox("", LOCATED_CSV):
        row = add_station.suggest("esso 9240 leslie st",
                                  {"lat": "43.86215", "lon": "-79.38709"})
        check("lat kept", row["lat"], "43.86215")
        check("lon kept", row["lon"], "-79.38709")
        check("written", add_station.add(row), 0)
        check("reloads", stationlib.load_stations()["esso-leslie-9240"].lat,
              43.86215)


def test_add_station_refuses_half_a_location():
    """One coordinate is not half a location; it is a row that looks located
    and sorts nowhere."""
    with sandbox("", LOCATED_CSV):
        row = add_station.suggest("esso 9240 leslie st", {"lat": "43.86215"})
        check("lat alone rejected", add_station.add(row), 2)


def test_add_station_refuses_a_bad_coordinate():
    with sandbox("", LOCATED_CSV):
        row = add_station.suggest("esso 9240 leslie st",
                                  {"lat": "not-a-number", "lon": "-79.38709"})
        check("garbage rejected", add_station.add(row), 2)


# --- finding the station you are standing at -------------------------------

NEAR_CSV = """\
id,brand,address,city,role,label,lat,lon
close,Esso,1 Test Rd,Testville,home,CLOSE,43.87581,-79.41512
also-close,Petro-Canada,2 Test Rd,Testville,tracked,ALSO CLOSE,43.87574,-79.41629
far,Shell,3 Test Rd,Testville,tracked,FAR,43.81450,-79.34827
nowhere,Pioneer,4 Test Rd,Testville,tracked,NOWHERE,,
"""

# The real pair, on opposite corners of Bayview & Major Mackenzie.
AT_THE_INTERSECTION = (43.87578, -79.41570)


def test_distance_matches_the_real_pair():
    """94 m is measured off the geocoded registry, not chosen."""
    d = stationlib.meters_between((43.87581, -79.41512), (43.87574, -79.41629))
    check("bayview/majmac spacing", round(d), 94)


def test_nearest_offers_both_sides_of_one_intersection():
    """The case that rules out auto-picking: two real stations inside GPS error."""
    with sandbox("", NEAR_CSV):
        got = stationlib.nearest(*AT_THE_INTERSECTION, stationlib.load_stations())
        check("both corners offered", [s.id for s, _ in got],
              ["close", "also-close"])
        check("nearest really is nearest", got[0][1] < got[1][1], True)


def test_nearest_skips_stations_with_no_coordinates():
    """Unknown distance is not far away — an unlocated station must not appear
    in a list sorted by a number it does not have."""
    with sandbox("", NEAR_CSV):
        got = stationlib.nearest(*AT_THE_INTERSECTION, stationlib.load_stations())
        check("unlocated absent", any(s.id == "nowhere" for s, _ in got), False)


def test_nearest_excludes_what_is_out_of_range():
    with sandbox("", NEAR_CSV):
        got = stationlib.nearest(*AT_THE_INTERSECTION, stationlib.load_stations())
        check("7 km away excluded", any(s.id == "far" for s, _ in got), False)


def test_nearest_returns_nothing_when_you_are_nowhere_near():
    """Standing in Ottawa must produce an empty list, not the least-wrong guess
    — an empty answer is a prompt to pick manually; a wrong one is a bad row."""
    with sandbox("", NEAR_CSV):
        check("no candidates far away",
              stationlib.nearest(45.4215, -75.6972, stationlib.load_stations()), [])


def test_resolve_accepts_a_label():
    """What a phone picks off a list is a label, not an id."""
    with sandbox("", NEAR_CSV):
        known = stationlib.load_stations()
        check("exact label", log_price.resolve("ALSO CLOSE", known), "also-close")
        check("case-insensitive", log_price.resolve("also close", known),
              "also-close")
        check("id still wins", log_price.resolve("close", known), "close")
        check("unknown still fails", log_price.resolve("NOPE", known), None)


def main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"{name}:")
            fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
        return 1
    print("all station checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
