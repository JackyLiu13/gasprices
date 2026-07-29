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

import base64
import datetime as dt
import json
import os
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import github_store  # noqa: E402
import paths  # noqa: E402

# Lambda's clock is UTC, and an observation is dated in the timezone it was seen
# in. Without this, anything logged after 8pm Toronto lands on tomorrow's date —
# at the newest end of the calibration window, anchoring the model to a day that
# has not happened. /usr/share/zoneinfo ships on the Lambda runtime; the fallback
# is only for a stripped container, where UTC is still better than crashing.
try:
    from zoneinfo import ZoneInfo

    LOCAL_TZ = ZoneInfo("America/Toronto")
except Exception:                                        # noqa: BLE001
    LOCAL_TZ = dt.timezone.utc


def _now_local() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ)

# The files the pipeline reads and may rewrite, repo-relative.
DATA_FILES = [
    "backend/history.csv",
    "backend/station_prices.csv",
    "backend/stations.csv",
    "backend/forecasts.csv",
]
OUTPUT_FILES = DATA_FILES + ["docs/data.json", "docs/stations.json"]

# Secrets are read from SSM at runtime rather than injected as function config.
# CloudFormation cannot resolve SecureString parameters at deploy time at all,
# and runtime lookup means rotating a secret is one put-parameter call with no
# redeploy. Cached per container, so a warm Lambda pays for it once.
_secret_cache: dict[str, str] = {}


def _secret(param_env: str, direct_env: str) -> str:
    """Resolve a secret: the SSM parameter named by `param_env`, or the literal
    in `direct_env` (which is how the local tests supply one)."""
    direct = os.environ.get(direct_env)
    if direct:
        return direct

    name = os.environ.get(param_env)
    if not name:
        raise RuntimeError(f"neither {direct_env} nor {param_env} is set")
    if name not in _secret_cache:
        # boto3 ships in the Lambda runtime; imported lazily so importing this
        # module on a laptop without boto3 still works.
        import boto3
        ssm = boto3.client("ssm")
        got = ssm.get_parameter(Name=name, WithDecryption=True)
        _secret_cache[name] = got["Parameter"]["Value"]
    return _secret_cache[name]


def _store():
    repo = os.environ.get("GP_REPO")
    if not repo:
        raise RuntimeError("GP_REPO must be set")
    token = _secret("GP_GITHUB_TOKEN_PARAM", "GP_GITHUB_TOKEN")
    return github_store.GitHubStore(repo, token,
                                    os.environ.get("GP_BRANCH", "main"))


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
    store = _store()
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
    expected = _secret("GP_INGEST_SECRET_PARAM", "GP_INGEST_SECRET")
    if not expected or headers.get("x-gp-secret") != expected:
        return _reply(401, {"ok": False, "error": "bad or missing x-gp-secret"})

    # Function URLs base64-encode the body whenever the content type isn't one
    # they consider text — which includes curl's default of
    # application/x-www-form-urlencoded. Decoding unconditionally on the flag
    # means callers don't have to remember to set a JSON content type, and an
    # ESP32 posting with whatever header it likes still works.
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return _reply(400, {"ok": False, "error": "body is not decodable"})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _reply(400, {"ok": False, "error": "body is not JSON"})

    station = str(payload.get("station") or "").strip()
    source = str(payload.get("source") or "dispatch").strip()

    # Lookup mode: {"lat": .., "lon": ..} with no price asks "what am I standing
    # at?" and writes nothing. It exists so a phone can offer you the right
    # station to confirm instead of making you find it in a list of 19, while
    # the choice itself stays with a human — two of these stations are 94 m
    # apart on opposite corners of one intersection, which is inside GPS error.
    # Kept on this endpoint rather than a second one so there is one URL and one
    # secret on the phone.
    if payload.get("lat") is not None and payload.get("price") is None:
        try:
            lat, lon = float(payload["lat"]), float(payload["lon"])
        except (TypeError, ValueError, KeyError):
            return _reply(400, {"ok": False,
                                "error": "lat and lon must both be numbers"})
        # Only the registry, not all four CSVs: this path never runs the model,
        # and /tmp starts empty on a cold container so *something* must be
        # fetched before load_stations sees a file at all.
        paths.ensure_dirs()
        _store().pull(["backend/stations.csv"], paths.DATA_DIR)

        import log_price
        found = log_price.stationlib.nearest(lat, lon,
                                             log_price.stationlib.load_stations())
        return _reply(200, {
            "ok": True, "logged": False,
            "nearest": [
                {"id": s.id, "label": s.label, "brand": s.brand,
                 "address": s.address, "meters": round(d)}
                for s, d in found],
            # The same labels, flat, nearest first. Redundant on purpose: a
            # phone Shortcut can feed a flat array straight into a picker,
            # whereas pulling one key out of an array of objects costs it a
            # loop — three actions of visual scripting, dragged into a Repeat
            # block by hand, to do what this line does once. Building the list
            # here also means `station` goes back exactly as it came, so
            # log_price.resolve() matches it as a label with nothing to strip.
            "labels": [s.label for s, _ in found],
        })

    # An absent station is NOT harmless: log_price treats "no station" as "this
    # is the regional benchmark" and would overwrite the series the whole model
    # is anchored to. Same guard as the GitHub Action.
    if not station:
        return _reply(400, {"ok": False, "error": "station is required"})
    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        return _reply(400, {"ok": False, "error": "price must be a number"})

    store = _store()
    _pull(store)

    import log_price
    known = log_price.stationlib.load_stations()
    sid = log_price.resolve(station, known)
    if sid is None:
        return _reply(400, {"ok": False, "error": f"unknown station {station!r}"})
    if not 0.5 <= price <= 3.5:
        return _reply(400, {"ok": False, "error": f"{price} is not a pump price"})

    # An explicit time lets a device that buffered a reading while offline say
    # when it actually saw it, instead of when it managed to reach the endpoint.
    now = _now_local()
    day = str(payload.get("date") or now.date().isoformat())
    time = str(payload.get("time") or now.strftime("%H:%M"))
    try:
        if dt.date.fromisoformat(day) > now.date():
            return _reply(400, {"ok": False, "error": f"{day} is in the future"})
        time = dt.time.fromisoformat(time).strftime("%H:%M")
    except ValueError:
        return _reply(400, {"ok": False,
                            "error": "date must be YYYY-MM-DD and time HH:MM"})

    log_price.log_station(price, day, sid, known[sid].label, source, time)

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
