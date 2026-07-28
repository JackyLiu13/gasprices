"""Read and write backend/forecasts.csv.

The model's memory of what it once believed. build.py appends to it and never
reads it back; everything that scores accuracy reads it and never writes.

Kept append-mostly and upserted on (made_on, target_date, variant) so the two
scheduled runs a day don't double-count, and so re-running build.py by hand
replaces the morning's forecast rather than stacking a second one beside it.
"""

from __future__ import annotations

import csv
import pathlib

from schema import FORECAST_FIELDS, FORECAST_KEY

from paths import FORECASTS


def load(path: pathlib.Path = FORECASTS) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return [r for r in csv.DictReader(f) if r.get("made_on")]


def save(rows: list[dict], path: pathlib.Path = FORECASTS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in FORECAST_KEY))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FORECAST_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FORECAST_FIELDS})


def record(new_rows: list[dict], path: pathlib.Path = FORECASTS) -> int:
    """Upsert `new_rows`. Returns the number of rows in the file afterwards."""
    if not new_rows:
        return len(load(path))

    def key(r):
        return tuple(str(r.get(k, "")) for k in FORECAST_KEY)

    incoming = {key(r): r for r in new_rows}
    kept = [r for r in load(path) if key(r) not in incoming]
    rows = kept + list(incoming.values())
    save(rows, path)
    return len(rows)
