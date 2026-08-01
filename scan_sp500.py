"""Run Playbook's after-close market scan.

Kept at this filename for backward compatibility; the universe is configurable
and defaults to the full Nasdaq common-stock listing merged with the S&P 500.
"""

import argparse
import os

from market_data import MarketIntelligenceService, YahooFinanceProvider
from scanner import (
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BASE_SECONDS,
    DEFAULT_RETRY_JITTER_SECONDS,
    DEFAULT_RETRY_MAX_SECONDS,
    DEFAULT_SCAN_TIME,
    DEFAULT_UNIVERSE_SCOPE,
    MarketUniverseProvider,
    OpportunityScanner,
    ScanGateError,
    UniverseError,
    run_scheduler,
)
from storage import PlaybookStore


def build_scanner(args):
    path = os.getenv(
        "PLAYBOOK_DATA_CACHE",
        os.path.join(os.path.dirname(__file__), "instance", "playbook.sqlite3"),
    )
    store = PlaybookStore(path)
    provider = YahooFinanceProvider(store=store)
    workers = max(1, min(16, int(args.workers)))
    service = MarketIntelligenceService(
        provider,
        cache_seconds=0,
        max_cache_entries=max(4, workers * 2),
        source_cache_seconds=0,
    )
    return OpportunityScanner(
        service,
        store,
        universe_provider=MarketUniverseProvider(
            store,
            scope=args.universe,
            max_symbols=args.max_symbols,
        ),
        workers=workers,
        scan_time=args.scan_time,
        request_interval_seconds=getattr(
            args,
            "request_interval",
            DEFAULT_REQUEST_INTERVAL_SECONDS,
        ),
        retry_attempts=getattr(args, "retry_attempts", DEFAULT_RETRY_ATTEMPTS),
        retry_base_seconds=getattr(
            args,
            "retry_base_seconds",
            DEFAULT_RETRY_BASE_SECONDS,
        ),
        retry_max_seconds=getattr(
            args,
            "retry_max_seconds",
            DEFAULT_RETRY_MAX_SECONDS,
        ),
        retry_jitter_seconds=getattr(
            args,
            "retry_jitter_seconds",
            DEFAULT_RETRY_JITTER_SECONDS,
        ),
        progress_callback=lambda message: print(message, flush=True),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Playbook's after-close market opportunity scanner."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run one eligible scan.")
    mode.add_argument(
        "--schedule",
        action="store_true",
        help="Stay running and scan after each configured close.",
    )
    parser.add_argument(
        "--universe",
        choices=("nasdaq", "us", "sp500"),
        default=os.getenv("PLAYBOOK_UNIVERSE", DEFAULT_UNIVERSE_SCOPE),
        help=(
            "nasdaq: every Nasdaq common stock plus the S&P 500 (default). "
            "us: adds NYSE and NYSE American. sp500: the index only."
        ),
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=int(os.getenv("PLAYBOOK_MAX_SYMBOLS") or 0) or None,
        help="Cap the universe size, useful for a first smoke run.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("PLAYBOOK_SCAN_WORKERS", "3")),
        help="Concurrent full analyses (default: 3, maximum: 16).",
    )
    parser.add_argument(
        "--scan-time",
        default=os.getenv("PLAYBOOK_SCAN_TIME", DEFAULT_SCAN_TIME),
        help="Earliest same-day scan time in America/New_York (default: 17:15).",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=float(
            os.getenv(
                "PLAYBOOK_SCAN_REQUEST_INTERVAL",
                str(DEFAULT_REQUEST_INTERVAL_SECONDS),
            )
        ),
        help="Minimum seconds between new symbol attempts across all workers.",
    )
    parser.add_argument(
        "--retry-attempts",
        type=int,
        default=int(
            os.getenv("PLAYBOOK_SCAN_RETRY_ATTEMPTS", str(DEFAULT_RETRY_ATTEMPTS))
        ),
        help="Total attempts for transient market-data failures.",
    )
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_BASE_SECONDS",
                str(DEFAULT_RETRY_BASE_SECONDS),
            )
        ),
        help="Initial exponential-backoff delay.",
    )
    parser.add_argument(
        "--retry-max-seconds",
        type=float,
        default=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_MAX_SECONDS",
                str(DEFAULT_RETRY_MAX_SECONDS),
            )
        ),
        help="Maximum exponential-backoff delay.",
    )
    parser.add_argument(
        "--retry-jitter-seconds",
        type=float,
        default=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_JITTER_SECONDS",
                str(DEFAULT_RETRY_JITTER_SECONDS),
            )
        ),
        help="Random jitter added to each retry delay.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force full price refreshes. Usually unnecessary.",
    )
    args = parser.parse_args(argv)
    scanner = build_scanner(args)
    try:
        if args.schedule:
            print(
                f"Scheduler active; scans begin after {args.scan_time} "
                "America/New_York. Press Ctrl+C to stop.",
                flush=True,
            )
            run_scheduler(scanner)
            return 0
        result = scanner.run_once(force_refresh=args.refresh)
        print(
            f"Scan {result['status']} for {result['session_date']}: "
            f"{result['completed_count']} completed, "
            f"{result['failed_count']} failed, "
            f"{result['skipped_count']} skipped.",
            flush=True,
        )
        return 0
    except (ScanGateError, UniverseError) as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        print("Scanner stopped. The expired lease can be resumed safely.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
