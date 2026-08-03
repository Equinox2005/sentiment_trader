# Playbook

**Today's clearest buy and short signals from the default covered universe.**

Playbook re-scans Nasdaq-listed common stocks plus current S&P 500 constituents after each close, matches today's setup against up to 20 years of history, replays what actually happened next, and publishes two ranked boards: the strongest **buy** signals and the strongest **short** signals.

The home page is the board. A ticker box sits directly above it, so any symbol — including ETFs, indices, and crypto — can be scored on demand with the identical logic, and every card links to the full historical breakdown.

## The signal board

Each name carries one obvious verdict rather than a wall of numbers:

| Badge | Meaning |
| --- | --- |
| `STRONG BUY` / `STRONG SHORT` | Positive untouched audit, a 6+ point probability edge, strong evidence, agreeing components, no news conflict, and reward/risk of at least 1.0. |
| `BUY` / `SHORT` | A positively or mixed-graded audit with a 4+ point edge, a healthy score, and reward/risk of at least 1.0. |
| `WEAK BUY` / `WEAK SHORT` | A real directional lean whose audit, evidence, or news support is thin. Watchlist material. |
| *(absent)* | The analogs did not lean far enough either way, the typical move was under 1.5%, or risk cancelled the edge. |

Four numbers sit on every card — typical move, odds, risk if wrong, and reward/risk. Everything else (probability edge, evidence, agreement, audit grade, Brier skill, match counts, interval width) is one click away under **Show details**.

The short board is the mirror image of the long board, not an afterthought: expected move, adverse move, and win probability are all read off the opposite tail, so a name with a −9% typical outcome and a +3% adverse tail ranks as a strong short.

### Conviction score

A transparent 0–100 score orders each side. Expected move (30), probability edge (22), evidence (16), audit grade (14), component agreement (10), and audited Brier skill (8) add points. Adverse movement (−14), interval width (−6), and headlines that fight the historical lean (−8) subtract them. Reward/risk is not reweighted into this formula; below 1.0 it acts only as a coherence floor that caps the tier at speculative. Folding reward/risk into the score was tested against the blind replay described below and made results worse in both halves of the sample, so the coherence-floor design stands on measurement rather than taste.

Adverse movement is read from the 20th–80th band, which cannot see downside when it sits entirely on one side of zero. Risk then falls back to a quarter of the band's own spread instead of a token floor, because signals in that state were reporting a median reward/risk of 15.1 while actually dipping 13.0% intraperiod.

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
- hindsight-sealed Time Machine replays and an immutable forward forecast ledger;
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

Missing optional inputs are excluded consistently from both current matching and historical validation. Broad-market context is lagged by one US session so an earlier-closing global market never receives a US close that had not happened yet.

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

Time Machine applies the same engine to a history physically truncated after the selected session; historical news and current earnings metadata are excluded. The realized outcome is calculated only after the forecast payload exists. Forward audited forecasts are stored once per confirmed completed session and graded from a single current adjusted-price vintage after the exact future-session count matures.

## Measured calibration

The per-symbol audit grades each matcher against its own history. It never established whether a published probability means the same thing across the whole market, so a blind replay was run to find out: 220 symbols × 126 non-overlapping 21-session windows, 2016-01-04 to 2026-06-12, 26,347 forecasts, each built on a history and market context physically truncated at its own session and graded afterwards on the production convention of buying the next open.

The raw probability turned out to be monotonic but far too dispersed. Setups carrying a stated 75% chance finished higher about 58% of the time, and the raw number scored *worse* than simply quoting the asset's own base rate. `calibration.py` therefore shrinks the stated edge toward that base rate in logit space, with the factor fitted on the first half of the replay and measured on the untouched second half:

| | Brier | Skill vs base rate |
| --- | ---: | ---: |
| Raw probability | 0.2552 | −1.2% |
| Shrunk toward base rate | 0.2489 | **+1.3%** |
| Base rate alone | 0.2494 | +1.1% |

Two things in that table are worth stating plainly. The fitted factor of 0.132 means roughly seven eighths of the stated edge was noise, and the matcher's contribution over a plain base rate is about 0.2 Brier points. Discrimination is unchanged by this transform — AUC 0.5548 against a coin flip's 0.5000 — because calibration corrects stated confidence, not the ranking itself.

The adjustment appears as an explicit **Measured calibration** step in the probability waterfall rather than being applied silently. `probability_up` and `edge_points` keep their raw values so board ranking, tier gating, and every forecast already written to the ledger stay on one definition; `calibrated_probability_up` is what the interface publishes. `PLAYBOOK_PROBABILITY_SHRINK` overrides the factor, where `1.0` disables calibration and `0.0` quotes the base rate alone.

The same replay found no calibration worth shipping for the magnitude estimate: predicting a flat zero beat both the raw typical move and every fitted rescaling of it. That estimate should be read as an ordinal hint, not a point forecast.

## Run locally

Requires Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements.lock
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

The same SQLite database holds the persistent forecast ledger. Forecast inserts are immutable per symbol/session/horizon, unfinished daily candles are never recorded or graded, and refreshed split/dividend adjustments are applied consistently to both grading endpoints.

### The market-wide scanner

```powershell
python scan_sp500.py --once                        # full Nasdaq + S&P 500
python scan_sp500.py --once --universe sp500       # index only
python scan_sp500.py --once --universe us          # adds NYSE / NYSE American
python scan_sp500.py --once --max-symbols 50       # quick smoke run
```

Run it after **5:15 PM America/New_York**. The scanner confirms the latest SPY session before creating a batch, then builds its universe from the NasdaqTrader symbol directory merged with the Wikipedia S&P 500 table so large NYSE names are not lost. Test issues, ETFs, financially delinquent issues, warrants, rights, units, and preferreds are excluded; symbols such as `BRK.B` are normalized to `BRK-B`. A current Nasdaq directory parsing fewer than 1,500 common stocks is rejected in favour of the last-known-good snapshot.

| Scope | Approximate size |
| --- | --- |
| `sp500` | ~503 |
| `nasdaq` *(default)* | ~3,500–4,000 |
| `us` | ~5,500–6,500 |

Each `(market session, algorithm version)` batch is immutable. SQLite stores the exact universe snapshot, per-symbol state, side, raw ranking factors, failures, runtime, and a bounded cross-process lease. If a run is interrupted, rerunning after the lease expires resumes unfinished symbols without recomputing completed ones, and a second process cannot steal an active lease. New symbol attempts are globally paced across workers. Transient market-data failures receive a three-attempt exponential-backoff budget with jitter; exhausted failures produce an explicit partial board and visible unavailable count rather than an empty board that looks like “no signals.” Detailed scan history is retained for 30 runs by default, and SQLite reuses the freed pages without an expensive nightly `VACUUM`.

A name reaches a board when its analogs lean clearly in one direction, the untouched audit ran, the typical move is at least 1.5%, the share price is at least $5, and risk has not cancelled the edge. Setting `PLAYBOOK_REQUIRE_REWARD_RISK=1` additionally requires reward/risk of at least 1.0 at entry, which lifted the measured win rate from 53.9% to 58.2% but removes roughly 85% of board entries. Audit grade, evidence, agreement, and news conflict then decide **which tier** it lands in rather than silently hiding it — that is what makes the board return dozens of ranked names instead of a handful.

Tune with `PLAYBOOK_SCAN_WORKERS` (maximum 16), `PLAYBOOK_UNIVERSE`, `PLAYBOOK_MAX_SYMBOLS`, `PLAYBOOK_SCAN_TIME`, and `PLAYBOOK_DATA_CACHE`. Rate-limit controls are `PLAYBOOK_SCAN_REQUEST_INTERVAL` (default `0.4` seconds), `PLAYBOOK_SCAN_RETRY_ATTEMPTS` (default `3`), `PLAYBOOK_SCAN_RETRY_BASE_SECONDS` (default `2`), `PLAYBOOK_SCAN_RETRY_MAX_SECONDS` (default `30`), and `PLAYBOOK_SCAN_RETRY_JITTER_SECONDS` (default `1`). `PLAYBOOK_SCAN_RETENTION_RUNS` defaults to `30`.

Measured on the development machine against 40 real S&P 500 symbols on 2026-08-01:

| Workers | Wall time | Seconds/symbol | 3,800-symbol extrapolation |
| ---: | ---: | ---: | ---: |
| 3 | 183.2 s | 4.58 s | 4.83 h |
| 4 | 123.5 s | 3.09 s | 3.26 h |
| 8 | 158.4 s | 3.96 s | 4.18 h |

The cold and warm four-worker runs were both about 2.4 minutes before hardening; caching avoids the historical download but the full walk-forward audit and optional Yahoo calls dominate. Eight workers were slower from oversubscription and peaked at 313.5 MB RSS. The isolated low-cache four-worker scanner peaked at 231.5 MB. These figures are honest local extrapolations, not a guarantee for Yahoo or Render; sustained-run results and provider failures are written to the scan log and database.

A sustained four-worker benchmark then scanned 200 S&P 500 symbols under algorithm version `benchmark-hardened-200-20260801`. It was deliberately stopped at 107/200 and resumed only after its 15-minute lease expired. The resumed process finished the remaining 93 symbols in about 4m 49s; all 107 earlier completion timestamps remained unchanged, proving they were not recomputed. Combined active scan time was about 11m 4s, or 3.32 seconds per symbol, which extrapolates to roughly 3.5 hours for 3,800 symbols on this machine. The final board had 199 analyses, no exhausted provider failures, and one explicit skip (`FDXF`, insufficient history). The scanner peaked at 273 MB RSS while the concurrently responsive development web process had peaked at 104 MB.

The completed sample stored 1,729,202 adjusted bars across 202 cached symbols in a 203.5 MiB database with only 48 KiB free, or about 1.01 MiB of active database space per cached symbol. The file grew 87.3 MiB while the resumed benchmark added 89 newly cached symbols. At the final density, 3,800 symbols require roughly 4.0 GB and 6,000 require roughly 6.3 GB before WAL and operational headroom. A 10 GB disk is appropriate for the Nasdaq scope; use at least 20 GB for the full U.S. scope. Monitor Render’s Disk Usage graph and increase the disk before it reaches 80%. Disk sizes can be increased but not reduced.

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

It provisions a Standard web service on a 10 GB persistent disk mounted at `/var/data`, points `PLAYBOOK_DATA_CACHE` at it, generates a `PLAYBOOK_SCAN_TOKEN`, enables the in-process scheduler, and adds a weekday cron job at 22:30 UTC that calls `trigger_scan.py` as a backstop. The schedule is 18:30 EDT in summer and 17:30 EST in winter, safely after the close and the 17:15 scan gate year-round.

Two constraints shaped this layout:

- Render cron jobs cannot mount disks, so the scan must execute inside the web service rather than in the cron container. The cron job only sends an authenticated HTTP trigger over Render’s private network.
- SQLite wants one writer, so `gunicorn.conf.py` defaults to a single worker with eight threads. Scaling horizontally would require moving storage to Postgres first.

The 512 MB Starter plan is not recommended: the sustained scanner and responsive development web process reached about 377 MB of combined process peaks before operating-system and production-server headroom, and the eight-worker test reached 313.5 MB for the scanner alone. `render.yaml` therefore uses Standard (1 CPU, 2 GB RAM). At current published prices, the realistic baseline is about **$28.50/month** on a Hobby workspace: $25 Standard web service + $2.50 for 10 GB of persistent disk + Render’s $1 minimum cron charge, before excess bandwidth. The cheaper Starter configuration is about $10.50/month but has insufficient safety margin and materially less CPU.

Deployment checklist:

1. Push `main` to a GitHub repository. Never add `.env`, `instance/`, or any `*.sqlite3` file; `.gitignore` already excludes all three.
2. In Render, choose **New → Blueprint**, connect the repository, select `render.yaml`, and apply the two services.
3. Wait for the `playbook` web service health check to pass. `PLAYBOOK_SCAN_TOKEN` is generated by Render; copy it only to a password manager and the two GitHub repository secrets described below.
4. Confirm **Disks** shows `playbook-data` mounted at `/var/data`, and **Environment** shows `PLAYBOOK_DATA_CACHE=/var/data/playbook.sqlite3`.
5. Visit `/api/health`, `/`, and `/api/opportunities/status`. Trigger one scan from the cron service’s **Runs → Trigger Run** control or with the authenticated `curl` command below.
6. After the first completed scan, note the board counts, choose **Manual Deploy → Deploy latest commit** on the web service, and confirm the same session and counts remain. That proves the SQLite file is on the persistent disk rather than the ephemeral source filesystem.

Environment variables:

- `PLAYBOOK_DATA_CACHE`: absolute SQLite path on the mounted disk.
- `PLAYBOOK_UNIVERSE`: `nasdaq`, `sp500`, or `us`.
- `PLAYBOOK_SCAN_WORKERS`: simultaneous audited symbol analyses; four is the measured default.
- `PLAYBOOK_SCAN_TIME`: earliest same-day scan time in America/New_York.
- `PLAYBOOK_ENABLE_SCHEDULER`: enables the in-process weekday scheduler when set to `1`.
- `PLAYBOOK_SCAN_TOKEN`: generated 256-bit trigger secret; never commit or expose it in a URL.
- `PLAYBOOK_SCAN_*RETRY*` and `PLAYBOOK_SCAN_REQUEST_INTERVAL`: Yahoo pacing and retry budget described above.
- `PLAYBOOK_SCAN_RETENTION_RUNS`: number of detailed immutable board runs retained.
- `WEB_CONCURRENCY=1`: preserves SQLite’s single-process ownership model.
- `GUNICORN_THREADS`: concurrent HTTP request capacity inside that worker.
- `PLAYBOOK_TRUSTED_PROXY_HOPS`: trusted ingress hops used to resolve the client address for rate limiting; the Render blueprint sets exactly one.

For GitHub Actions, add repository secrets named `PLAYBOOK_SCAN_URL` (the public `https://…onrender.com` origin) and `PLAYBOOK_SCAN_TOKEN` (the generated Render value). The Render cron uses the private `hostport` reference with `PLAYBOOK_SCAN_SCHEME=http`; public GitHub triggers default to HTTPS.

Manual production verification:

```bash
curl https://<app>/api/health
curl -X POST -H "X-Playbook-Scan-Token: $PLAYBOOK_SCAN_TOKEN" https://<app>/api/opportunities/run
curl https://<app>/api/opportunities/status
```

The first trigger should return `202`; a second while it is running should return `409`. The status endpoint should show the active persisted run and advance its processed count while `/` and `/api/health` remain responsive.

## Test

```powershell
python -m unittest discover -s tests -v
```

The suite covers two-sided ranking and tiering, Nasdaq/NYSE directory parsing and derivative filtering, universe merging and fallback, per-side ranking, board side filters, the single-symbol signal endpoint, scan-trigger authorization, request pacing, exponential retry exhaustion, partial-board warnings, scan-history retention, private Render trigger URLs, vectorized shape equivalence, batch/single matching equivalence, persistent refresh and split drift, cross-worker snapshot coherence, immutable forecast grading, completed-session boundaries, dense leak-free audit records, separated conformal calibration, multi-horizon distributions, waterfall arithmetic, Time Machine routes, corrected RSI edge cases, independent episode spacing, bounded news adjustment, intraday path outcomes, global session-date alignment, crypto calendars, base-rate shrinkage, API errors, sentiment behavior, universe validation/fallback, after-close gating, exact-session batch alignment, opportunity eligibility/ranking, claim-owner-safe resumable leases, immutable scans, partial failures, and board APIs. On the development machine, deterministic 20-year pure compute takes roughly 0.05 seconds for the preliminary forecast and 5 seconds for the complete 260-record audit.

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
GET  /api/scorecard
GET  /api/performance                       # running mark-to-market
POST /api/opportunities/run                 # header: X-Playbook-Scan-Token

GET  /                                      # signal board + ticker checker
GET  /performance                           # live returns, updated daily
GET  /scorecard                             # site-wide forecast ledger
GET  /forecast/NVDA
GET  /audit/NVDA
```

`/api/opportunities/latest` returns `longs` and `shorts` ranked independently, plus `long_count`, `short_count`, and run metadata. The legacy `opportunities` key still mirrors `longs`. `/api/signal/<symbol>` runs the full audited engine and returns the exact payload the board uses, which is what keeps the inline ticker checker consistent with the ranked lists.

The original endpoint remains backward compatible. The detail page requests `/quick` first so the fingerprint, twins, projection, and preliminary forecast paint immediately, then passes its snapshot token to `/audit` for adaptive weight selection, separate interval calibration, and untouched evaluation on the exact same source data. `/forecast/<symbol>` and `/audit/<symbol>` are durable shareable routes.

The board serves the latest completed scan while a newer run progresses, and all read-only board APIs use `no-store`. Scan triggering requires the shared token, so the board cannot be abused into recomputing the market on demand. Forecast responses remain cached in memory for five minutes. Use `?refresh=1` on the original or quick endpoint to create a fresh price snapshot; snapshot-bound audit requests intentionally reject refresh.

## Live returns

`/performance` answers "how is the current cohort actually doing?" while the horizon is still open. The scorecard waits for a forecast to mature; this page marks every open forecast to the latest stored close each trading day, using the same convention the grader settles on — buy the open of the session after the signal, measure to a session close — so the running number converges on the graded one instead of contradicting it. A forecast that has already been graded is read back from the ledger rather than recomputed.

It publishes the average and median return per forecast, the share that are positive, the split by side, and an equal-weight comparator holding every symbol in the ledger over the identical window.

Four leaderboards rank up to 50 names each: best and worst longs, best and worst shorts. A "top returns" table is never padded to a fixed length with losing names, so a short list means the cohort genuinely has that few names in profit. When a table is truncated it states the full population it was drawn from.

Three groups stay out of the average and are reported instead of hidden: neutral forecasts, which assert no direction; positions whose holding window contains a session-to-session move beyond +300% or −75%, which is the signature of a reverse split the cached history has not been adjusted for; and sessions in which fewer than a fifth of tracked symbols traded, so a single crypto ticker's weekend bar cannot advance the board early. A rising withheld count means stale unadjusted price history, not market volatility.

The number is an equal-weight average price move per forecast before costs. It is not a portfolio return and open positions are marked at an unrealised price.

## Docker

```powershell
docker build -t playbook .
docker run --rm -p 8000:8000 playbook
```

## Important limitation

Historical analogs estimate a distribution; they do not know the future. Earnings surprises, policy decisions, liquidity shocks, changing businesses, and unprecedented events can invalidate every match. Walk-forward results are symbol-specific and based on a limited number of independent checkpoints. Playbook is a research instrument, not financial advice or a profitability guarantee.
