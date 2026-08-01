import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
