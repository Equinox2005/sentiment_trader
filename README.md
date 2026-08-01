# Playbook

**Today's clearest buy and short signals, ranked from the whole market.**

Playbook re-scans every US-listed common stock after each close, matches today's setup against up to 20 years of history, replays what actually happened next, and publishes two ranked boards: the strongest **buy** signals and the strongest **short** signals.

The home page is the board. A ticker box sits directly above it, so any symbol — including ETFs, indices, and crypto — can be scored on demand with the identical logic, and every card links to the full historical breakdown.

## The signal board

Each name carries one obvious verdict rather than a wall of numbers:

| Badge | Meaning |
| --- | --- |
| `STRONG BUY` / `STRONG SHORT` | Positive untouched audit, a 6+ point probability edge, strong evidence, agreeing components, and no news conflict. |
| `BUY` / `SHORT` | A positively or mixed-graded audit with a 4+ point edge and a healthy score. |
| `WEAK BUY` / `WEAK SHORT` | A real directional lean whose audit, evidence, or news support is thin. Watchlist material. |
| *(absent)* | The analogs did not lean far enough either way, the typical move was under 1.5%, or risk cancelled the edge. |

Four numbers sit on every card — typical move, odds, risk if wrong, and reward/risk. Everything else (probability edge, evidence, agreement, audit grade, Brier skill, match counts, interval width) is one click away under **Show details**.

The short board is the mirror image of the long board, not an afterthought: expected move, adverse move, and win probability are all read off the opposite tail, so a name with a −9% typical outcome and a +3% adverse tail ranks as a strong short.

### Conviction score

A transparent 0–100 score orders each side. Expected move (30), probability edge (22), evidence (16), audit grade (14), component agreement (10), and audited Brier skill (8) add points. Adverse movement (−14), interval width (−6), and headlines that fight the historical lean (−8) subtract them.

## The forecasting engine

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
- a nightly market-wide board ranking both bullish and bearish audited setups;
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

### The market-wide scanner

```powershell
python scan_sp500.py --once                        # full Nasdaq + S&P 500
python scan_sp500.py --once --universe sp500       # index only
python scan_sp500.py --once --universe us          # adds NYSE / NYSE American
python scan_sp500.py --once --max-symbols 50       # quick smoke run
```

Run it after **5:15 PM America/New_York**. The scanner confirms the latest SPY session before creating a batch, then builds its universe from the NasdaqTrader symbol directory merged with the Wikipedia S&P 500 table so large NYSE names are not lost. Test issues, ETFs, financially delinquent issues, warrants, rights, units, and preferreds are excluded; symbols such as `BRK.B` are normalized to `BRK-B`. A live Nasdaq directory parsing fewer than 1,500 common stocks is rejected in favour of the last-known-good snapshot.

| Scope | Approximate size |
| --- | --- |
| `sp500` | ~503 |
| `nasdaq` *(default)* | ~3,500–4,000 |
| `us` | ~5,500–6,500 |

Each `(market session, algorithm version)` batch is immutable. SQLite stores the exact universe snapshot, per-symbol state, side, raw ranking factors, failures, runtime, and a bounded cross-process lease. If a run is interrupted, rerunning after the lease expires resumes unfinished symbols without recomputing completed ones, and a second process cannot steal an active lease. Individual provider failures produce an explicit partial board rather than aborting the batch.

A name reaches a board when its analogs lean clearly in one direction, the untouched audit ran, the typical move is at least 1.5%, the share price is at least $2, and risk has not cancelled the edge. Audit grade, evidence, agreement, and news conflict then decide **which tier** it lands in rather than silently hiding it — that is what makes the board return dozens of ranked names instead of a handful.

Tune with `PLAYBOOK_SCAN_WORKERS` (maximum 16), `PLAYBOOK_UNIVERSE`, `PLAYBOOK_MAX_SYMBOLS`, `PLAYBOOK_SCAN_TIME`, and `PLAYBOOK_DATA_CACHE`. With four workers and a warm price cache, a ~3,800-symbol scan takes roughly two to five hours depending on provider latency and CPU; the first cold run is slower because every symbol downloads twenty years of history.

## Automatic daily updates

Three ways to run the scan without touching your computer, in order of preference.

**1 · In-process scheduler (default when hosted).** Set `PLAYBOOK_ENABLE_SCHEDULER=1` and the web service starts a daemon thread that runs the scan after each configured close. The SQLite lease makes this safe even if several workers boot: only the process that acquires the lease does work.

**2 · External trigger.** Set `PLAYBOOK_SCAN_TOKEN` to enable `POST /api/opportunities/run`. Any scheduler can then start a scan over HTTP, and the work still executes inside the service that owns the persistent disk:

```powershell
python trigger_scan.py     # reads PLAYBOOK_SCAN_URL and PLAYBOOK_SCAN_TOKEN
```

`.github/workflows/nightly-scan.yml` does exactly this on a free GitHub Actions schedule, which also wakes a sleeping instance. A `409` response means a scan is already in flight and is treated as success.

**3 · Local scheduler process.** `python scan_sp500.py --schedule` stays running and scans after each close. Windows Task Scheduler can instead run `python scan_sp500.py --once` on weekdays after 5:15 PM ET; weekend and holiday retries safely resolve to the latest confirmed session.

`GET /api/opportunities/status` reports whether a scan is running and what the last one returned.

## Deploy to Render

`render.yaml` is a ready blueprint. Push the repo to GitHub, then choose **New → Blueprint** on Render.

It provisions a web service on a 10 GB persistent disk mounted at `/var/data`, points `PLAYBOOK_DATA_CACHE` at it, generates a `PLAYBOOK_SCAN_TOKEN`, enables the in-process scheduler, and adds a weekday cron job at 22:30 UTC that calls `trigger_scan.py` as a backstop.

Two constraints shaped this layout:

- Render cron jobs cannot mount disks, so the scan must execute inside the web service rather than in the cron container. The cron job only sends the trigger.
- SQLite wants one writer, so `gunicorn.conf.py` defaults to a single worker with eight threads. Scaling horizontally would require moving storage to Postgres first.

The `starter` plan (0.5 CPU) completes a full Nasdaq scan overnight. `standard` roughly halves it. On a free instance that sleeps, rely on the GitHub Actions workflow, which pings `/api/health` before triggering.

## Test

```powershell
python -m unittest discover -s tests -v
```

The suite covers two-sided ranking and tiering, Nasdaq/NYSE directory parsing and derivative filtering, universe merging and fallback, per-side ranking, board side filters, the single-symbol signal endpoint, scan-trigger authorization, vectorized shape equivalence, batch/single matching equivalence, persistent refresh and split drift, cross-worker snapshot coherence, immutable forecast grading, completed-session boundaries, dense leak-free audit records, separated conformal calibration, multi-horizon distributions, waterfall arithmetic, Time Machine routes, corrected RSI edge cases, independent episode spacing, bounded news adjustment, intraday path outcomes, global session-date alignment, crypto calendars, base-rate shrinkage, API errors, sentiment behavior, universe validation/fallback, after-close gating, exact-session batch alignment, opportunity eligibility/ranking, claim-owner-safe resumable leases, immutable scans, partial failures, and board APIs. On the development machine, deterministic 20-year pure compute takes roughly 0.05 seconds for the preliminary forecast and 5 seconds for the complete 260-record audit.

## API

```text
GET  /api/health
GET  /api/analyze/NVDA
GET  /api/analyze/NVDA/quick
GET  /api/analyze/NVDA/audit?snapshot=<quick-response-token>
GET  /api/analyze/NVDA/as-of?date=2024-01-15
GET  /api/track-record/NVDA
GET  /api/analyze/BTC-USD?refresh=1
GET  /api/signal/NVDA                       # board verdict for one symbol
GET  /api/opportunities/latest?side=short&limit=50
GET  /api/opportunities/history
GET  /api/opportunities/status
POST /api/opportunities/run                 # header: X-Playbook-Scan-Token

GET  /                                      # signal board + ticker checker
GET  /forecast/NVDA
GET  /audit/NVDA
```

`/api/opportunities/latest` returns `longs` and `shorts` ranked independently, plus `long_count`, `short_count`, and run metadata. The legacy `opportunities` key still mirrors `longs`. `/api/signal/<symbol>` runs the full audited engine and returns the exact payload the board uses, which is what keeps the inline ticker checker consistent with the ranked lists.

The original endpoint remains backward compatible. The detail page requests `/quick` first so the fingerprint, twins, projection, and preliminary forecast paint immediately, then passes its snapshot token to `/audit` for adaptive weight selection, separate interval calibration, and untouched evaluation on the exact same source data. `/forecast/<symbol>` and `/audit/<symbol>` are durable shareable routes.

The board serves the latest completed scan while a newer run progresses, and all read-only board APIs use `no-store`. Scan triggering requires the shared token, so the board cannot be abused into recomputing the market on demand. Forecast responses remain cached in memory for five minutes. Use `?refresh=1` on the original or quick endpoint to create a fresh price snapshot; snapshot-bound audit requests intentionally reject refresh.

## Docker

```powershell
docker build -t playbook .
docker run --rm -p 8000:8000 playbook
```

## Important limitation

Historical analogs estimate a distribution; they do not know the future. Earnings surprises, policy decisions, liquidity shocks, changing businesses, and unprecedented events can invalidate every match. Walk-forward results are symbol-specific and based on a limited number of independent checkpoints. Playbook is a research instrument, not financial advice or a profitability guarantee.
