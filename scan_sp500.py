import argparse
import os

from market_data import MarketIntelligenceService, YahooFinanceProvider
from scanner import (
    DEFAULT_SCAN_TIME,
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
    service = MarketIntelligenceService(provider)
    return OpportunityScanner(
        service,
        store,
        workers=args.workers,
        scan_time=args.scan_time,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Playbook's after-close S&P 500 opportunity scanner."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Run one eligible scan.")
    mode.add_argument(
        "--schedule",
        action="store_true",
        help="Stay running and scan after each configured close.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("PLAYBOOK_SCAN_WORKERS", "3")),
        help="Concurrent full analyses (default: 3, maximum: 8).",
    )
    parser.add_argument(
        "--scan-time",
        default=os.getenv("PLAYBOOK_SCAN_TIME", DEFAULT_SCAN_TIME),
        help="Earliest same-day scan time in America/New_York (default: 17:15).",
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
