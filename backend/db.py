"""Build backend/analytics.db — a derived read model over the CSVs.

    python3 backend/db.py            # rebuild
    python3 backend/db.py --check    # exit 1 if the CSVs are newer than the db

The CSVs remain the source of truth. They are what git diffs, what the twice-
daily Action commits, and what merges without a fight; a binary blob would give
up all three. This file is a cache: delete it any time, rebuild it in a
millisecond, and lose nothing.

It exists because the interesting questions are joins. "Show me each station's
observations against the benchmark on the same day, next to the forecast error
for that day" is one query here and three parallel passes over three CSVs in
JavaScript. Nothing writes to it — if a query needs data the CSVs lack, the fix
is a CSV column, not a table.

sqlite3 is standard library, so this keeps the project's no-dependency rule.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import model  # noqa: E402
from schema import (FORECAST_FIELDS, HISTORY_FIELDS,  # noqa: E402
                    STATION_PRICE_FIELDS)

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
DB = BACKEND / "analytics.db"

SOURCES = {
    "history": (BACKEND / "history.csv", HISTORY_FIELDS),
    "station_prices": (BACKEND / "station_prices.csv", STATION_PRICE_FIELDS),
    "forecasts": (BACKEND / "forecasts.csv", FORECAST_FIELDS),
    "stations": (BACKEND / "stations.csv",
                 ["id", "brand", "address", "city", "role", "label"]),
}

# Numeric columns. Stored as REAL so SQL can do arithmetic; the CSVs keep them
# as formatted strings because that is what a human reads in a diff.
NUMERIC = {
    "history": ["rbob_usd_gal", "usd_cad", "wholesale_cad_l",
                "retail_model", "retail_survey", "retail_actual", "margin"],
    "station_prices": ["price"],
    "forecasts": ["horizon", "predicted"],
    "stations": [],
}

# The benchmark coalesce, defined once. build.best_retail is the Python copy of
# this rule; keeping the SQL beside it in one view stops a third variant from
# appearing inside a chart.
VIEWS = """
CREATE VIEW v_benchmark AS
  SELECT date,
         COALESCE(retail_actual, retail_survey, retail_model) AS price,
         CASE WHEN retail_actual IS NOT NULL THEN 'logged'
              WHEN retail_survey IS NOT NULL THEN 'survey'
              WHEN retail_model  IS NOT NULL THEN 'model'
              ELSE 'none' END AS source
  FROM history
  WHERE COALESCE(retail_actual, retail_survey, retail_model) IS NOT NULL;

-- Measurements only — never retail_model. Feeding the model's own output back
-- into a margin would fit the margin to a price the margin produced.
CREATE VIEW v_observed AS
  SELECT date, COALESCE(retail_actual, retail_survey) AS price
  FROM history
  WHERE COALESCE(retail_actual, retail_survey) IS NOT NULL;

-- model.implied_margin, in SQL. The rates are interpolated from model.py at
-- build time rather than typed in: Ontario's fuel tax moves, and a stale
-- literal here would quietly shift every margin on the dashboard while the
-- Python engine stayed right.
CREATE VIEW v_margin AS
  SELECT o.date,
         o.price / {hst} - {taxes} - h.wholesale_cad_l AS implied_margin,
         h.wholesale_cad_l
  FROM v_observed o JOIN history h USING (date)
  WHERE h.wholesale_cad_l IS NOT NULL;

CREATE VIEW v_station_priced AS
  SELECT p.date, p.station_id, s.label, s.city, s.role, p.price, p.source,
         b.price AS benchmark, p.price - b.price AS delta
  FROM station_prices p
  JOIN stations s ON s.id = p.station_id
  LEFT JOIN v_benchmark b ON b.date = p.date;

CREATE VIEW v_forecast_scored AS
  SELECT f.made_on, f.target_date, f.horizon, f.variant, f.basis,
         f.predicted, t.price AS actual, o.price AS origin,
         f.predicted - t.price AS error,
         o.price - t.price AS baseline_error
  FROM forecasts f
  JOIN v_benchmark t ON t.date = f.target_date
  JOIN v_benchmark o ON o.date = f.made_on;
"""


def _rows(path: pathlib.Path, fields: list[str]) -> list[tuple]:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        # stations.csv carries '#' comment lines, same as stations.load_stations.
        lines = (ln for ln in f if not ln.startswith("#") and ln.strip())
        for r in csv.DictReader(lines):
            if not any((r.get(k) or "").strip() for k in fields):
                continue
            out.append(tuple((r.get(k) or "").strip() or None for k in fields))
    return out


def build(db_path: pathlib.Path = DB) -> dict[str, int]:
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    counts = {}

    for table, (path, fields) in SOURCES.items():
        cols = ", ".join(
            f'"{f}" {"REAL" if f in NUMERIC[table] else "TEXT"}' for f in fields)
        con.execute(f"CREATE TABLE {table} ({cols})")
        rows = _rows(path, fields)
        if rows:
            marks = ", ".join("?" * len(fields))
            con.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        counts[table] = len(rows)

    con.execute("CREATE INDEX ix_history_date ON history(date)")
    con.execute("CREATE INDEX ix_prices ON station_prices(station_id, date)")
    con.execute("CREATE INDEX ix_forecast ON forecasts(made_on, target_date)")
    con.executescript(VIEWS.format(hst=1.0 + model.HST,
                                   taxes=model.taxes_per_litre()))

    # Staleness is judged against source mtimes, not a build timestamp: the
    # question is "has a CSV changed since this was built", and comparing to
    # wall-clock time would call the db stale every time you looked at it.
    con.execute("CREATE TABLE meta (source TEXT PRIMARY KEY, mtime REAL)")
    con.executemany("INSERT INTO meta VALUES (?, ?)",
                    [(name, path.stat().st_mtime if path.exists() else 0.0)
                     for name, (path, _) in SOURCES.items()])
    con.commit()
    con.close()
    return counts


def stale(db_path: pathlib.Path = DB) -> str | None:
    """Name of the first source newer than the db, or None if in sync."""
    if not db_path.exists():
        return "(no database yet)"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        seen = dict(con.execute("SELECT source, mtime FROM meta"))
        con.close()
    except sqlite3.Error:
        return "(unreadable database)"
    for name, (path, _) in SOURCES.items():
        now = path.stat().st_mtime if path.exists() else 0.0
        if now > seen.get(name, -1.0) + 1e-6:
            return name
    return None


def ensure(db_path: pathlib.Path = DB) -> bool:
    """Rebuild if a CSV has moved under it. Returns True if it rebuilt."""
    if stale(db_path) is None:
        return False
    build(db_path)
    return True


def connect(db_path: pathlib.Path = DB) -> sqlite3.Connection:
    ensure(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the database is out of date")
    args = ap.parse_args()

    if args.check:
        why = stale()
        if why is None:
            print(f"{DB.relative_to(ROOT)} is in sync with the CSVs")
            return 0
        print(f"{DB.relative_to(ROOT)} is out of date ({why} is newer)",
              file=sys.stderr)
        print("run: python3 backend/db.py", file=sys.stderr)
        return 1

    counts = build()
    print(f"wrote {DB.relative_to(ROOT)}: "
          + ", ".join(f"{n} {t}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
