# AGENT_NOTES.md

How to take my requests on this project.

[`CLAUDE.md`](CLAUDE.md) is the code facts — what the files are, what the
conventions are, what will break. This file is the other half: what I actually
mean when I ask for something, and what I want you to do when the request and
the evidence disagree.

Written for whoever picks this up next, human or model. If you disagree with
something here, say so — but say so *before* you act on the disagreement.

---

## The one-paragraph version

This is a small, honest instrument. It exists to answer "should I fill up, and
where?" and to be right about it. I would rather it say "I don't know" than say
something confident and wrong. Almost every rule below is a specific case of
that.

---

## What "improve the prediction" means

It does **not** mean "make the model more sophisticated." It means: move a
number on the dashboard, and show me which one.

There are two axes and they are not equal:

- **Where you fill up** — the station offset model. Measured spread across
  Richmond Hill on 2026-07-27 was **9 ¢/L**.
- **When you fill up** — the timing engine. Its measured edge over "assume no
  move" is about **0.5–0.8 ¢/L**, with an oracle ceiling near **1.15 ¢/L**.

Where beats when by roughly 5×. So:

- A change that improves timing MAE while degrading the station model is a
  **loss**, even if the timing number looks impressive.
- The highest-value change is almost always **more logged prices**, not more
  model. If a request could be satisfied by logging a price, say so first.
- The timing verdicts are the *minor* axis. Keep them, don't foreground them,
  don't spend a week on them.

**Before and after, or it didn't happen.** Any claim that something improved
needs the accuracy table from the dashboard (or `backend/backtest.py`) on both
sides, with `n`. "This should be more accurate" is not a result.

**Know the ceiling before you optimise.** The sweep panel reports what fraction
of the oracle's edge the current rules already capture — it has been around
68%. If the rules capture most of the ceiling, better forecasting cannot pay for
itself and you should be working on the station model instead. Check this before
proposing a modelling change, not after.

## What is load-bearing

Change these only deliberately, and tell me you did:

- **The engine exists three times** — `firmware/gasprices/verdict.h` (C),
  `backend/verdict.py`, `preview/verdict.js` — pinned by `tests/vectors.csv`.
  Change a rule, change all three, or CI fails. This is not duplication to be
  cleaned up; it is what lets the device keep working when the backend is down.
- **`tests/vectors.csv` columns 0–10** are the engine contract and are indexed by
  position. **Append, never insert.**
- **Prices are integers in tenths of a cent** on the device and in the JSON.
  `$1.489/L` is `1489`. The chip has no FPU.
- **Windows are date-based, never row-based.** `history.csv` is weekly for its
  first ~60 rows and daily after. This has caused two real bugs. If you write
  `[-30:]` on a history slice, you are writing the third.
- **Calibration never consumes the model's own output.** Only logged prices and
  the Ontario survey feed margin and offset calibration. `retail_model` is not
  evidence.
- **`build.py` fails closed.** It exits non-zero and leaves the previous
  `docs/data.json` in place rather than publish a wrong number. A day-old price
  is fine — the firmware says `STALE`. A wrong price is not fine. Never add a
  fallback that publishes a guess.
- **Backfilling the forecast log from history is forbidden.** Scoring a forecast
  the model never made, under a margin it did not have, is the self-referential
  trap this project is built to avoid. `backfill_forecasts.py` is allowed only
  because it reads forecasts that were *actually published*, from git.
- **`firmware/gasprices/layout.h` is generated.** Never hand-edit it.
- **`preview/render.js` must match `ui.h` pixel for pixel.**

## Decisions I want you to make without asking

- **Fewer dependencies over convenience.** Everything here is stdlib Python and
  vanilla ES modules, with no build step, on purpose. Do not add a package to
  save yourself twenty lines. If something genuinely needs a dependency, that is
  a conversation, not a commit.
- **A measured number over a plausible story.** If you can compute it, compute
  it. The rockets-and-feathers asymmetry *sounded* right and was wrong; it only
  fell over when someone measured it against a centered margin.
- **"The sample is too small" over drawing the line anyway.** With `n=3`, say
  `n=3`. Every panel on the dashboard states its own sample size for this
  reason. An empty chart that says why is better than a confident one that lies.
- **Match the surrounding code.** Comment density, naming, and the habit of
  explaining *why* in the comment rather than *what*.
- **Keep the honest boundary visible.** When something can't be modelled — panel
  gamma, GasBuddy, the true daily passthrough rate at weekly resolution — write
  down that it can't, where the next person will look.

## How to read the things I say

| I say | I mean |
|---|---|
| "make it better" | Show me the current measurement first, then propose. Don't rewrite anything yet. |
| "add a station" | A row in `backend/stations.csv` plus one logged price. Not new code. Label ≤ 18 chars. |
| "this number looks wrong" | Check station-shift space **first** — `docs/data.json` is rebased to the cheapest station and the CSVs are not. Most "bugs" here are that. Then check units (tenths vs dollars). Then check the model. |
| "can we predict better" | Look at the oracle ceiling before touching the model. Then check whether the station offsets are the real bottleneck. |
| "clean this up" | Readability and duplication, not architecture. Don't collapse the three engines. |
| "just make it work" | Correctly. Not by adding a fallback that publishes a guess. |
| "add a dashboard panel" | It must state its own `n` and say when the sample can't support a conclusion. |

## What to report back

Every time you change something that touches the model:

1. **Which axis moved** — station (where) or timing (when).
2. **The number, before and after**, with `n` behind it.
3. **What got worse.** Something usually does. If nothing did, say what you
   checked to be sure.
4. **What you did not verify**, plainly. I would rather know a gap exists than
   find it later.

Don't tell me a change is "more robust" or "more accurate" without a number
attached. Don't apologise at length for a mistake — correct it and move on.

## Things that are settled, don't relitigate

- **GasBuddy cannot be automated.** Cloudflare 403s every automated request.
  Building fingerprint-spoofing or headless-stealth workarounds is detection
  evasion, against their ToS, and brittle. Manual logging is the answer.
- **Rockets-and-feathers was measured and rejected here.** Passthrough is
  symmetric at 0.50/day. The `asymmetric` variant is kept in `model.VARIANTS`
  and scored every run precisely so this stays evidence-based — if it ever
  starts winning, the forecast log will show it.
- **The margin is not a constant.** ~27 ¢/L in late 2025, ~15 ¢/L by mid-2026.
  The 90-day window is short on purpose.
- **Deep sleep kills the backlight** (GPIO22 isn't an RTC pin on the C6). Long
  battery life, not a persistent display. Not a bug to fix.
- **The timing model's edge is small and that is the honest output**, not a
  defect to engineer away.

## Where to look

- [`DASHBOARD.md`](DASHBOARD.md) — how the analytics dashboard and the forecast
  log fit together, and how to run them.
- [`README.md`](README.md) — the decision logic and the data model.
- [`CLAUDE.md`](CLAUDE.md) — conventions, gotchas, and the measured findings that
  shaped the design.
- The dashboard's **Copy for AI** button — a self-contained context bundle with
  the current state, the accuracy table, the station ladder and the raw CSVs,
  with the units and station-shift warnings already in it. Start there rather
  than reading six files.
