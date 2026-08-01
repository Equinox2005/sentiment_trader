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
3. **Select the weighting model.** Balanced, price-structure, regime, and participation profiles compete on older walk-forward checkpoints. The lowest-Brier profile wins.
4. **Protect the audit.** Newer checkpoints remain untouched until the chosen profile is evaluated.
5. **Find independent twins.** Weighted feature distance and one-month chart-shape distance rank past dates. Up to 30 matches are selected. Exchange-traded assets use 21-session shapes and 42-session episode spacing; seven-day markets use 30-calendar-day shapes and 60-day spacing. The recent exclusion window prevents a candidate's forward path from overlapping today's fingerprint.
6. **Weight the evidence.** Closer matches contribute more through a stable kernel whose bandwidth targets a healthy effective sample size. Evidence is capped by the number of calendar years represented.
7. **Beat the base rate.** The probability is shrunk toward the asset's own as-of historical up-rate. A bullish or bearish verdict requires an edge over that rate plus a compatible median return—not merely a result above or below 50%.

The **match score** is a distance score from 1–99. It is not a probability.

## Forecasts, news, and risk

The headline horizon is one market month: 21 trading sessions for exchange-traded assets and 30 calendar days for seven-day markets such as crypto. Momentum, volatility annualization, trend windows, shape matching, spacing, projections, and validation all use that same detected sampling calendar. Playbook reports the similarity-weighted 20th percentile, median, and 80th percentile returns plus a Bayesian evidence interval for the chance of finishing higher.

Recent headline sentiment can move the displayed probability by at most five percentage points. News can strengthen, weaken, or cancel an analog edge, but it cannot create one or reverse its direction. Historical article sentiment is not used in matching because the free data source does not provide a reliable historical news archive.

Stops and targets come from each matched path's actual intramonth adverse and favorable excursion—not its month-end return. The plan simulates which level was touched first and includes unresolved paths at their final return. If those paths do not support positive expectancy and defensible reward/risk, Playbook refuses to manufacture a trade.

Known upcoming earnings within seven days are shown as catalyst risk rather than pretended to be an ordinary analog feature.

## Walk-forward reliability

For each historical checkpoint, Playbook:

1. uses only information available on that date;
2. builds scaling parameters from earlier candidate history;
3. excludes candidates whose future outcome was not yet known;
4. runs the same matcher used for today's forecast;
5. compares its probability with the realized 21-day direction.

The UI reports directional accuracy with a 95% Wilson interval, Brier score, stronger-edge coverage, and the accuracy of simply choosing the asset's normal direction. A small or weak validation sample is labeled accordingly; it is not presented as proven accuracy.

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

## Test

```powershell
python -m unittest discover -s tests -v
```

The 56-test suite covers vectorized shape equivalence, batch/single matching equivalence, persistent cache refresh and split drift, cross-worker snapshot coherence and bounds, progressive endpoints, the richer fingerprint, corrected RSI edge cases, independent episode spacing, bounded news adjustment, post-entry intraday path outcomes, extreme base rates, global session-date alignment, crypto calendar windows, base-rate shrinkage, walk-forward reporting, API errors, and sentiment behavior. A deterministic 20-year pure-compute benchmark runs in roughly 0.8 seconds on the development machine.

## API

```text
GET /api/health
GET /api/analyze/NVDA
GET /api/analyze/NVDA/quick
GET /api/analyze/NVDA/audit?snapshot=<quick-response-token>
GET /api/analyze/BTC-USD?refresh=1
```

The original endpoint remains backward compatible. The browser requests `/quick` first so the fingerprint, twins, projection, and preliminary forecast paint immediately, then passes its snapshot token to `/audit` for adaptive weight selection and untouched walk-forward evaluation on the exact same source data. A real progress bar reflects those stages. Responses remain cached in memory for five minutes. Use `?refresh=1` on the original or quick endpoint to create a fresh price snapshot; snapshot-bound audit requests intentionally reject refresh.

## Docker

```powershell
docker build -t playbook .
docker run --rm -p 8000:8000 playbook
```

## Important limitation

Historical analogs estimate a distribution; they do not know the future. Earnings surprises, policy decisions, liquidity shocks, changing businesses, and unprecedented events can invalidate every match. Walk-forward results are symbol-specific and based on a limited number of independent checkpoints. Playbook is a research instrument, not financial advice or a profitability guarantee.
