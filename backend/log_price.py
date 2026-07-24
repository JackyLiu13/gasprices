"""Log the price you actually paid. This is the input that makes everything else
work — it calibrates the margin and anchors the level.

    python3 backend/log_price.py 1.489              # today
    python3 backend/log_price.py 1.489 2026-07-22   # a day you forgot
"""

from __future__ import annotations

import csv
import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISTORY = ROOT / "backend" / "history.csv"
FIELDS = ["date", "rbob_usd_gal", "usd_cad", "wholesale_cad_l",
          "retail_model", "retail_actual", "margin"]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    price = float(sys.argv[1])
    if not 0.5 <= price <= 3.5:
        print(f"{price} $/L doesn't look like a pump price — typo?", file=sys.stderr)
        return 2
    day = sys.argv[2] if len(sys.argv) > 2 else dt.date.today().isoformat()
    dt.date.fromisoformat(day)  # validate

    rows: list[dict] = []
    if HISTORY.exists():
        with HISTORY.open() as f:
            rows = [r for r in csv.DictReader(f) if r.get("date")]

    for r in rows:
        if r["date"] == day:
            r["retail_actual"] = f"{price:.3f}"
            break
    else:
        rows.append({"date": day, "retail_actual": f"{price:.3f}"})
        rows.sort(key=lambda r: r["date"])

    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})

    observed = sum(1 for r in rows if (r.get("retail_actual") or "").strip())
    print(f"logged {price:.3f} $/L for {day} ({observed} observed day(s) on file)")
    if observed < 5:
        print("  margin calibration kicks in at 5 observations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
