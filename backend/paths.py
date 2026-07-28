"""Where the data lives. One module, so it can be pointed somewhere else.

Every other module used to compute its own
`ROOT = pathlib.Path(__file__).resolve().parents[1]` and write next to the
code. That is fine on a laptop and fine on a GitHub runner, and it is fatal
anywhere the code ships read-only — AWS Lambda unpacks to /var/task, where the
only writable directory is /tmp.

So locations resolve from GP_DATA_DIR, defaulting to the repo, which keeps
local behaviour byte-identical:

    python3 backend/build.py                          # writes in the repo
    GP_DATA_DIR=/tmp/gp python3 backend/build.py      # writes under /tmp/gp

The layout *inside* the data dir mirrors the repo (backend/*.csv, docs/*.json)
so the same tree can be copied either way without rewriting paths, and a test
fixture looks exactly like a real checkout.

analytics.db is deliberately NOT here-and-fixed: it is derived and gitignored,
so it belongs in a scratch location when running somewhere ephemeral.
"""

from __future__ import annotations

import os
import pathlib

# The repository itself: two levels up from this file (backend/paths.py).
REPO = pathlib.Path(__file__).resolve().parents[1]

# Where the CSVs and published JSON live. Override for Lambda, tests, or a
# dry run against a fixture directory.
DATA_DIR = pathlib.Path(os.environ.get("GP_DATA_DIR", REPO))

BACKEND = DATA_DIR / "backend"
DOCS = DATA_DIR / "docs"

# Canonical inputs — these are the files git tracks and calibration reads.
HISTORY = BACKEND / "history.csv"            # regional benchmark series
STATION_PRICES = BACKEND / "station_prices.csv"  # per-station observations
STATIONS = BACKEND / "stations.csv"          # station registry
FORECASTS = BACKEND / "forecasts.csv"        # what the model committed to

# Published output — the ~4 KB the device actually fetches.
DATA_JSON = DOCS / "data.json"

# Derived, gitignored, rebuildable from the CSVs at any time.
DB = pathlib.Path(os.environ.get("GP_DB_PATH", BACKEND / "analytics.db"))


def ensure_dirs() -> None:
    """Create the directory layout. A no-op in the repo, load-bearing under
    /tmp where nothing exists yet."""
    BACKEND.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
