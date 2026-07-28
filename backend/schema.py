"""Column definitions for the CSVs the backend owns.

One definition per file, imported everywhere. These lists used to be copy-pasted
into build.py, backfill.py and log_price.py; three copies of a schema is two
opportunities for a silent column reorder, and csv.DictWriter would have written
the wrong header without complaining.

Nothing here does I/O. It is the shape of the data, not the data.
"""

from __future__ import annotations

import csv

# backend/history.csv — the regional benchmark series.
#
# retail_actual  a price you logged, most trustworthy
# retail_survey  Ontario's weekly survey (GTA regular average)
# retail_model   what this model believed that day — never feed it back into
#                calibration, or the margin fits a price the margin produced
HISTORY_FIELDS = ["date", "rbob_usd_gal", "usd_cad", "wholesale_cad_l",
                  "retail_model", "retail_survey", "retail_actual", "margin"]

# backend/station_prices.csv — individual pump observations.
STATION_PRICE_FIELDS = ["date", "station_id", "price", "source"]

# backend/forecasts.csv — every forward price this model has ever committed to.
#
# Written by build.py each run and never read by it. It exists so the question
# "how wrong were we at a 3-day horizon?" has an answer that does not depend on
# re-deriving what the model would have said under today's calibration.
#
# made_on      the date the forecast was made
# target_date  the date it is a forecast *of*
# horizon      target_date - made_on, in days; 1 = tomorrow
# variant      which predictor produced it (see model.VARIANTS)
# predicted    $/L, REGIONAL BENCHMARK SPACE — never station-shifted
# basis        the level source it was built from: observed / anchored(...) /
#              modelled. A forecast anchored to a five-day-old survey is a
#              different animal from one built on a price you logged this
#              morning, and scoring them together hides that.
FORECAST_FIELDS = ["made_on", "target_date", "horizon", "variant",
                   "predicted", "basis"]

# Upsert keys — what makes a row the "same" row when it is written again.
FORECAST_KEY = ("made_on", "target_date", "variant")
STATION_PRICE_KEY = ("date", "station_id")

# csv.DictWriter's default lineterminator is \r\n. These files are written by
# two machines — your laptop, and the Lambda, which normalises to \n when it
# reads a file back to commit it — so a file's line endings depended on who
# wrote it last. Git then sees every line as changed, and PR #1 hit a conflict
# in all 15 unchanged rows of station_prices.csv for that reason alone.
#
# Pinned to \n once, here, rather than at each writer, so a sixth call site
# can't quietly reintroduce the split.
CSV_LINETERMINATOR = "\n"


def writer(f, fields: list[str]) -> csv.DictWriter:
    """A DictWriter with this project's dialect. Constructs, does not write."""
    return csv.DictWriter(f, fieldnames=fields,
                          lineterminator=CSV_LINETERMINATOR)
