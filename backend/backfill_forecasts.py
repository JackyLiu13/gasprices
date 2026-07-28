"""One-shot: recover the forecasts this project has already published.

    python3 backend/backfill_forecasts.py            # write them
    python3 backend/backfill_forecasts.py --dry-run  # just show them

Every commit of docs/data.json is a forecast the model actually committed to, in
public, on a known date. That is a real track record sitting in git, and it costs
nothing to read back — so the accuracy panel does not have to start empty and
wait a week to say anything.

WHY THIS IS NOT THE SELF-REFERENTIAL TRAP
-----------------------------------------
The rule this project holds to is that calibration must never consume the
model's own output, and that you must not score a forecast the model never made.
This does neither. It does not re-derive anything: it reads the literal `pred`
array that was published that day, under the margin and level source in force
that day. Re-running today's model over old inputs would be the forbidden thing;
reading what the old model actually said is just record-keeping.

Two conversions, both exact:

  schema 2  prices are shifted to the cheapest station. meta.station_shift_cad_l
            records the shift, so subtracting it recovers benchmark space.
  schema 1  predates station pricing entirely — commit d9a6c47 introduced it —
            so those files are already in benchmark space and the shift is 0.

Where a day was published more than once (a rebuild, a corrected price), the
last commit of that day wins: it is what the device would have been holding
overnight.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import forecast_log  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = "docs/data.json"


def commits() -> list[str]:
    """Commit hashes touching docs/data.json, newest first."""
    r = subprocess.run(["git", "log", "--format=%H", "--", DATA],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"git log failed: {r.stderr.strip()}")
    return [h for h in r.stdout.split() if h]


def payload_at(commit: str) -> dict | None:
    r = subprocess.run(["git", "show", f"{commit}:{DATA}"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows: list[dict] = []
    seen: set[str] = set()

    for commit in commits():                       # newest first
        d = payload_at(commit)
        if not d or not d.get("pred"):
            continue
        made_on = (d.get("updated") or "")[:10]
        if not made_on or made_on in seen:
            continue                               # keep the day's last publish
        seen.add(made_on)

        # Recover benchmark space. Schema 1 has no stations, so no shift.
        shift = float(d.get("meta", {}).get("station_shift_cad_l", 0.0) or 0.0)
        basis = d.get("meta", {}).get("level_source", "unknown")

        for i, tenths in enumerate(d["pred"], start=1):
            target = dt.date.fromisoformat(made_on) + dt.timedelta(days=i)
            rows.append({
                "made_on": made_on,
                "target_date": target.isoformat(),
                "horizon": i,
                "variant": "passthrough",
                "predicted": f"{tenths / 1000.0 - shift:.4f}",
                "basis": basis,
            })

    if not rows:
        print("no published forecasts found in git history", file=sys.stderr)
        return 1

    days = sorted(seen)
    print(f"{len(rows)} forecasts recovered from {len(days)} published day(s): "
          f"{days[0]} .. {days[-1]}")
    if args.dry_run:
        for r in sorted(rows, key=lambda r: (r["made_on"], r["horizon"])):
            print(f"  {r['made_on']} +{r['horizon']}d -> {r['target_date']} "
                  f"{r['predicted']}  [{r['basis']}]")
        return 0

    total = forecast_log.record(rows)
    print(f"backend/forecasts.csv now holds {total} row(s)")
    print("These are historical. build.py appends new ones from here on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
