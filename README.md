# Playbook

**What happened the last 20 times this stock looked exactly like today?**

Playbook is a no-login research website for everyday traders. Type any stock, ETF, index, or crypto symbol and it scans up to **ten years of price history** for the days that most resemble today's setup — same momentum, same trend position, same volatility, same fear level — then shows you exactly what happened next. No jargon, no wall of numbers. Just a verdict, a plan, and the receipts.

## Why it's different

Most free tools show you *indicators* and leave the interpretation to you. Playbook answers the actual question a trader has:

> "Based on real history, is this more likely to go up or down from here — and what should I do about it?"

- **One plain-English verdict** — "History leans UP", "History leans DOWN", or "History is split" — with a signal-strength gauge and a one-sentence explanation anyone can understand.
- **Ghost paths** — the chart projects faint lines showing what price *actually did* after each similar past setup, plus a shaded "likely range" cone. You literally see the possible futures history suggests.
- **A concrete trade plan** — entry, take-profit, stop-loss, reward-to-risk, and time horizon, all derived from where past look-alike setups actually topped and bottomed. Type an amount and it tells you your exact dollar upside and downside.
- **The receipts** — every match is a real, dated, verifiable moment in that asset's history with its real 1-week / 2-week / 1-month outcomes. Nothing is a black box.
- **A news reality-check** — today's headlines are scored with an explainable finance lexicon and tested against the historical verdict: *confirms*, *conflicts*, or *neutral*.
- **Honest by design** — when history shows no edge, Playbook says so and tells you to wait. When there isn't enough data, it refuses to guess.

## Run locally

Requires Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open [http://localhost:8000](http://localhost:8000). Market data comes from Yahoo Finance via `yfinance` — no API keys needed.

## Test

```powershell
python -m unittest discover -s tests -v
```

The suite runs on deterministic synthetic data with no network access.

## API

```text
GET /api/health
GET /api/analyze/NVDA
GET /api/analyze/BTC-USD?refresh=1
```

The analysis payload contains the quote, the playbook (verdict, odds, trade plan, ghost paths, dated matches), the news check, scored headlines, and chart history. Results are cached in memory for five minutes; `?refresh=1` bypasses the cache.

## Docker

```powershell
docker build -t playbook .
docker run --rm -p 8000:8000 playbook
```

## How the engine works

1. **Fingerprint today.** Six features describe the current setup: 1-month and 1-week momentum, RSI (14), distance from the 50-day trend, 21-day realized volatility, and drawdown from the one-year high.
2. **Scan history.** Every qualifying past day (up to ten years) gets the same fingerprint. Days are ranked by normalized distance to today; matches must be at least two months apart so they represent independent episodes, and recent days can't match themselves.
3. **Measure what followed.** For up to 20 best matches, the engine records the real 5-, 10-, and 21-day forward returns.
4. **Verdict and plan.** Win rate and median outcome set the direction; the stop goes where the weakest quarter of past setups bottomed, the target where the strongest quarter peaked.
5. **News check.** Recent headlines are sentiment-scored and compared against the verdict.

### What it can't do

Playbook sees patterns, not the future. It doesn't know about tomorrow's earnings, Fed meetings, or black-swan events, and past performance never guarantees future results. It is a research tool, not financial advice — the user always makes the call, which is why every brief ships with a stop-loss.

## Project structure

```text
.
├── app.py            # Flask app and API routes
├── playbook.py       # Historical analog engine (the core product)
├── market_data.py    # Yahoo provider, caching, news check, payload assembly
├── sentiment.py      # Explainable financial-language scoring
├── static/           # Frontend logic and styles
├── templates/        # Single-page UI
├── tests/            # 22 deterministic unit/API tests
├── Dockerfile
└── requirements.txt
```
