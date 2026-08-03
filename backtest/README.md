# Blind replay

The walk-forward audit inside the engine grades each matcher against its own
history. It cannot answer whether the board as a whole makes money, because it
never leaves the symbol. This directory does: it replays the complete board
across the market and across a decade, then measures what actually happened.

## What a replay cell is

For one symbol on one historical session, the engine receives a price history
and SPY/VIX context **physically truncated at that session** and runs the exact
audited pipeline the live board uses — `_build_analysis` with the full
walk-forward audit, then `rank_analysis`. The verdict it produces is the verdict
the board would have published that night.

Only afterwards is the untruncated series consulted, using the same convention
the production grader settles on: **buy the open of the session after the
signal, measure to the close of the 21st session** (see
`storage.grade_pending_forecasts`). Nothing after the as-of session is visible
to the forecast.

The harness was verified against the production Time Machine endpoint: for the
same symbol and date, probability, edge, evidence, range, audit grade, Brier
skill, tier, and conviction score match exactly.

## The stored panel

`replay_2016_2026.csv` holds 26,347 cells — 220 symbols × 126 as-of sessions,
2016-01-04 to 2026-06-12, about 38 CPU-hours. As-of dates are spaced 21 sessions
apart so **no two holding windows overlap**, which makes the 126 periods
independent in time rather than 2,600 correlated ones.

Every score input is stored alongside the outcome, so changes to
`rank_analysis` scoring or tier thresholds can be re-scored against all 26,347
forecasts in seconds. Only changes to the matching engine or the feature set
require a fresh run.

## Running it

```powershell
python backtest/run_replay.py
```

`BT_SYMBOLS` sets the universe size and `BT_WORKERS` the process count. Results
stream to CSV per symbol, and a rerun resumes rather than recomputing whatever
is already present. Budget roughly 3.7 seconds per cell per worker.

`market_context.csv` is a cached SPY/VIX series so a replay makes no network
calls; delete it to refetch.

```powershell
python backtest/analyze_replay.py
```

## Reading the output honestly

Three limits are structural and no amount of compute fixes them.

**Survivorship.** The price cache holds currently-listed symbols. Companies that
delisted between 2016 and now are absent, which flatters long returns and
punishes short returns. This is why the headline metric is excess return against
an equal-weight basket of *the same symbols on the same date* — both legs carry
the identical bias, so subtracting cancels most of it. Raw return is not the
trustworthy figure.

**Outliers.** A handful of microcap moonshots dominate the untrimmed arithmetic
mean, and the algorithm avoids them by design. The equal-weight universe returns
+2.80% per period raw and +0.98% winsorized at 2/98 within date. Report both;
the disagreement is itself a finding.

**Selectivity.** A 220-name sample makes "top 3" the best of ~172. The live board
picks 3 from ~3,800. This panel cannot tell you what the real board's top few
would have done.

## Statistics

t-statistics are computed on date-level means, so cross-sectional correlation
within a date cannot inflate significance. Confidence intervals use a stationary
block bootstrap with 4-period blocks to preserve regime persistence.

Fit on the first half, validate on the second. The value of doing so is not
theoretical: the strongest pattern found in training — that the conviction score
runs *backwards*, with the lowest quintile beating the highest at t=2.7 — did
not replicate out of sample. Reported without the split, it would have looked
like a discovery.
