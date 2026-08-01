import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data import MarketDataError
from scanner import (
    ALGORITHM_VERSION,
    MarketUniverseProvider,
    OpportunityBoardService,
    OpportunityScanner,
    SP500UniverseProvider,
    ScanGateError,
    UniverseError,
    _RequestPacer,
    compact_scan_result,
    confirmed_scan_session,
    merge_universes,
    parse_nasdaq_listed,
    parse_other_listed,
    parse_sp500_constituents,
    rank_analysis,
)
from storage import PlaybookStore


def spy_history(date="2025-06-10"):
    index = pd.DatetimeIndex([pd.Timestamp(date, tz="America/New_York")])
    return pd.DataFrame({"Close": [600.0]}, index=index)


def audited_analysis(
    symbol="AAA",
    grade="positive",
    typical=8.0,
    low=-4.0,
    high=18.0,
    direction="bullish",
    analog_direction=None,
    edge=13,
    probability_up=68,
):
    analog_direction = analog_direction or direction
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
            "verdict": {"direction": direction},
            "forecast": {
                "horizon_days": 21,
                "horizon_label": "21 sessions",
                "direction": direction,
                "analog_direction": analog_direction,
                "probability_up": probability_up,
                "analog_probability_up": 67,
                "baseline_up_rate": 54,
                "edge_points": edge,
                "evidence_score": 82,
                "range_21d": {
                    "low": low,
                    "typical": typical,
                    "high": high,
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


def bearish_analysis(symbol="BEAR", typical=-9.0, low=-20.0, high=3.0):
    return audited_analysis(
        symbol=symbol,
        typical=typical,
        low=low,
        high=high,
        direction="bearish",
        edge=-13,
        probability_up=31,
    )


NASDAQ_HEADER = (
    "Symbol|Security Name|Market Category|Test Issue|Financial Status|"
    "Round Lot Size|ETF|NextShares"
)


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

    def test_parses_nasdaq_common_stocks_and_rejects_derivatives(self):
        rows = [
            f"SYM{index}|Company {index} - Common Stock|Q|N|N|100|N|N"
            for index in range(1500)
        ]
        rows += [
            "ABCDW|Alpha Corp - Warrants|Q|N|N|100|N|N",
            "ABCDU|Alpha Corp - Units|Q|N|N|100|N|N",
            "TESTX|Test Issue - Common Stock|Q|Y|N|100|N|N",
            "FUNDX|Some Index Fund - ETF|Q|N|N|100|Y|N",
            "SICKX|Sick Corp - Common Stock|Q|N|D|100|N|N",
        ]
        document = "\n".join([NASDAQ_HEADER, *rows, "File Creation Time: 0610202517:00"])

        result = parse_nasdaq_listed(document)
        symbols = {item["symbol"] for item in result}

        self.assertEqual(len(result), 1500)
        self.assertNotIn("ABCDW", symbols)
        self.assertNotIn("ABCDU", symbols)
        self.assertNotIn("TESTX", symbols)
        self.assertNotIn("FUNDX", symbols)
        self.assertNotIn("SICKX", symbols)
        self.assertEqual(result[0]["name"], "Company 0")

    def test_rejects_truncated_nasdaq_directory(self):
        document = "\n".join(
            [NASDAQ_HEADER, "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N"]
        )

        with self.assertRaises(UniverseError):
            parse_nasdaq_listed(document)

    def test_other_listed_normalizes_class_share_symbols(self):
        document = "\n".join(
            [
                "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|"
                "Round Lot Size|Test Issue|NASDAQ Symbol",
                "BRK.B|Berkshire Hathaway Inc. Class B|N|BRK B|N|100|N|BRK.B",
            ]
        )

        result = parse_other_listed(document)

        self.assertEqual(result[0]["symbol"], "BRK-B")
        self.assertEqual(result[0]["display_symbol"], "BRK.B")

    def test_merge_prefers_sector_metadata_from_the_index_table(self):
        merged = merge_universes(
            [{"symbol": "AAPL", "name": "Apple", "sector": "", "list": "Nasdaq"}],
            [
                {
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "sector": "Information Technology",
                    "list": "S&P 500",
                }
            ],
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["sector"], "Information Technology")
        self.assertEqual(merged[0]["list"], "S&P 500")

    def test_market_universe_falls_back_to_last_verified_snapshot(self):
        cached = [
            {
                "symbol": f"S{index}",
                "display_symbol": f"S{index}",
                "name": f"Company {index}",
                "sector": "Sector",
            }
            for index in range(1500)
        ]
        self.store.save_scan_universe(cached, "verified")

        def unavailable(*_args, **_kwargs):
            raise OSError("offline")

        result = MarketUniverseProvider(
            self.store,
            scope="nasdaq",
            opener=unavailable,
        ).load()

        self.assertTrue(result["stale"])
        self.assertEqual(len(result["constituents"]), 1500)

    def test_bullish_and_bearish_setups_reach_opposite_boards(self):
        long_signal = rank_analysis(audited_analysis())
        short_signal = rank_analysis(bearish_analysis())

        self.assertEqual(long_signal["side"], "long")
        self.assertEqual(short_signal["side"], "short")
        self.assertTrue(long_signal["eligible"])
        self.assertTrue(short_signal["eligible"])
        self.assertIn("BUY", long_signal["signal"])
        self.assertIn("SHORT", short_signal["signal"])
        # A short's expected move and adverse move are read off the other tail.
        self.assertAlmostEqual(short_signal["expected_move"], 9.0)
        self.assertAlmostEqual(short_signal["adverse_move"], 3.0)

    def test_neutral_analogs_stay_off_both_boards(self):
        neutral = rank_analysis(
            audited_analysis(direction="neutral", analog_direction="neutral")
        )

        self.assertFalse(neutral["eligible"])
        self.assertIsNone(neutral["side"])

    def test_audit_grade_downgrades_the_tier_without_hiding_the_name(self):
        positive = rank_analysis(audited_analysis())
        mixed = rank_analysis(audited_analysis(grade="mixed"))
        weak = rank_analysis(audited_analysis(grade="weak"))

        self.assertEqual(positive["tier"], "strong")
        self.assertEqual(mixed["tier"], "moderate")
        self.assertEqual(weak["tier"], "speculative")
        self.assertTrue(mixed["eligible"])
        self.assertGreater(positive["opportunity_score"], mixed["opportunity_score"])
        self.assertGreater(mixed["opportunity_score"], weak["opportunity_score"])

    def test_news_conflict_is_flagged_and_penalized(self):
        aligned = rank_analysis(audited_analysis())
        conflicted = rank_analysis(
            audited_analysis(direction="neutral", analog_direction="bullish")
        )

        self.assertFalse(aligned["news_conflict"])
        self.assertTrue(conflicted["news_conflict"])
        self.assertNotEqual(conflicted["tier"], "strong")
        self.assertGreater(
            aligned["opportunity_score"],
            conflicted["opportunity_score"],
        )

    def test_downside_penalty_reduces_rank_score(self):
        lower_risk = rank_analysis(audited_analysis(low=-2))
        higher_risk = rank_analysis(audited_analysis(low=-14))

        self.assertGreater(
            lower_risk["opportunity_score"],
            higher_risk["opportunity_score"],
        )

    def test_tiny_expected_move_is_rejected(self):
        result = rank_analysis(audited_analysis(typical=0.6))

        self.assertFalse(result["eligible"])
        self.assertIn("typical historical move", result["reason"])

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
                if symbol == "SHT":
                    return bearish_analysis(symbol)
                return audited_analysis(
                    symbol,
                    typical=12.0 if symbol == "BBB" else 8.0,
                )

        class Universe:
            def load(self, now=None):
                constituents = [
                    {"symbol": "AAA", "name": "A", "sector": "Tech"},
                    {"symbol": "BBB", "name": "B", "sector": "Health"},
                    {"symbol": "SHT", "name": "S", "sector": "Energy"},
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
        self.assertEqual(first["completed_count"], 3)
        self.assertEqual(first["failed_count"], 1)
        self.assertFalse(second["started"])
        self.assertTrue(board["available"])
        self.assertEqual(board["longs"][0]["symbol"], "BBB")
        self.assertEqual(board["longs"][0]["rank"], 1)
        self.assertEqual(board["longs"][1]["symbol"], "AAA")
        self.assertEqual(board["longs"][1]["rank"], 2)
        self.assertEqual(board["long_count"], 2)
        # Shorts are ranked independently, so the best short is also rank 1.
        self.assertEqual(board["shorts"][0]["symbol"], "SHT")
        self.assertEqual(board["shorts"][0]["rank"], 1)
        self.assertEqual(board["short_count"], 1)
        self.assertEqual(board["eligible_count"], 3)

    def test_transient_market_data_failure_retries_with_backoff(self):
        delays = []

        class Provider:
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return spy_history(), []

        class Service:
            provider = Provider()
            attempts = 0

            def analyze(self, symbol, force_refresh=False, include_validation=True):
                self.attempts += 1
                if self.attempts < 3:
                    raise MarketDataError("temporarily throttled")
                return audited_analysis(symbol)

        class Universe:
            def load(inner_self, now=None):
                items = [{"symbol": "AAA", "name": "A", "sector": "Tech"}]
                return {
                    "id": self.store.save_scan_universe(items, "test", now=now),
                    "constituents": items,
                    "warning": None,
                }

        service = Service()
        result = OpportunityScanner(
            service,
            self.store,
            universe_provider=Universe(),
            workers=1,
            retry_attempts=3,
            retry_base_seconds=0.25,
            retry_max_seconds=1,
            sleep=delays.append,
            random_uniform=lambda _start, _end: 0,
        ).run_once(now=datetime(2025, 6, 10, 22, tzinfo=timezone.utc))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(service.attempts, 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_retry_exhaustion_is_visible_on_partial_board(self):
        class Provider:
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return spy_history(), []

        class Service:
            provider = Provider()

            def analyze(self, symbol, force_refresh=False, include_validation=True):
                raise MarketDataError("provider rate limited")

        class Universe:
            def load(inner_self, now=None):
                items = [{"symbol": "BAD", "name": "B", "sector": "Tech"}]
                return {
                    "id": self.store.save_scan_universe(items, "test", now=now),
                    "constituents": items,
                    "warning": None,
                }

        result = OpportunityScanner(
            Service(),
            self.store,
            universe_provider=Universe(),
            workers=1,
            retry_attempts=2,
            sleep=lambda _delay: None,
            random_uniform=lambda _start, _end: 0,
        ).run_once(now=datetime(2025, 6, 10, 22, tzinfo=timezone.utc))
        stored = self.store.get_scan_run(result["id"], include_results=True)

        self.assertEqual(result["status"], "partial")
        self.assertIn("board is partial", result["warnings"][-1])
        self.assertIn("failed after 2 attempts", stored["results"][0]["error"])

    def test_request_pacer_spaces_attempt_starts(self):
        clock = [10.0]
        delays = []

        def sleep(delay):
            delays.append(delay)
            clock[0] += delay

        pacer = _RequestPacer(0.5, sleep=sleep, monotonic=lambda: clock[0])
        pacer.wait()
        pacer.wait()
        pacer.wait()

        self.assertEqual(delays, [0.5, 0.5])

    def test_broken_progress_stream_does_not_fail_completed_scan(self):
        class Provider:
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return spy_history(), []

        class Service:
            provider = Provider()

            def analyze(self, symbol, force_refresh=False, include_validation=True):
                return audited_analysis(symbol)

        class Universe:
            def load(inner_self, now=None):
                items = [{"symbol": "AAA", "name": "A", "sector": "Tech"}]
                return {
                    "id": self.store.save_scan_universe(items, "test", now=now),
                    "constituents": items,
                    "warning": None,
                }

        def broken_progress(_message):
            raise OSError(22, "Invalid argument")

        result = OpportunityScanner(
            Service(),
            self.store,
            universe_provider=Universe(),
            workers=1,
            progress_callback=broken_progress,
            progress_every=1,
        ).run_once(now=datetime(2025, 6, 10, 22, tzinfo=timezone.utc))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completed_count"], 1)

    def test_scan_history_retention_prunes_old_runs(self):
        self.store.scan_retention_runs = 2
        run_ids = []
        for day in (8, 9, 10):
            session = f"2025-06-{day:02d}"
            items = [{"symbol": "AAA", "name": "A", "sector": "Tech"}]
            universe_id = self.store.save_scan_universe(items, "test")
            current = datetime(2025, 6, day, 22, tzinfo=timezone.utc)
            run, acquired = self.store.acquire_scan_run(
                session,
                ALGORITHM_VERSION,
                universe_id,
                items,
                f"owner-{day}",
                now=current,
            )
            self.assertTrue(acquired)
            self.store.claim_scan_symbol(
                run["id"], "AAA", f"owner-{day}", now=current
            )
            self.store.save_scan_result(
                run["id"],
                "AAA",
                "completed",
                f"owner-{day}",
                payload=compact_scan_result(audited_analysis("AAA")),
                now=current,
            )
            self.store.finish_scan_run(run["id"], f"owner-{day}", now=current)
            run_ids.append(run["id"])

        self.assertIsNone(self.store.get_scan_run(run_ids[0]))
        self.assertIsNotNone(self.store.get_scan_run(run_ids[1]))
        self.assertIsNotNone(self.store.get_scan_run(run_ids[2]))
        self.assertEqual(len(self.store.list_scan_runs(limit=10)), 2)

    def test_board_can_be_restricted_to_one_side(self):
        universe_id = self.store.save_scan_universe(
            [{"symbol": "AAA", "name": "A", "sector": "Tech"}], "test"
        )
        now = datetime(2025, 6, 10, 22, tzinfo=timezone.utc)
        run, _ = self.store.acquire_scan_run(
            "2025-06-10",
            ALGORITHM_VERSION,
            universe_id,
            [{"symbol": "AAA", "name": "A", "sector": "Tech"}],
            "owner",
            now=now,
        )
        self.store.claim_scan_symbol(run["id"], "AAA", "owner", now=now)
        self.store.save_scan_result(
            run["id"],
            "AAA",
            "completed",
            "owner",
            payload=compact_scan_result(audited_analysis("AAA")),
            now=now,
        )
        self.store.finish_scan_run(run["id"], "owner", now=now)

        board = OpportunityBoardService(self.store)

        self.assertEqual(len(board.latest(side="long")["longs"]), 1)
        self.assertEqual(board.latest(side="short")["longs"], [])
        self.assertEqual(board.latest(side="short")["long_count"], 1)

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
        self.assertEqual(result["side"], "long")
        self.assertEqual(result["validation_grade"], "positive")
        self.assertEqual(
            result["ranking_factors"]["predicted_increase"],
            8.0,
        )
        self.assertEqual(result["ranking_factors"]["adverse_move"], 4.0)


if __name__ == "__main__":
    unittest.main()
