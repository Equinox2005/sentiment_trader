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

## Sampling beyond the default grid

The default 126 dates are one of 21 possible phases: same start, same 21-session
step. A result that only holds on that phase is an artifact of it, so
`run_replay.py` takes two overrides.

```powershell
$env:BT_RANDOM_DATES=30                                   # sessions off the grid
$env:BT_CONSECUTIVE="2019-05-06:6,2022-06-06:6"           # consecutive sessions
```

Both were worth running.

**Off-grid (`replay_offgrid.csv`, 30 dates, 6,279 cells).** The long-only result
did not reproduce. Top-10 went from +14.8% CAGR on the grid to −11.0% off it,
and every other K flipped from strongly positive to flat or negative. Year mix
does not explain it: re-weighting the grid to the off-grid year composition
still gives +0.357%. Nor does market regime, which predicts the opposite sign.

Thirty dates cannot refute 126 on their own — the off-grid interval is
[−0.56%, +0.51%] and contains the original estimate. What matters is the pooled
number across all 156 dates:

| Sample | Dates | Excess | t | 95% CI |
| --- | ---: | ---: | ---: | --- |
| Original grid | 126 | +0.311% | 2.09 | [+0.02%, +0.60%] |
| Off-grid | 30 | −0.026% | −0.09 | [−0.56%, +0.51%] |
| **Pooled** | **156** | **+0.246%** | **1.88** | **[−0.01%, +0.50%]** |

The original edge barely cleared zero. Adding fresh dates puts it back under.
Treat the long-only edge as **not statistically established**.

**Consecutive (`replay_consecutive.csv`, 18 sessions across 2019, 2022, 2025).**
Day-over-day turnover of the top-10 long board is 36-48%, not the 5-15% a
trailing-window feature set suggests. The cause is the eligible pool itself: it
moved from 35 to 77 names in six sessions in 2019 and 80 to 116 in 2022. Names
cross the gates constantly, so the ranking is redrawn from a shifting
population. That is threshold noise, not new signal.

## Screening a candidate factor

`fetch_short_interest.py`, `screen_short_interest.py`, and `screen_breadth.py`
test a factor against the stored panel in seconds rather than paying 3.2 hours
to integrate it first. `search_next_day.py` sweeps a wide feature space for
next-day predictors with the multiple-testing threshold reported alongside.

That last one is the most useful calibration in the directory. It finds real,
replicating effects — overnight gap reversal at +7.61 basis points a day,
t=5.48 out of sample — and then shows the same effect is exactly zero
(−0.12bp, t=−0.06) among the top 30% by dollar volume, the only names where
spreads are tight enough to trade it. Statistically bulletproof, economically
dead. Check the basis points before believing the t-statistic.

## Statistics

t-statistics are computed on date-level means, so cross-sectional correlation
within a date cannot inflate significance. Confidence intervals use a stationary
block bootstrap with 4-period blocks to preserve regime persistence.

Fit on the first half, validate on the second. The value of doing so is not
theoretical: the strongest pattern found in training — that the conviction score
runs *backwards*, with the lowest quintile beating the highest at t=2.7 — did
not replicate out of sample. Reported without the split, it would have looked
like a discovery.
