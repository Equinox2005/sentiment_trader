"""Refresh prices for open forecasts and grade the ones that have matured.

The nightly scan does two unrelated jobs: it builds tomorrow's board, which
costs a full audited analysis per symbol, and it grades yesterday's forecasts,
which costs two prices. Only the first is expensive, so measuring how the model
is doing does not require rebuilding the board.

This does the cheap half alone. It updates the cached price series for every
symbol carrying an open forecast, grades whatever has reached its horizon, and
prints the scorecard and live-performance headlines.

    python grade_forecasts.py
    python grade_forecasts.py --workers 8 --quiet

Nothing here writes a forecast, so running it can never change what the board
predicted. It is safe to run as often as you like.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from market_data import YahooFinanceProvider
from performance import LivePerformanceService
from scanner import _RequestPacer
from scorecard import ScorecardService
from storage import PlaybookStore

DEFAULT_WORKERS = 6
DEFAULT_REQUEST_INTERVAL = 0.25


def open_store():
    """Same resolution the scanner and web app use, so all three agree."""
    path = os.getenv(
        "PLAYBOOK_DATA_CACHE",
        os.path.join(os.path.dirname(__file__), "instance", "playbook.sqlite3"),
    )
    return PlaybookStore(path)


def pending_symbols(store):
    """Symbols holding at least one ungraded forecast."""
    return sorted(
        {
            record["symbol"]
            for record in store.list_all_forecasts()
            if record["status"] == "pending"
        }
    )


def refresh_and_grade(symbol, provider, store, pacer):
    pacer.wait()
    history, warnings = provider.history(symbol, provider._ticker(symbol))
    if history is None or history.empty:
        return symbol, 0, warnings, "no price history returned"
    graded = store.grade_pending_forecasts(symbol, history)
    return symbol, graded, warnings, None


def run(workers, request_interval, verbose=True):
    store = open_store()
    provider = YahooFinanceProvider(store=store)
    pacer = _RequestPacer(request_interval)

    symbols = pending_symbols(store)
    if not symbols:
        print("No open forecasts. Nothing to grade.", flush=True)
        return 0

    print(
        f"Grading {len(symbols)} symbols with open forecasts "
        f"({workers} workers, {request_interval}s pacing)",
        flush=True,
    )

    started = time.time()
    graded_total = 0
    failures = []
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(refresh_and_grade, symbol, provider, store, pacer): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            symbol = futures[future]
            completed += 1
            try:
                _symbol, graded, _warnings, problem = future.result()
            except Exception as exc:  # noqa: BLE001
                failures.append((symbol, f"{type(exc).__name__}: {exc}"))
                continue
            if problem:
                failures.append((symbol, problem))
                continue
            graded_total += graded
            if verbose and graded:
                print(f"  {symbol}: graded {graded}", flush=True)
            if verbose and completed % 250 == 0:
                elapsed = time.time() - started
                rate = completed / max(elapsed, 1e-9)
                remaining = (len(symbols) - completed) / max(rate, 1e-9)
                print(
                    f"  [{completed}/{len(symbols)}] {elapsed / 60:.1f}m elapsed, "
                    f"{remaining / 60:.1f}m remaining",
                    flush=True,
                )

    elapsed = time.time() - started
    print(
        f"\nRefreshed {len(symbols) - len(failures)} symbols in {elapsed / 60:.1f}m, "
        f"graded {graded_total} forecasts, {len(failures)} failures",
        flush=True,
    )
    for symbol, reason in failures[:10]:
        print(f"  failed: {symbol} — {reason}", flush=True)
    if len(failures) > 10:
        print(f"  ... and {len(failures) - 10} more", flush=True)

    report_progress(store)
    return 0


def report_progress(store):
    live = LivePerformanceService(store).current()
    counts = live["counts"]
    print("\nOPEN FORECASTS")
    print(
        f"  total {counts['total']}  marked {counts.get('scored', 0)}  "
        f"awaiting entry {counts.get('awaiting_entry', 0)}  "
        f"matured {counts.get('matured', 0)}"
    )
    headline = live.get("headline") or {}
    if headline.get("available"):
        metrics = headline["metrics"]
        print(
            f"  running average {metrics.get('average_return')}%  "
            f"median {metrics.get('median_return')}%  "
            f"positive {metrics.get('positive_share')}%"
        )
    else:
        print(f"  {headline.get('reason', 'No running average yet.')}")

    card = ScorecardService(store).current()
    print("\nSETTLED SCORECARD")
    print(
        f"  matured {card['counts']['matured']}  "
        f"scored {card['counts']['scored_matured']}  "
        f"pending {card['counts']['pending']}"
    )
    card_headline = card.get("headline") or {}
    if card_headline.get("available"):
        metrics = card_headline["metrics"]
        print(
            f"  average return {metrics.get('mean_signal_return')}%  "
            f"hit rate {metrics.get('hit_rate')}%  "
            f"n={metrics.get('sample_size')}"
        )
    else:
        print(f"  {card_headline.get('reason', 'Headline not available yet.')}")

    calibration = card.get("calibration") or {}
    print("\nCALIBRATION (forward evidence, cannot be overfitted)")
    if calibration.get("available"):
        print(
            f"  n={calibration['sample']}  realized up-rate "
            f"{calibration['realized_up_rate']}%"
        )
        print(
            f"  Brier published {calibration['brier_published']} vs base rate "
            f"{calibration['brier_base_rate']}  "
            f"skill {calibration['skill_published_points']} points"
        )
        for row in calibration.get("buckets", []):
            print(
                f"    {row['bucket']:>7}  n={row['count']:<5} predicted "
                f"{row['predicted_up']:>5}%  realized {row['realized_up']:>5}%  "
                f"gap {row['gap_points']:+.1f}"
            )
    else:
        print(f"  {calibration.get('reason', 'Not available yet.')}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=DEFAULT_REQUEST_INTERVAL,
        help="Seconds between symbol fetches, shared across workers.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return run(
        max(1, args.workers),
        max(0.0, args.request_interval),
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    sys.exit(main())
