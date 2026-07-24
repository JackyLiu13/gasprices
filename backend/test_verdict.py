"""Runs backend/verdict.py against tests/vectors.csv — the same file the C++ test
reads. If the firmware and the backend ever disagree, one of these two fails.

    python3 backend/test_verdict.py
"""

from __future__ import annotations

import csv
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from verdict import Config, Input, Tank, Verdict, evaluate  # noqa: E402

VECTORS = pathlib.Path(__file__).resolve().parents[1] / "tests" / "vectors.csv"


def rows():
    with VECTORS.open() as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            yield line
    return


def main() -> int:
    cfg = Config()
    passed = failed = 0

    for row in csv.DictReader(rows()):
        inp = Input(
            today=int(row["today"]),
            pred=[] if row["pred"] == "-" else [int(p) for p in row["pred"].split("|")],
            window_lo=int(row["window_lo"]),
            window_hi=int(row["window_hi"]),
            age_minutes=int(row["age_minutes"]),
            tank=Tank(row["tank"]),
        )
        got = evaluate(inp, cfg)

        want = (
            Verdict(row["expect_verdict"]),
            int(row["expect_level"]),
            int(row["expect_days"]),
            int(row["expect_save"]),
        )
        mine = (got.verdict, got.level_pct, got.days_to_wait, got.save)

        if mine == want:
            passed += 1
            print(f"  ok   {row['name']:<28} {got.verdict.value:<9} "
                  f"level={got.level_pct:<4} days={got.days_to_wait}  \"{got.reason}\"")
        else:
            failed += 1
            print(f"  FAIL {row['name']:<28} got {mine} want {want}")

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
