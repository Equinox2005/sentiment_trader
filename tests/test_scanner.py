import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd

from market_data import MarketDataError
from scanner import (
    ALGORITHM_VERSION,
    OpportunityBoardService,
    OpportunityScanner,
    SP500UniverseProvider,
    ScanGateError,
    UniverseError,
    compact_scan_result,
    confirmed_scan_session,
    parse_sp500_constituents,
    rank_analysis,
)
from storage import PlaybookStore


def spy_history(date="2025-06-10"):
    index = pd.DatetimeIndex([pd.Timestamp(date, tz="America/New_York")])
    return pd.DataFrame({"Close": [600.0]}, index=index)


def audited_analysis(symbol="AAA", grade="positive", typical=8.0, low=-4.0):
    return {
        "symbol": symbol,
        "name": f"{symbol} Company",
        "sector": "Technology",
        "currency": "USD",
        "quote": {"price": 125.0},
        "history": [{"date": "2025-06-10", "close": 125.0}],
        "playbook": {
            "available": True,
            "matching": {"match_count": 30},
            "stats": {"effective_matches": 14.5, "distinct_years": 10},
            "verdict": {"direction": "bullish"},
            "forecast": {
                "horizon_days": 21,
                "horizon_label": "21 sessions",
                "direction": "bullish",
                "analog_direction": "bullish",
                "probability_up": 68,
                "analog_probability_up": 67,
                "baseline_up_rate": 54,
                "edge_points": 13,
                "evidence_score": 82,
                "range_21d": {
                    "low": low,
                    "typical": typical,
                    "high": 18.0,
                },
                "agreement": {"score": 88, "label": "Broad agreement"},
            },
            "validation": {
                "available": True,
                "grade": grade,
                "label": "Historically useful",
                "sample_size": 180,
                "accuracy": 61,
                "baseline_accuracy": 53,
                "brier_skill": 12.5,
            },
        },
    }


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlaybookStore(f"{self.temp.name}\\playbook.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_parses_and_normalizes_verified_universe(self):
        rows = "".join(
            f"<tr><td>{'BRK.B' if index == 0 else f'S{index}'}</td>"
            f"<td>Company {index}</td><td>Sector</td></tr>"
            for index in range(450)
        )
        document = (
            '<table id="constituents"><tr><th>Symbol</th>'
            "<th>Security</th><th>GICS Sector</th></tr>"
            f"{rows}</table>"
        )

        result = parse_sp500_constituents(document)

        self.assertEqual(len(result), 450)
        self.assertEqual(result[0]["display_symbol"], "BRK.B")
        self.assertEqual(result[0]["symbol"], "BRK-B")

    def test_rejects_partial_universe(self):
        document = (
            '<table id="constituents"><tr><th>Symbol</th>'
            "<th>Security</th><th>GICS Sector</th></tr>"
            "<tr><td>AAA</td><td>Company</td><td>Sector</td></tr></table>"
        )

        with self.assertRaises(UniverseError):
            parse_sp500_constituents(document)

    def test_universe_provider_uses_last_verified_snapshot(self):
        cached = [
            {
                "symbol": f"S{index}",
                "display_symbol": f"S{index}",
                "name": f"Company {index}",
                "sector": "Sector",
            }
            for index in range(450)
        ]
        self.store.save_scan_universe(cached, "verified")

        def unavailable(*_args, **_kwargs):
            raise OSError("offline")

        result = SP500UniverseProvider(
            self.store,
            opener=unavailable,
        ).load()

        self.assertTrue(result["stale"])
        self.assertEqual(len(result["constituents"]), 450)
        self.assertIn("last verified", result["warning"])

    def test_same_day_scan_requires_configured_after_close_time(self):
        before = datetime(2025, 6, 10, 20, 30, tzinfo=timezone.utc)
        after = datetime(2025, 6, 10, 22, 0, tzinfo=timezone.utc)

        with self.assertRaises(ScanGateError):
            confirmed_scan_session(spy_history(), now=before)
        self.assertEqual(
            confirmed_scan_session(spy_history(), now=after),
            "2025-06-10",
        )

    def test_only_positive_audit_is_eligible(self):
        positive = rank_analysis(audited_analysis())
        mixed = rank_analysis(audited_analysis(grade="mixed"))

        self.assertTrue(positive["eligible"])
        self.assertFalse(mixed["eligible"])
        self.assertIn("not positively graded", mixed["reason"])

    def test_downside_penalty_reduces_rank_score(self):
        lower_risk = rank_analysis(audited_analysis(low=-2))
        higher_risk = rank_analysis(audited_analysis(low=-14))

        self.assertGreater(
            lower_risk["opportunity_score"],
            higher_risk["opportunity_score"],
        )

    def test_scan_run_is_immutable_and_expired_lease_resumes(self):
        universe_id = self.store.save_scan_universe(
            [{"symbol": "AAA", "name": "A", "sector": "Tech"}],
            "test",
        )
        started = datetime(2025, 6, 10, 22, tzinfo=timezone.utc)
        first, acquired = self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            [{"symbol": "AAA", "name": "A", "sector": "Tech"}],
            "first",
            lease_seconds=60,
            now=started,
        )
        active = self.store.active_scan_run(
            now=started + timedelta(seconds=30)
        )
        expired = self.store.active_scan_run(
            now=started + timedelta(seconds=61)
        )
        blocked, blocked_acquire = self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            [],
            "second",
            lease_seconds=60,
            now=started + timedelta(seconds=30),
        )
        resumed, resumed_acquire = self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            [],
            "second",
            lease_seconds=60,
            now=started + timedelta(seconds=61),
        )

        self.assertTrue(acquired)
        self.assertEqual(active["id"], first["id"])
        self.assertIsNone(expired)
        self.assertFalse(blocked_acquire)
        self.assertEqual(blocked["id"], first["id"])
        self.assertTrue(resumed_acquire)
        self.assertEqual(resumed["id"], first["id"])

    def test_expired_owner_cannot_overwrite_resumed_claim(self):
        constituents = [{"symbol": "AAA", "name": "A", "sector": "Tech"}]
        universe_id = self.store.save_scan_universe(constituents, "test")
        started = datetime(2025, 6, 10, 22, tzinfo=timezone.utc)
        run, _ = self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            constituents,
            "first",
            lease_seconds=60,
            now=started,
        )
        self.assertTrue(
            self.store.claim_scan_symbol(
                run["id"], "AAA", "first", now=started
            )
        )
        self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            constituents,
            "second",
            lease_seconds=60,
            now=started + timedelta(seconds=61),
        )
        self.assertTrue(
            self.store.claim_scan_symbol(
                run["id"],
                "AAA",
                "second",
                now=started + timedelta(seconds=61),
            )
        )

        stale = self.store.save_scan_result(
            run["id"],
            "AAA",
            "completed",
            "first",
            payload={"eligible": False},
            now=started + timedelta(seconds=62),
        )
        current = self.store.save_scan_result(
            run["id"],
            "AAA",
            "completed",
            "second",
            payload={"eligible": False},
            now=started + timedelta(seconds=62),
        )

        self.assertFalse(stale)
        self.assertTrue(current)

    def test_scanner_completes_partial_run_and_reuses_it(self):
        class Provider:
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return spy_history(), []

        class Service:
            provider = Provider()

            def analyze(self, symbol, force_refresh=False, include_validation=True):
                if symbol == "BAD":
                    raise MarketDataError("missing")
                return audited_analysis(
                    symbol,
                    typical=12.0 if symbol == "BBB" else 8.0,
                )

        class Universe:
            def load(self, now=None):
                constituents = [
                    {"symbol": "AAA", "name": "A", "sector": "Tech"},
                    {"symbol": "BBB", "name": "B", "sector": "Health"},
                    {"symbol": "BAD", "name": "B", "sector": "Tech"},
                ]
                universe_id = self_store.save_scan_universe(
                    constituents, "test", now=now
                )
                return {
                    "id": universe_id,
                    "constituents": constituents,
                    "source": "test",
                    "source_timestamp": None,
                    "fetched_at": now.isoformat(),
                    "stale": False,
                    "warning": None,
                }

        self_store = self.store
        scanner = OpportunityScanner(
            Service(),
            self.store,
            universe_provider=Universe(),
            workers=2,
        )
        now = datetime(2025, 6, 10, 22, tzinfo=timezone.utc)

        first = scanner.run_once(now=now)
        second = scanner.run_once(now=now + timedelta(minutes=1))
        board = OpportunityBoardService(self.store).latest()

        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["completed_count"], 2)
        self.assertEqual(first["failed_count"], 1)
        self.assertFalse(second["started"])
        self.assertTrue(board["available"])
        self.assertEqual(board["opportunities"][0]["symbol"], "BBB")
        self.assertEqual(board["opportunities"][0]["rank"], 1)
        self.assertEqual(board["opportunities"][1]["symbol"], "AAA")
        self.assertEqual(board["opportunities"][1]["rank"], 2)
        self.assertEqual(board["eligible_count"], 2)

    def test_scanner_rejects_symbol_from_wrong_price_session(self):
        class Provider:
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return spy_history(), []

        class Service:
            provider = Provider()

            def analyze(self, symbol, force_refresh=False, include_validation=True):
                result = audited_analysis(symbol)
                result["history"][-1]["date"] = "2025-06-09"
                return result

        class Universe:
            def load(inner_self, now=None):
                items = [{"symbol": "AAA", "name": "A", "sector": "Tech"}]
                return {
                    "id": self.store.save_scan_universe(items, "test", now=now),
                    "constituents": items,
                    "warning": None,
                }

        result = OpportunityScanner(
            Service(),
            self.store,
            universe_provider=Universe(),
        ).run_once(now=datetime(2025, 6, 10, 22, tzinfo=timezone.utc))

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_count"], 1)
        stored = self.store.get_scan_run(result["id"], include_results=True)
        self.assertIn("not the confirmed", stored["results"][0]["error"])

    def test_compact_result_keeps_reproducible_ranking_factors(self):
        result = compact_scan_result(audited_analysis())

        self.assertTrue(result["eligible"])
        self.assertEqual(result["validation_grade"], "positive")
        self.assertEqual(
            result["ranking_factors"]["predicted_increase"],
            8.0,
        )


if __name__ == "__main__":
    unittest.main()
