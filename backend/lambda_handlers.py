"""Lambda entry points: the scheduled build, and the price-logging endpoint.

Both do the same three things — pull the CSVs out of GitHub into /tmp, run the
existing pipeline against them, push back what changed. No business logic lives
here. `build.py` still decides prices and `log_price.py` still owns validation;
this file is plumbing between them and the network.

GP_DATA_DIR must point at /tmp (the template sets it). Lambda unpacks the code
to /var/task, which is read-only, so anything writing next to the code fails.
`paths.py` exists to make that a one-line configuration rather than a rewrite.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import github_store  # noqa: E402
import paths  # noqa: E402

# The files the pipeline reads and may rewrite, repo-relative.
DATA_FILES = [
    "backend/history.csv",
    "backend/station_prices.csv",
    "backend/stations.csv",
    "backend/forecasts.csv",
]
OUTPUT_FILES = DATA_FILES + ["docs/data.json"]


def _pull(store) -> None:
    paths.ensure_dirs()
    store.pull(DATA_FILES, paths.DATA_DIR)


def _push(store, message: str) -> str | None:
    files = {}
    for rel in OUTPUT_FILES:
        local = paths.DATA_DIR / rel
        if local.exists():
            files[rel] = local.read_text()
    return store.commit(files, message)


def _run_build() -> None:
    """Run build.py's main() in-process.

    Imported lazily and with argv stubbed: build.py parses arguments at call
    time, and a Lambda's argv is not something you want it reading.
    """
    import build
    argv, sys.argv = sys.argv, ["build.py"]
    try:
        rc = build.main()
    finally:
        sys.argv = argv
    if rc != 0:
        raise RuntimeError(f"build.py exited {rc}")


def _summary() -> str:
    try:
        d = json.loads((paths.DATA_JSON).read_text())
        return f"{d['today_cad'] / 1000:.3f}/L {d['verdict_hint']}"
    except Exception:
        return "rebuild"


# ---------------------------------------------------------------------------
# Scheduled build — EventBridge
# ---------------------------------------------------------------------------
def build_handler(event, context):
    store = github_store.from_env()
    _pull(store)
    _run_build()
    sha = _push(store, f"data: {_summary()}")
    return {"ok": True, "commit": sha, "summary": _summary()}


# ---------------------------------------------------------------------------
# Logging endpoint — Lambda Function URL
# ---------------------------------------------------------------------------
def _reply(code: int, body: dict) -> dict:
    return {"statusCode": code,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(body)}


def log_handler(event, context):
    # Function URLs lowercase header names, but be tolerant anyway.
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    expected = os.environ.get("GP_INGEST_SECRET", "")
    if not expected or headers.get("x-gp-secret") != expected:
        return _reply(401, {"ok": False, "error": "bad or missing x-gp-secret"})

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _reply(400, {"ok": False, "error": "body is not JSON"})

    station = str(payload.get("station") or "").strip()
    source = str(payload.get("source") or "dispatch").strip()

    # An absent station is NOT harmless: log_price treats "no station" as "this
    # is the regional benchmark" and would overwrite the series the whole model
    # is anchored to. Same guard as the GitHub Action.
    if not station:
        return _reply(400, {"ok": False, "error": "station is required"})
    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return _reply(400, {"ok": False, "error": "price must be a number"})

    store = github_store.from_env()
    _pull(store)

    import log_price
    known = log_price.stationlib.load_stations()
    sid = log_price.resolve(station, known)
    if sid is None:
        return _reply(400, {"ok": False, "error": f"unknown station {station!r}"})
    if not 0.5 <= price <= 3.5:
        return _reply(400, {"ok": False, "error": f"{price} is not a pump price"})

    import datetime as dt
    day = dt.date.today().isoformat()
    log_price.log_station(price, day, sid, known[sid].label, source)

    # Rebuild immediately so the device sees the new offset on its next fetch
    # rather than waiting for the next scheduled run.
    try:
        _run_build()
    except Exception:
        traceback.print_exc()
        # The price is still worth keeping even if the rebuild failed — a
        # transient upstream outage shouldn't discard what you typed at a pump.
        _push(store, f"log: {known[sid].label} {price:.3f} (rebuild failed)")
        return _reply(202, {"ok": True, "logged": True, "rebuilt": False,
                            "station": sid, "price": price})

    sha = _push(store, f"log: {known[sid].label} {price:.3f} -> {_summary()}")
    return _reply(200, {"ok": True, "logged": True, "rebuilt": True,
                        "station": sid, "label": known[sid].label,
                        "price": price, "commit": sha,
                        "summary": _summary()})
