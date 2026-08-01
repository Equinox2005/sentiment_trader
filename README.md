# Playbook

**Find today's closest historical market twins. See every future that followed.**

Playbook is a no-login historical-analog forecasting website for stocks, ETFs, indices, and crypto. It converts the complete current setup into a market fingerprint, searches up to 20 years for independent look-alike episodes, replays their next month, and shows whether this exact matching method worked at untouched historical checkpoints.

It does not hide uncertainty behind a single indicator or an unexplained confidence number. Every forecast exposes:

- today's fingerprint;
- every dated historical match;
- the paths and outcome distribution that produced the estimate;
- the asset's normal up-rate, which the analogs must beat;
- an uncertainty range based on effective independent evidence;
- walk-forward performance against a simple baseline;
- probability calibration, regime/edge strata, and a continuous long-or-cash audit;
- 5-session, 10-session, and primary-horizon distributions;
- empirically measured split-conformal interval coverage;
- hindsight-sealed Time Machine replays and an immutable live forecast ledger;
- a nightly S&P 500 opportunity board that ranks only positively audited bullish setups;
- any small adjustment made by current headlines.

## What the fingerprint contains

The engine uses adjusted OHLCV data and, when available, aligned SPY/VIX context:

- 1-month and 3-month momentum;
- correctly handled Wilder-style RSI;
- short-, medium-, and long-term trend structure;
- current volatility and volatility expansion/contraction;
- drawdown from the trailing one-year high;
- ATR and five-day candlestick pressure;
- five-day versus twenty-day volume;
- prior-session SPY momentum/trend and trailing VIX percentile;
- the normalized geometry of the complete trailing one-month price path.

Missing optional inputs are excluded consistently from both live matching and historical validation. Broad-market context is lagged by one US session so an earlier-closing global market never receives a US close that had not happened yet.

## How matching works

1. **Fingerprint today.** Every feature is trailing/as-of; future data is never used to describe a historical day.
2. **Scale robustly.** Candidate history determines each feature's median and median absolute deviation. Extreme values are clipped so one crisis observation cannot dominate the distance.
3. **Select the weighting model.** Balanced, price-structure, regime, and participation profiles compete on the oldest walk-forward checkpoints. The lowest-Brier profile wins.
4. **Calibrate uncertainty separately.** A later, disjoint historical slice expands the raw outcome band using split-conformal nonconformity.
5. **Protect the audit.** Newer checkpoints remain untouched until the chosen profile and interval are evaluated every five sessions.
6. **Find independent twins.** Weighted feature distance and one-month chart-shape distance rank past dates. Up to 30 matches are selected. Exchange-traded assets use 21-session shapes and 42-session episode spacing; seven-day markets use 30-calendar-day shapes and 60-day spacing. The recent exclusion window prevents a candidate's forward path from overlapping today's fingerprint.
7. **Weight the evidence.** Closer matches contribute more through a stable kernel whose bandwidth targets a healthy effective sample size. Evidence is capped by the number of calendar years represented.
8. **Beat the base rate.** The probability is shrunk toward the asset's own as-of historical up-rate. A bullish or bearish verdict requires an edge over that rate plus a compatible median return—not merely a result above or below 50%.

The **match score** is a distance score from 1–99. It is not a probability.

## Forecasts, news, and risk

The headline horizon is one market month: 21 trading sessions for exchange-traded assets and 30 calendar days for seven-day markets such as crypto. Momentum, volatility annualization, trend windows, shape matching, spacing, projections, and validation all use that same detected sampling calendar. Playbook also reports 5-session/day and 10-session/day distributions. Every horizon exposes its as-of base rate, shrunk up-probability, median return, and raw 20th–80th percentile return band.

For the headline endpoint, the raw band is expanded by a split-conformal adjustment learned only from the interval-calibration period. The UI reports the target, raw holdout coverage, adjusted holdout coverage, and expansion size. Coverage misses remain visible rather than being relabeled as confidence.

Recent headline sentiment can move the displayed probability by at most five percentage points. News can strengthen, weaken, or cancel an analog edge, but it cannot create one or reverse its direction. Historical article sentiment is not used in matching because the free data source does not provide a reliable historical news archive.

Stops and targets come from each matched path's actual intramonth adverse and favorable excursion—not its month-end return. The plan simulates which level was touched first and includes unresolved paths at their final return. If those paths do not support positive expectancy and defensible reward/risk, Playbook refuses to manufacture a trade.

Known upcoming earnings within seven days are shown as catalyst risk rather than pretended to be an ordinary analog feature.

## Walk-forward reliability

For each historical checkpoint, Playbook:

1. uses only information available on that date;
2. builds scaling parameters from earlier candidate history;
3. excludes candidates whose future outcome was not yet known;
4. runs the same matcher used for today's forecast;
5. compares its probability, direction, and projected range with the realized outcome.

The untouched evaluation period is sampled every five sessions and capped at 260 dense records. Because adjacent outcomes overlap, the 95% Wilson interval and the strategy audit use a non-overlapping subset. The UI reports directional accuracy, Brier skill versus the asset-specific base rate, calibration buckets, edge/regime strata, interval coverage, and a daily mark-to-market long-or-cash curve against continuous buy-and-hold. A small or weak sample is labeled accordingly.

Time Machine applies the same engine to a history physically truncated after the selected session; historical news and current earnings metadata are excluded. The realized outcome is calculated only after the forecast payload exists. Live audited forecasts are stored once per confirmed completed session and graded from a single current adjusted-price vintage after the exact future-session count matures.

## Run locally

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open [http://localhost:8000](http://localhost:8000). Yahoo Finance supplies market data; no API key is required.

For a Windows production process, use Waitress:

```powershell
python serve.py
```

Set `PORT`, `HOST`, and `WAITRESS_THREADS` to override its defaults.

### Persistent adjusted-price cache

Playbook stores adjusted OHLCV in `instance/playbook.sqlite3` and refreshes only the latest overlap on normal requests. It performs a full refresh weekly, when `?refresh=1` is used, or when the overlap reveals a split/adjustment drift above 0.5%. Set `PLAYBOOK_DATA_CACHE` to place the SQLite database elsewhere.

Quick forecasts also persist a short-lived, opaque source-snapshot token. The audit endpoint uses that token to reload the exact same prices, profile, headlines, warnings, and market context—even when a different Gunicorn or Waitress worker handles the request. Expired and excess snapshots are removed automatically.

The same SQLite database holds the live forecast ledger. Forecast inserts are immutable per symbol/session/horizon, unfinished daily candles are never recorded or graded, and refreshed split/dividend adjustments are applied consistently to both grading endpoints.

### After-close S&P 500 scanner

Playbook can run the complete audited engine across the current S&P 500 and publish the largest credible predicted increases:

```powershell
python scan_sp500.py --once
```

Run it after **5:15 PM America/New_York**. The scanner verifies the latest SPY session before it creates a batch, fetches the current Wikipedia constituent table, normalizes provider symbols such as `BRK.B` to `BRK-B`, and caches a last-known-good universe. A live universe that parses fewer than 450 constituents is rejected rather than silently accepted.

For a dedicated always-running scheduler process:

```powershell
python scan_sp500.py --schedule
```

This is intentionally separate from Flask/Waitress so multiple web workers cannot start duplicate jobs. The machine and scheduler process must remain running. For unattended operation, Windows Task Scheduler can run `python scan_sp500.py --once` on weekdays after 5:15 PM ET; weekend/holiday retries safely resolve to the latest confirmed market session.

Each `(market session, algorithm version)` batch is immutable. SQLite stores the exact universe snapshot, per-symbol state, raw ranking factors, failures, runtime, and a bounded cross-process lease. If the process is interrupted, rerunning the command after the lease expires resumes unfinished symbols without recomputing completed ones. A second process cannot acquire an active lease.

The default board admits only setups with all of these properties:

- a **positive** untouched walk-forward audit—not mixed, limited, or weak;
- a bullish analog direction that current news has not cancelled;
- at least a four-point analog probability edge and positive median return;
- evidence score of at least 50 and non-conflicting agreement.

The transparent opportunity score rewards projected median increase, analog edge, independent evidence, agreement, and Brier skill. It subtracts points for the adjusted downside estimate and interval width. News cannot create eligibility because the underlying analog direction must already be bullish.

With three concurrent workers and a warm price cache, a full 503-constituent scan is expected to take roughly 20–60 minutes depending on provider latency and CPU. Use `PLAYBOOK_SCAN_WORKERS` (maximum 8), `PLAYBOOK_SCAN_TIME`, and `PLAYBOOK_DATA_CACHE` to configure operation. Individual provider failures produce an explicit partial board rather than aborting the batch.

## Test

```powershell
python -m unittest discover -s tests -v
```

The 84-test suite covers vectorized shape equivalence, batch/single matching equivalence, persistent refresh and split drift, cross-worker snapshot coherence, immutable forecast grading, completed-session boundaries, dense leak-free audit records, separated conformal calibration, multi-horizon distributions, waterfall arithmetic, Time Machine routes, corrected RSI edge cases, independent episode spacing, bounded news adjustment, intraday path outcomes, global session-date alignment, crypto calendars, base-rate shrinkage, API errors, sentiment behavior, S&P universe validation/fallback, after-close gating, exact-session batch alignment, opportunity eligibility/ranking, claim-owner-safe resumable leases, immutable scans, partial failures, and board APIs. On the development machine, deterministic 20-year pure compute takes roughly 0.05 seconds for the preliminary forecast and 5 seconds for the complete 260-record audit.

## API

```text
GET /api/health
GET /api/analyze/NVDA
GET /api/analyze/NVDA/quick
GET /api/analyze/NVDA/audit?snapshot=<quick-response-token>
GET /api/analyze/NVDA/as-of?date=2024-01-15
GET /api/track-record/NVDA
GET /api/analyze/BTC-USD?refresh=1
GET /api/opportunities/latest
GET /api/opportunities/history

GET /forecast/NVDA
GET /audit/NVDA
GET /opportunities
```

The original endpoint remains backward compatible. The browser requests `/quick` first so the fingerprint, twins, projection, and preliminary forecast paint immediately, then passes its snapshot token to `/audit` for adaptive weight selection, separate interval calibration, and untouched evaluation on the exact same source data. A real progress bar reflects those stages. `/forecast/<symbol>` and `/audit/<symbol>` are durable shareable product routes. `/opportunities` displays the latest completed scan while a newer run progresses, and its read-only APIs use `no-store`; scanner triggering remains CLI-only to prevent public abuse. Forecast responses remain cached in memory for five minutes. Use `?refresh=1` on the original or quick endpoint to create a fresh price snapshot; snapshot-bound audit requests intentionally reject refresh.

## Docker

```powershell
docker build -t playbook .
docker run --rm -p 8000:8000 playbook
```

## Important limitation

Historical analogs estimate a distribution; they do not know the future. Earnings surprises, policy decisions, liquidity shocks, changing businesses, and unprecedented events can invalidate every match. Walk-forward results are symbol-specific and based on a limited number of independent checkpoints. Playbook is a research instrument, not financial advice or a profitability guarantee.
