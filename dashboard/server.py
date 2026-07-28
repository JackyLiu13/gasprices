#!/usr/bin/env python3
"""Local server for the analytics dashboard.

    python3 dashboard/server.py          # then open the URL it prints

Serves the repository root, like preview/server.py, so the page can read
docs/data.json and the CSVs straight off disk. On top of static files it adds a
small read API backed by backend/analytics.db, and exactly two writes:

    POST /api/price      log a pump price   (shells backend/log_price.py)
    POST /api/refresh    rebuild the feed   (shells backend/build.py)

Both writes go through the existing CLIs rather than reimplementing the file
handling. log_price.py already owns price validation, station-id resolution, the
upsert and the sort order; a second copy of that in here would be a second thing
to keep correct.

Standard library only, and bound to localhost: it writes to your working tree
and runs subprocesses, so it has no business listening on the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import http.server
import json
import pathlib
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

import analytics  # noqa: E402
import backtest  # noqa: E402
import build as buildmod  # noqa: E402
import db  # noqa: E402
import forecast_log  # noqa: E402
import model  # noqa: E402
import stations as stationlib  # noqa: E402
from schema import HISTORY_FIELDS  # noqa: E402
from verdict import Config  # noqa: E402

DATA_JSON = ROOT / "docs" / "data.json"


# --- data assembly ---------------------------------------------------------

def _history() -> list[dict]:
    return buildmod.load_history()


def _benchmark(rows: list[dict]) -> dict[str, float]:
    return {r["date"]: b for r in rows if (b := buildmod.best_retail(r))}


def api_series(days: int | None) -> dict:
    """The price/target/margin panels, all on one date axis.

    Everything here is REGIONAL BENCHMARK space. docs/data.json is shifted to
    the cheapest station; mixing the two in one chart would put a step in the
    line every time a different station takes the lead.
    """
    rows = _history()
    if days:
        keep = set(analytics.since([r["date"] for r in rows], days))
        rows = [r for r in rows if r["date"] in keep]

    bench = _benchmark(rows)
    margins = analytics.margin_series(rows, buildmod.observed_retail)
    rolled = analytics.rolling_margin(margins)
    targets = analytics.target_series(rows, dict(rolled))

    def source_of(r):
        for key, name in (("retail_actual", "logged"),
                          ("retail_survey", "survey"),
                          ("retail_model", "model")):
            if (r.get(key) or "").strip():
                return name
        return "none"

    return {
        "benchmark": [{"date": d, "v": v, "src": source_of(r)}
                      for r in rows if (d := r["date"]) in bench
                      for v in [bench[d]]],
        "target": [{"date": d, "v": v} for d, v in targets],
        "wholesale": [{"date": r["date"], "v": w}
                      for r in rows
                      if (w := buildmod.fnum(r, "wholesale_cad_l")) is not None],
        "margin": [{"date": d, "v": v} for d, v in margins],
        "margin_rolling": [{"date": d, "v": v} for d, v in rolled],
        "coverage": analytics.coverage(rows, HISTORY_FIELDS),
        "fields": HISTORY_FIELDS[1:],
        "margin_window_days": model.MARGIN_WINDOW_DAYS,
    }


def api_stations() -> dict:
    """The station ladder, with the offset spread stations.py discards."""
    rows = _history()
    bench = _benchmark(rows)
    today = max(bench) if bench else dt.date.today().isoformat()
    sts = stationlib.load_stations()
    stationlib.compute_offsets(sts, bench, today)
    stationlib.predict_all(sts, bench.get(today, 0.0))

    spread = analytics.station_dispersion(stationlib.load_prices(), bench,
                                          today=today)
    best = stationlib.cheapest(sts)
    out = []
    for s in sorted(sts.values(),
                    key=lambda s: (s.predicted is None, s.predicted or 0)):
        d = spread.get(s.id, {})
        out.append({
            "id": s.id, "label": s.label, "brand": s.brand, "city": s.city,
            "role": s.role, "address": s.address,
            "offset": s.offset, "predicted": s.predicted,
            "n": s.observations, "confident": s.confident,
            "last_seen": s.last_seen,
            "mad": d.get("mad"), "span_days": d.get("span_days"),
            "deltas": d.get("deltas", []),
            "is_best": bool(best and best.id == s.id),
        })
    return {"today": today, "benchmark": bench.get(today),
            "confident_at": stationlib.CONFIDENT_OBSERVATIONS,
            "stations": out}


def api_accuracy() -> dict:
    """Forecast error per variant per horizon, against the no-move baseline."""
    rows = _history()
    scored = analytics.forecast_error(forecast_log.load(), _benchmark(rows))
    return {
        "buckets": scored,
        "variants": sorted(model.VARIANTS),
        "default_variant": model.DEFAULT_VARIANT,
        "forecast_count": len(forecast_log.load()),
    }


def api_sweep() -> dict:
    """The backtest grid. Runs the simulation ~70 times, so it is its own call."""
    observed = backtest.load_series()
    series = backtest.to_daily(observed)
    need = backtest.WINDOW_DAYS + 12
    if len(series) < need:
        return {"ready": False, "have": len(series), "need": need}

    horizons = [3, 4, 5, 6, 7]
    thresholds = [5, 10, 15, 20, 25, 30, 40]
    grid = []
    for thr in thresholds:
        row = []
        for hz in horizons:
            cfg = Config(save_threshold=thr, horizon=hz)
            b, _, _ = backtest.simulate(series, cfg, False, follow=False)
            s, _, _ = backtest.simulate(series, cfg, False, follow=True)
            row.append((b - s) * 100)
        grid.append(row)

    cfg = Config()
    trace: list = []
    backtest.simulate(series, cfg, False, follow=True, trace=trace)
    base, bfills, _ = backtest.simulate(series, cfg, False, follow=False)
    strat, sfills, _ = backtest.simulate(series, cfg, False, follow=True)
    orc_b, _, _ = backtest.simulate(series, cfg, True, follow=False)
    orc_s, _, _ = backtest.simulate(series, cfg, True, follow=True)

    return {
        "ready": True,
        "horizons": horizons, "thresholds": thresholds, "grid": grid,
        "days": len(series), "observed": len(observed),
        "current": {"threshold": cfg.save_threshold, "horizon": cfg.horizon,
                    "baseline": base, "strategy": strat,
                    "edge_cents": (base - strat) * 100,
                    "fills": sfills, "baseline_fills": bfills,
                    "oracle_edge_cents": (orc_b - orc_s) * 100},
        "trace": trace,
    }


def api_replay(days: int) -> dict:
    """Per-day engine *inputs*, so the browser can run the device engine itself.

    Deliberately split: the model lives in Python and is not ported, but the
    verdict engine the page runs is preview/verdict.js — the same module the
    firmware's pixel test is held to. Computing the verdict here instead would
    mean the dashboard agreed with backend/verdict.py while the device ran
    something else, which is the one disagreement this project is built to
    prevent.

    Margins are calibrated as of each day, never from the whole series, so a
    replayed verdict only ever saw what the device could have seen.
    """
    series = backtest.to_daily(backtest.load_series())
    margins = backtest.margins_asof(series)
    out = []
    win = backtest.WINDOW_DAYS
    for i in range(len(series)):
        if i < win:
            continue
        date, price, wholesale = series[i]
        window = [p for _, p, _ in series[i - win: i + 1]]
        pred = []
        if wholesale is not None:
            target = model.retail_from_wholesale(wholesale, margins[i])
            pred = [model.to_tenths(p)
                    for p in model.predict(price, target, days)]
        out.append({
            "date": date,
            "today": model.to_tenths(price),
            "pred": pred,
            "window_lo": model.to_tenths(min(window)),
            "window_hi": model.to_tenths(max(window)),
        })
    return {"days": out, "window_days": win}


def api_overview() -> dict:
    published = {}
    if DATA_JSON.exists():
        try:
            published = json.loads(DATA_JSON.read_text())
        except json.JSONDecodeError:
            published = {}
    rows = _history()
    cfg = Config()
    return {
        "published": published,
        "history_rows": len(rows),
        "logged_prices": sum(1 for r in rows
                             if (r.get("retail_actual") or "").strip()),
        "survey_rows": sum(1 for r in rows
                           if (r.get("retail_survey") or "").strip()),
        "config": {k: getattr(cfg, k) for k in
                   ("save_threshold", "low_pct", "high_pct", "jump_level_cap",
                    "max_age_minutes", "horizon", "patience_full_pct",
                    "patience_half_pct")},
        "model": {"margin_window_days": model.MARGIN_WINDOW_DAYS,
                  "passthrough_up": model.PASSTHROUGH_UP,
                  "passthrough_down": model.PASSTHROUGH_DOWN,
                  "wholesale_ema_days": model.WHOLESALE_EMA_DAYS,
                  "hst": model.HST, "taxes": model.taxes_per_litre()},
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
    }


def api_raw() -> dict:
    """Everything the Copy-for-AI bundle needs, in one round trip."""
    return {
        "history_csv": (BACKEND / "history.csv").read_text(),
        "station_prices_csv": (BACKEND / "station_prices.csv").read_text(),
        "stations_csv": (BACKEND / "stations.csv").read_text(),
        "forecasts_csv": (forecast_log.FORECASTS.read_text()
                          if forecast_log.FORECASTS.exists() else ""),
    }


ROUTES = {
    "/api/overview": lambda q: api_overview(),
    "/api/series": lambda q: api_series(int(q["days"][0]) if q.get("days") else None),
    "/api/stations": lambda q: api_stations(),
    "/api/accuracy": lambda q: api_accuracy(),
    "/api/sweep": lambda q: api_sweep(),
    "/api/replay": lambda q: api_replay(int(q["horizon"][0]) if q.get("horizon") else 5),
    "/api/raw": lambda q: api_raw(),
}


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        # A dashboard that serves a cached copy of the price you just logged is
        # worse than no dashboard.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        """Declare UTF-8 on text.

        SimpleHTTPRequestHandler sends text/html with no charset, so browsers
        fall back to latin-1 and every em dash and ¢ in the page turns to
        mojibake. The files are UTF-8; say so.
        """
        ctype = super().guess_type(path)
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/json"):
            return f"{ctype}; charset=utf-8"
        return ctype

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_only(self) -> bool:
        """Reject cross-origin writes.

        The only client is the page this server itself served. Anything else
        reaching a write endpoint means a browser somewhere was talked into
        posting at localhost, so refuse rather than edit the working tree.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        if urlparse(origin).hostname in ("localhost", "127.0.0.1", "::1"):
            return True
        self._json(403, {"error": f"cross-origin request from {origin} refused"})
        return False

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except json.JSONDecodeError:
            return {}

    def _run(self, argv: list[str]) -> tuple[bool, str]:
        r = subprocess.run([sys.executable] + argv, cwd=str(ROOT),
                           capture_output=True, text=True, timeout=180)
        return r.returncode == 0, (r.stdout + r.stderr).strip()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ROUTES:
            try:
                db.ensure()
                self._json(200, ROUTES[path](parse_qs(urlparse(self.path).query)))
            except Exception as e:                       # noqa: BLE001
                # A broken panel should say why, not spin forever.
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
            return
        if path.startswith("/api/"):
            self._json(404, {"error": f"no route {path}"})
            return
        super().do_GET()

    def do_POST(self):
        if not self._local_only():
            return
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/price":
            price = body.get("price")
            if price is None:
                self._json(400, {"error": "no price"})
                return
            argv = [str(BACKEND / "log_price.py"), str(price)]
            if body.get("station"):
                argv += ["--station", str(body["station"])]
            if body.get("date"):
                argv += ["--date", str(body["date"])]
            ok, out = self._run(argv)
            self._json(200 if ok else 400, {"ok": ok, "output": out})
            return

        if path == "/api/refresh":
            # Rewrites backend/history.csv, backend/forecasts.csv and
            # docs/data.json in the working tree. Committing stays manual: the
            # Action publishes, and a page that can push is a page that can
            # publish a wrong price.
            ok, out = self._run([str(BACKEND / "build.py")])
            self._json(200 if ok else 500, {"ok": ok, "output": out})
            return

        self._json(404, {"error": f"no write route {path}"})

    def log_message(self, fmt, *args):
        if self.command == "POST":
            sys.stderr.write(f"POST {self.path} — {fmt % args}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()

    db.ensure()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {ROOT}")
    print(f"open http://127.0.0.1:{args.port}/dashboard/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
