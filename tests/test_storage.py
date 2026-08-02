import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pandas as pd

from market_data import (
    _serialize_source,
    MarketIntelligenceService,
    YahooFinanceProvider,
)
from storage import PlaybookStore


def price_frame(start="2024-01-02", periods=12, scale=1.0):
    index = pd.date_range(start, periods=periods, freq="B", tz="UTC")
    close = pd.Series(
        [(100 + step) * scale for step in range(periods)],
        index=index,
    )
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=index,
    )


class FakeTicker:
    def __init__(self, full, incremental=None):
        self.full = full
        self.incremental = incremental if incremental is not None else full.tail(3)
        self.full_calls = 0
        self.incremental_calls = 0

    def history(self, **kwargs):
        if kwargs.get("period") == "max":
            self.full_calls += 1
            return self.full.copy()
        self.incremental_calls += 1
        return self.incremental.copy()


class PriceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlaybookStore(f"{self.temp.name}\\playbook.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_cold_cache_full_fetch_then_incremental_refresh(self):
        ticker = FakeTicker(price_frame())
        provider = YahooFinanceProvider(store=self.store)

        first, first_warnings = provider.history("AAA", ticker)
        second, second_warnings = provider.history("AAA", ticker)

        self.assertEqual(len(first), 12)
        self.assertEqual(len(second), 12)
        self.assertEqual(first_warnings, [])
        self.assertEqual(second_warnings, [])
        self.assertEqual(ticker.full_calls, 1)
        self.assertEqual(ticker.incremental_calls, 1)
        self.assertEqual(len(self.store.load_prices("AAA")), 12)

    def test_force_refresh_replaces_cached_series(self):
        original = price_frame()
        self.store.save_prices("AAA", original, full_refresh=True)
        replacement = price_frame(periods=14, scale=2.0)
        ticker = FakeTicker(replacement)
        provider = YahooFinanceProvider(store=self.store)

        result, _warnings = provider.history("AAA", ticker, force_refresh=True)

        self.assertEqual(ticker.full_calls, 1)
        self.assertEqual(len(result), 14)
        self.assertAlmostEqual(
            self.store.load_prices("AAA")["Close"].iloc[-1],
            replacement["Close"].iloc[-1],
        )

    def test_weekly_refresh_uses_full_history(self):
        old = datetime.now(timezone.utc) - timedelta(days=8)
        self.store.save_prices(
            "AAA", price_frame(), full_refresh=True, now=old
        )
        ticker = FakeTicker(price_frame(periods=13))
        provider = YahooFinanceProvider(store=self.store)

        result, _warnings = provider.history("AAA", ticker)

        self.assertEqual(ticker.full_calls, 1)
        self.assertEqual(ticker.incremental_calls, 0)
        self.assertEqual(len(result), 13)

    def test_adjustment_drift_triggers_full_refetch(self):
        original = price_frame()
        self.store.save_prices("AAA", original, full_refresh=True)
        adjusted = original.copy()
        adjusted["Close"] *= 0.5
        adjusted["Open"] *= 0.5
        adjusted["High"] *= 0.5
        adjusted["Low"] *= 0.5
        ticker = FakeTicker(adjusted, incremental=adjusted.tail(4))
        provider = YahooFinanceProvider(store=self.store)

        result, warnings = provider.history("AAA", ticker)

        self.assertEqual(ticker.incremental_calls, 1)
        self.assertEqual(ticker.full_calls, 1)
        self.assertIn("historical adjustment", " ".join(warnings))
        self.assertAlmostEqual(result["Close"].iloc[-1], adjusted["Close"].iloc[-1])

    def test_drift_on_any_overlap_row_triggers_full_refetch(self):
        original = price_frame()
        self.store.save_prices("AAA", original, full_refresh=True)
        incremental = original.tail(4).copy()
        incremental.iloc[0, incremental.columns.get_loc("Close")] *= 0.5
        adjusted = original.copy()
        adjusted["Close"] *= 0.5
        ticker = FakeTicker(adjusted, incremental=incremental)
        provider = YahooFinanceProvider(store=self.store)

        _result, warnings = provider.history("AAA", ticker)

        self.assertEqual(ticker.full_calls, 1)
        self.assertIn("historical adjustment", " ".join(warnings))

    def test_empty_incremental_response_is_explicitly_stale(self):
        self.store.save_prices("AAA", price_frame(), full_refresh=True)
        ticker = FakeTicker(price_frame(), incremental=pd.DataFrame())
        provider = YahooFinanceProvider(store=self.store)

        result, warnings = provider.history("AAA", ticker)

        self.assertFalse(result.empty)
        self.assertIn("no incremental prices", " ".join(warnings))

    def test_generation_check_rejects_stale_writer(self):
        first = price_frame()
        self.store.save_prices("AAA", first, full_refresh=True)
        generation = self.store.price_meta("AAA")["generation"]
        newer = price_frame(periods=13, scale=2)
        stale = price_frame(periods=14, scale=3)

        accepted = self.store.save_prices(
            "AAA",
            newer,
            full_refresh=True,
            expected_generation=generation,
        )
        rejected = self.store.save_prices(
            "AAA",
            stale,
            full_refresh=True,
            expected_generation=generation,
        )

        self.assertTrue(accepted)
        self.assertFalse(rejected)
        self.assertAlmostEqual(
            self.store.load_prices("AAA")["Close"].iloc[-1],
            newer["Close"].iloc[-1],
        )

    def test_symbols_are_isolated(self):
        self.store.save_prices("AAA", price_frame(scale=1), full_refresh=True)
        self.store.save_prices("BBB", price_frame(scale=2), full_refresh=True)

        aaa = self.store.load_prices("AAA")
        bbb = self.store.load_prices("BBB")

        self.assertNotEqual(aaa["Close"].iloc[-1], bbb["Close"].iloc[-1])
        self.assertEqual(len(aaa), len(bbb))

    def test_source_snapshot_is_shared_between_service_instances(self):
        class BundleProvider:
            def __init__(self, store):
                self.store = store
                self.calls = 0

            def fetch(self, symbol, force_refresh=False):
                self.calls += 1
                return price_frame(periods=900), {}, [], [], None

        first_provider = BundleProvider(self.store)
        second_provider = BundleProvider(self.store)
        first_service = MarketIntelligenceService(first_provider)
        second_service = MarketIntelligenceService(second_provider)

        quick = first_service.analyze_quick("AAA")
        audit = second_service.analyze_audit(
            "AAA", snapshot_id=quick["snapshot_id"]
        )

        self.assertEqual(first_provider.calls, 1)
        self.assertEqual(second_provider.calls, 0)
        self.assertEqual(quick["snapshot_id"], audit["snapshot_id"])

    def test_cross_worker_source_cache_remains_bounded(self):
        class BundleProvider:
            def __init__(self, store):
                self.store = store

        source = (price_frame(), {}, [], [], None)
        tokens = []
        for symbol in ("AAA", "BBB", "CCC"):
            token = symbol.lower()
            tokens.append((token, symbol))
            self.store.save_source_snapshot(
                token,
                symbol,
                _serialize_source(source),
                max_entries=10,
            )
        service = MarketIntelligenceService(
            BundleProvider(self.store),
            max_cache_entries=2,
        )

        for token, symbol in tokens:
            self.assertIsNotNone(service._get_source(token, symbol))

        self.assertEqual(len(service._source_cache), 2)
        self.assertNotIn("aaa", service._source_cache)

    def test_live_forecast_is_immutable_and_graded_by_future_sessions(self):
        history = price_frame(periods=30)
        as_of_position = 10
        as_of_date = history.index[as_of_position].date().isoformat()
        payload = {
            "entry_price": float(history["Close"].iloc[as_of_position]) * 4,
            "probability_up": 62,
            "analog_probability_up": 62,
            "baseline_up_rate": 54,
            "edge_points": 8,
            "direction": "bullish",
            "range": {"low": -3, "typical": 4, "high": 9},
            "evidence_score": 70,
            "validation_grade": "positive",
            "horizon_label": "5 sessions",
            "snapshot_id": "first",
            "exchange_timezone": "UTC",
        }

        first = self.store.save_forecast(
            "AAA",
            as_of_date,
            5,
            "2099-01-01",
            payload,
        )
        changed = dict(payload, probability_up=10, snapshot_id="second")
        second = self.store.save_forecast(
            "AAA",
            as_of_date,
            5,
            "2099-01-01",
            changed,
        )
        graded = self.store.grade_pending_forecasts("AAA", history)
        record = self.store.list_forecasts("AAA")[0]

        expected = (
            history["Close"].iloc[as_of_position + 5]
            / history["Open"].iloc[as_of_position + 1]
            - 1
        ) * 100
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(graded, 1)
        self.assertEqual(record["status"], "graded")
        self.assertEqual(record["probability_up"], 62)
        self.assertAlmostEqual(record["realized_return"], expected)
        self.assertEqual(
            record["outcome_date"],
            history.index[as_of_position + 5].date().isoformat(),
        )

    def test_forecast_grading_uses_next_session_open(self):
        history = price_frame(periods=30)
        signal_position = 10
        as_of_date = history.index[signal_position].date().isoformat()
        payload = {
            "entry_price": None,
            "entry_reference": {"session_offset": 1, "price_field": "Open"},
            "signal_close": float(history["Close"].iloc[signal_position]),
            "probability_up": 62,
            "analog_probability_up": 62,
            "baseline_up_rate": 54,
            "edge_points": 8,
            "direction": "bullish",
            "range": {"low": -3, "typical": 4, "high": 9},
            "evidence_score": 70,
            "validation_grade": "positive",
            "horizon_label": "5 sessions",
            "snapshot_id": "next-open",
            "exchange_timezone": "UTC",
        }
        self.store.save_forecast(
            "AAA",
            as_of_date,
            5,
            history.index[signal_position + 5].date().isoformat(),
            payload,
        )

        graded = self.store.grade_pending_forecasts("AAA", history)
        record = self.store.list_forecasts("AAA")[0]

        expected = (
            history["Close"].iloc[signal_position + 5]
            / history["Open"].iloc[signal_position + 1]
            - 1
        ) * 100
        self.assertEqual(graded, 1)
        self.assertAlmostEqual(record["realized_return"], expected)
        self.assertNotEqual(
            history["Open"].iloc[signal_position + 1],
            history["Close"].iloc[signal_position],
        )

    def test_missing_forecast_entry_session_is_not_approximately_graded(self):
        payload = {
            "entry_price": 100,
            "probability_up": 40,
            "analog_probability_up": 40,
            "baseline_up_rate": 50,
            "edge_points": -10,
            "direction": "bearish",
            "range": {"low": -8, "typical": -3, "high": 4},
            "evidence_score": 60,
            "validation_grade": "mixed",
            "horizon_label": "5 sessions",
            "snapshot_id": "missing",
            "exchange_timezone": "UTC",
        }
        self.store.save_forecast(
            "AAA",
            "2020-01-02",
            5,
            "2024-01-10",
            payload,
        )

        graded = self.store.grade_pending_forecasts("AAA", price_frame())
        record = self.store.list_forecasts("AAA")[0]

        self.assertEqual(graded, 0)
        self.assertEqual(record["status"], "pending")

    def test_forecast_listing_is_unbounded_unless_limit_is_explicit(self):
        payload = {
            "entry_price": 100,
            "probability_up": 50,
            "analog_probability_up": 50,
            "baseline_up_rate": 50,
            "edge_points": 0,
            "direction": "neutral",
            "range": {"low": -2, "typical": 0, "high": 2},
            "evidence_score": 50,
            "validation_grade": "mixed",
            "horizon_label": "5 sessions",
            "snapshot_id": "list",
            "exchange_timezone": "UTC",
        }
        for day in ("02", "03", "04"):
            self.store.save_forecast(
                "BBB",
                f"2024-01-{day}",
                5,
                "2024-01-20",
                payload,
            )

        self.assertEqual(len(self.store.list_forecasts("BBB")), 3)
        self.assertEqual(len(self.store.list_forecasts("BBB", limit=2)), 2)

    def test_in_progress_outcome_session_remains_pending(self):
        history = price_frame(start="2025-01-02", periods=8)
        as_of_date = history.index[0].date().isoformat()
        outcome_date = history.index[5].date().isoformat()
        payload = {
            "entry_price": float(history["Close"].iloc[0]),
            "probability_up": 60,
            "analog_probability_up": 60,
            "baseline_up_rate": 50,
            "edge_points": 10,
            "direction": "bullish",
            "range": {"low": -2, "typical": 3, "high": 8},
            "evidence_score": 70,
            "validation_grade": "positive",
            "horizon_label": "5 sessions",
            "snapshot_id": "open-outcome",
            "exchange_timezone": "UTC",
        }
        self.store.save_forecast(
            "CCC",
            as_of_date,
            5,
            outcome_date,
            payload,
        )

        same_day = datetime.fromisoformat(
            f"{outcome_date}T20:00:00+00:00"
        )
        next_day = same_day + timedelta(days=1)

        self.assertEqual(
            self.store.grade_pending_forecasts(
                "CCC",
                history,
                now=same_day,
            ),
            0,
        )
        self.assertEqual(
            self.store.grade_pending_forecasts(
                "CCC",
                history,
                now=next_day,
            ),
            1,
        )

    def test_schema_initialization_is_safe_across_store_instances(self):
        path = f"{self.temp.name}\\concurrent.sqlite3"
        errors = []

        def initialize():
            try:
                PlaybookStore(path)
            except Exception as exc:
                errors.append(exc)

        workers = [threading.Thread(target=initialize) for _ in range(3)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        self.assertEqual(errors, [])

    def test_forecast_provenance_migration_is_versioned_and_round_trips(self):
        path = f"{self.temp.name}\\pre-migration.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                """
                CREATE TABLE forecasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    horizon_date TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    realized_return REAL,
                    realized_price REAL,
                    outcome_date TEXT,
                    created_at TEXT NOT NULL,
                    graded_at TEXT,
                    UNIQUE(symbol, as_of_date, horizon_days)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE price_cache_meta (
                    symbol TEXT PRIMARY KEY,
                    last_updated_at TEXT NOT NULL,
                    last_full_refresh_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE scan_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    display_symbol TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    sector TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    eligible INTEGER NOT NULL DEFAULT 0,
                    opportunity_score REAL,
                    rank INTEGER,
                    payload_json TEXT,
                    error TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(run_id, symbol)
                )
                """
            )

        migrated = PlaybookStore(path)
        with closing(sqlite3.connect(path)) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(forecasts)")
            }
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            cache_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(price_cache_meta)"
                )
            }
            scan_result_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(scan_results)"
                )
            }

        self.assertTrue(
            {
                "model_version",
                "git_commit",
                "config_hash",
                "data_vintage",
                "universe_id",
            }.issubset(columns)
        )
        self.assertIn(("0001",), versions)
        self.assertIn("generation", cache_columns)
        self.assertTrue({"claim_owner", "side"}.issubset(scan_result_columns))

        migrated.save_forecast(
            "PROV",
            "2025-06-10",
            21,
            "2025-07-09",
            {
                "entry_price": None,
                "entry_reference": {
                    "session_offset": 1,
                    "price_field": "Open",
                },
                "exchange_timezone": "America/New_York",
            },
            model_version="model-v3",
            git_commit="abc123",
            config_hash="sha256:config",
            data_vintage="sha256:data",
            universe_id=17,
            scan_run_id=31,
        )
        record = migrated.list_forecasts("PROV")[0]
        scan_provenance = migrated.forecast_provenance_for_scan(31)

        self.assertEqual(record["model_version"], "model-v3")
        self.assertEqual(record["git_commit"], "abc123")
        self.assertEqual(record["config_hash"], "sha256:config")
        self.assertEqual(record["data_vintage"], "sha256:data")
        self.assertEqual(record["universe_id"], 17)
        self.assertEqual(scan_provenance["model_version"], "model-v3")
        self.assertEqual(scan_provenance["git_commit"], "abc123")
        self.assertEqual(scan_provenance["config_hash"], "sha256:config")

        PlaybookStore(path)
        with closing(sqlite3.connect(path)) as connection:
            applied_count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version = '0001'"
            ).fetchone()[0]
        self.assertEqual(applied_count, 1)


if __name__ == "__main__":
    unittest.main()
