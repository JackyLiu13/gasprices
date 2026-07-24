"""Upstream data fetch. Standard library only, so the GitHub Action needs no
pip install and can't break on a dependency bump.

Every source here is free and unofficial. They WILL fail sometimes — that is
expected and handled by the caller, which keeps the last good JSON rather than
publishing something wrong.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

UA = "gasprices/1.0 (personal gas price indicator)"
TIMEOUT = 20


class FetchError(RuntimeError):
    pass


def _get_json(url: str, attempts: int = 3) -> dict:
    last: Exception | None = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(2 ** i)
    raise FetchError(f"{url}: {last}")


# --- RBOB gasoline futures, USD per US gallon ------------------------------

def rbob_history(days: int = 40, period: str = "3mo") -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, close_usd_per_gal), ...] oldest first, from Yahoo Finance.

    `period` is a Yahoo range string (1mo/3mo/6mo/1y/2y); backfill.py asks for a
    year. Unofficial endpoint — no key, but no SLA either. If it starts 429ing,
    swap in EIA's series (free key, https://api.eia.gov) behind this signature.
    """
    override = os.environ.get("RBOB_OVERRIDE")
    if override:
        return [(time.strftime("%Y-%m-%d"), float(override))]

    # The front-month RBOB contract is "RB=F". Note it is NOT "RBOB=F", which
    # 404s. `range` must be a Yahoo period string (1mo/3mo/...), not a day count.
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/RB%3DF"
           f"?range={period}&interval=1d")
    data = _get_json(url)
    try:
        res = data["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as e:
        raise FetchError(f"unexpected Yahoo payload: {e}") from e

    out = []
    for ts, close in zip(stamps, closes):
        if close is None:          # holidays / half sessions come back null
            continue
        out.append((time.strftime("%Y-%m-%d", time.gmtime(ts)), float(close)))
    if not out:
        raise FetchError("Yahoo returned no usable closes")
    return out[-days:]


# --- USD/CAD ---------------------------------------------------------------

def usd_cad() -> float:
    override = os.environ.get("USDCAD_OVERRIDE")
    if override:
        return float(override)

    try:
        data = _get_json("https://api.frankfurter.app/latest?from=USD&to=CAD")
        return float(data["rates"]["CAD"])
    except (FetchError, KeyError, TypeError):
        pass  # frankfurter is ECB-backed and only publishes on weekdays

    data = _get_json("https://open.er-api.com/v6/latest/USD")
    return float(data["rates"]["CAD"])


def fx_series(start: str, end: str) -> dict[str, float]:
    """{YYYY-MM-DD: usd_cad} over a date range, for historical backfill.

    Applying today's FX to a year of RBOB history would be worth several cents
    per litre of error — USD/CAD moves far more over a year than over the month
    build.py covers. Weekends and holidays are absent; callers forward-fill.
    """
    data = _get_json(f"https://api.frankfurter.app/{start}..{end}?from=USD&to=CAD")
    return {d: float(v["CAD"]) for d, v in data.get("rates", {}).items()}


# --- Ontario official weekly retail survey ---------------------------------

# data.ontario.ca, "Fuels price survey information". Weekly regular-unleaded
# pump prices in cents/L for 10 Ontario markets, back to 1990, updated Mondays,
# Open Government Licence - Ontario. This is a published open dataset, so no
# scraping and no bot-protection to fight.
ONTARIO_FUEL_CSV = "https://ontario.ca/v1/files/fuel-prices/fueltypesall.csv"

# Richmond Hill sits on Yonge Street, which is roughly the line the survey uses
# to split Toronto East from Toronto West, so average the two.
GTA_COLUMNS = ("Toronto East/Est", "Toronto West/Ouest")


def ontario_retail_survey(limit: int | None = None) -> list[tuple[str, float]]:
    """[(YYYY-MM-DD, dollars_per_litre), ...] oldest first, regular unleaded."""
    import csv
    import io

    req = urllib.request.Request(ONTARIO_FUEL_CSV, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8-sig", errors="replace")
    except (urllib.error.URLError, TimeoutError) as e:
        raise FetchError(f"{ONTARIO_FUEL_CSV}: {e}") from e

    out: list[tuple[str, float]] = []
    for row in csv.DictReader(io.StringIO(text)):
        date = (row.get("Date") or "").strip()
        if len(date) != 10 or "Regular" not in (row.get("Fuel Type") or ""):
            continue
        vals = []
        for col in GTA_COLUMNS:
            try:
                cents = float((row.get(col) or "").strip())
            except ValueError:
                continue
            if cents > 0:                      # 0 means "not surveyed"
                vals.append(cents)
        if vals:
            out.append((date, sum(vals) / len(vals) / 100.0))   # cents -> dollars

    if not out:
        raise FetchError("Ontario survey returned no regular-unleaded rows")
    out.sort()
    return out[-limit:] if limit else out


# --- Local retail price ----------------------------------------------------

def local_retail_hint() -> float | None:
    """Today's actual Richmond Hill pump price, in $/L, or None.

    This is the genuinely hard input and there is no clean free API for it.
    Three ways to fill it, in increasing order of effort:

      1. LOCAL_PRICE_OVERRIDE env var / `python3 backend/log_price.py 1.489`
         — you type in what you paid. Zero infrastructure, and after ~30 days of
         logging the model is calibrated well enough to carry the level itself.
      2. Scrape a GTA next-day price tracker once a day and parse the number.
         Put that parser here; it returns $/L or None, and nothing else changes.
      3. Skip it entirely — the model predicts a level from wholesale + margin.
         Less accurate in absolute terms, still fine for the direction signal.

    Returning None is a first-class outcome: build.py falls back to the model.
    """
    override = os.environ.get("LOCAL_PRICE_OVERRIDE")
    return float(override) if override else None
