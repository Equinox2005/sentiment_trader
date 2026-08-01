import math
import threading
import unittest
from datetime import datetime, timezone

import pandas as pd

from market_data import (
    _build_track_record,
    _deserialize_source,
    _session_is_complete,
    _serialize_source,
    InvalidSymbolError,
    InvalidDateError,
    MarketDataError,
    MarketIntelligenceService,
    YahooFinanceProvider,
    normalize_symbol,
)


def make_history(periods=900, wave=True):
    index = pd.date_range("2022-01-03", periods=periods, freq="B", tz="UTC")
    closes = []
    for step in range(periods):
        base = 100 + step * 0.12
        ripple = 8 * math.sin(step / 17) + 4 * math.sin(step / 5) if wave else 0
        closes.append(base + ripple)
    close = pd.Series(closes, index=index)
    open_price = close * (
        1 + pd.Series(
            [0.002 * math.sin(step / 4) for step in range(periods)],
            index=index,
        )
    )
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": pd.concat([open_price, close], axis=1).max(axis=1) * 1.006,
            "Low": pd.concat([open_price, close], axis=1).min(axis=1) * 0.994,
            "Close": close,
            "Volume": [
                1_000_000 + 100_000 * math.sin(step / 9)
                for step in range(periods)
            ],
        },
        index=index,
    )


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def fetch(self, symbol, force_refresh=False):
        self.calls += 1
        history = make_history()
        profile = {
            "longName": f"{symbol} Test Company",
            "exchange": "NMS",
            "sector": "Technology",
            "currency": "USD",
        }
        news = [
            {
                "title": "Company posts strong growth and earnings beat",
                "publisher": "Test Wire",
                "providerPublishTime": int(
                    datetime.now(timezone.utc).timestamp()
                ),
                "link": "https://example.com/positive",
            },
            {
                "content": {
                    "title": "Analyst upgrade follows resilient demand",
                    "provider": {"displayName": "Market Desk"},
                    "pubDate": datetime.now(timezone.utc).isoformat(),
                    "canonicalUrl": {"url": "https://example.com/upgrade"},
                }
            },
        ]
        return history, profile, news, [], self.market_context()

    def market_context(self):
        history = make_history()
        return pd.DataFrame(
            {
                "Market": history["Close"] * 4,
                "VIX": [
                    18 + 4 * math.sin(step / 20)
                    for step in range(len(history))
                ],
            },
            index=history.index,
        )


class StaleNewsProvider(FakeProvider):
    def fetch(self, symbol, force_refresh=False):
        history, profile, news, warnings, context = super().fetch(
            symbol, force_refresh=force_refresh
        )
        news.append(
            {
                "title": "Company posts record growth",
                "publisher": "Archive Wire",
                "providerPublishTime": int(
                    datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
                ),
                "link": "https://example.com/archive",
            }
        )
        return history, profile, news, warnings, context


class MarketIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.provider = FakeProvider()
        self.service = MarketIntelligenceService(self.provider, cache_seconds=60)

    def test_builds_playbook_market_brief(self):
        result = self.service.analyze("$test")

        self.assertEqual(result["symbol"], "TEST")
        self.assertEqual(result["name"], "TEST Test Company")
        self.assertEqual(result["news_summary"]["count"], 2)
        self.assertTrue(result["playbook"]["available"])
        self.assertGreaterEqual(result["playbook"]["stats"]["count"], 5)
        self.assertIn(
            result["playbook"]["verdict"]["direction"],
            {"bullish", "bearish", "neutral"},
        )
        self.assertIn("action", result["playbook"]["trade_plan"])
        self.assertIn("probability_up", result["playbook"]["forecast"])
        self.assertIn("validation", result["playbook"])
        self.assertIn("Market trend", result["playbook"]["matching"]["features_used"])
        self.assertGreater(
            result["playbook"]["forecast"]["news_adjustment_points"],
            0,
        )
        self.assertIn(result["story"]["state"], {"confirms", "conflicts", "neutral"})
        self.assertLessEqual(len(result["history"]), 190)
        self.assertEqual(result["news"][0]["sentiment_label"], "Positive")

    def test_short_history_returns_unavailable_playbook(self):
        class ShortProvider(FakeProvider):
            def fetch(self, symbol, force_refresh=False):
                self.calls += 1
                return make_history(periods=90), {}, [], [], None

        service = MarketIntelligenceService(ShortProvider())
        result = service.analyze("NEW")

        self.assertFalse(result["playbook"]["available"])
        self.assertIn("reason", result["playbook"])

    def test_reuses_recent_cached_result(self):
        first = self.service.analyze("TEST")
        second = self.service.analyze("TEST")

        self.assertEqual(first, second)
        self.assertEqual(self.provider.calls, 1)

    def test_force_refresh_bypasses_cache(self):
        self.service.analyze("TEST")
        self.service.analyze("TEST", force_refresh=True)

        self.assertEqual(self.provider.calls, 2)

    def test_normalizes_supported_symbols(self):
        self.assertEqual(normalize_symbol("$brk.b"), "BRK.B")
        self.assertEqual(normalize_symbol("btc-usd"), "BTC-USD")
        self.assertEqual(normalize_symbol("^gspc"), "^GSPC")

    def test_rejects_invalid_symbol(self):
        with self.assertRaises(InvalidSymbolError):
            normalize_symbol("AAPL; DROP TABLE")

    def test_excludes_stale_headlines_from_analysis(self):
        service = MarketIntelligenceService(StaleNewsProvider())

        result = service.analyze("TEST")

        self.assertEqual(result["news_summary"]["count"], 2)
        self.assertIn("older headline", " ".join(result["warnings"]))

    def test_cache_is_bounded(self):
        service = MarketIntelligenceService(
            self.provider,
            cache_seconds=60,
            max_cache_entries=2,
        )

        service.analyze("AAA")
        service.analyze("BBB")
        service.analyze("CCC")

        self.assertEqual(len(service._cache), 2)
        self.assertNotIn(("AAA", "legacy", "latest"), service._cache)

    def test_quick_analysis_marks_audit_pending(self):
        result = self.service.analyze_quick("TEST")

        self.assertEqual(result["stage"], "quick")
        self.assertTrue(result["playbook"]["preliminary"])
        self.assertTrue(result["playbook"]["validation"]["pending"])

    def test_audit_analysis_returns_full_validation(self):
        quick = self.service.analyze_quick("TEST")
        result = self.service.analyze_audit(
            "TEST", snapshot_id=quick["snapshot_id"]
        )

        self.assertEqual(result["stage"], "audit")
        self.assertFalse(result["playbook"]["preliminary"])

    def test_audit_requires_snapshot_and_rejects_refresh(self):
        with self.assertRaises(MarketDataError):
            self.service.analyze_audit("TEST")

        quick = self.service.analyze_quick("TEST")
        with self.assertRaises(MarketDataError):
            self.service.analyze_audit(
                "TEST",
                force_refresh=True,
                snapshot_id=quick["snapshot_id"],
            )

    def test_cached_audit_still_requires_live_matching_snapshot(self):
        quick = self.service.analyze_quick("TEST")
        token = quick["snapshot_id"]
        self.service.analyze_audit("TEST", snapshot_id=token)
        created_at, symbol, source = self.service._source_cache[token]
        self.service._source_cache[token] = (
            created_at - self.service.source_cache_seconds,
            symbol,
            source,
        )

        with self.assertRaises(MarketDataError):
            self.service.analyze_audit("TEST", snapshot_id=token)

    def test_audit_token_cannot_collide_with_legacy_cache(self):
        self.service.analyze("TEST")

        with self.assertRaises(MarketDataError):
            self.service.analyze_audit("TEST", snapshot_id="latest")

    def test_quick_and_audit_share_one_market_snapshot(self):
        quick = self.service.analyze_quick("TEST")
        audit = self.service.analyze_audit(
            "TEST", snapshot_id=quick["snapshot_id"]
        )

        self.assertEqual(self.provider.calls, 1)
        self.assertEqual(quick["snapshot_id"], audit["snapshot_id"])

    def test_quick_methodology_does_not_claim_completed_audit(self):
        quick = self.service.analyze_quick("TEST")

        self.assertIn("preliminary", quick["methodology"].lower())
        self.assertNotIn("evaluates that profile", quick["methodology"])

    def test_serialized_dst_history_restores_datetime_index(self):
        for timezone_name in ("America/New_York", "Asia/Tokyo"):
            history = make_history(periods=100)
            history.index = pd.date_range(
                "2025-01-02",
                periods=len(history),
                freq="B",
                tz=timezone_name,
            )

            restored = _deserialize_source(
                _serialize_source((history, {}, [], [], None))
            )[0]

            self.assertIsInstance(restored.index, pd.DatetimeIndex)
            self.assertIsNotNone(restored.index.tz)
            self.assertEqual(str(restored.index.tz), timezone_name)
            self.assertEqual(len(restored), len(history))
            self.assertEqual(
                restored.index[-1].date(),
                history.index[-1].date(),
            )

    def test_older_request_cannot_overwrite_newer_cached_result(self):
        cache_key = ("TEST", "full", "latest")
        older = self.service._begin_request(cache_key)
        newer = self.service._begin_request(cache_key)

        self.assertTrue(
            self.service._store_cached(cache_key, {"price": 202}, newer)
        )
        self.assertFalse(
            self.service._store_cached(cache_key, {"price": 101}, older)
        )
        self.assertEqual(
            self.service._get_cached(cache_key),
            {"price": 202},
        )

    def test_time_machine_uses_only_data_available_as_of_session(self):
        history = make_history()
        selected_position = len(history) - 100
        selected_date = history.index[selected_position].date().isoformat()

        result = self.service.analyze_as_of("TEST", selected_date)

        self.assertEqual(result["stage"], "time_machine")
        self.assertEqual(result["as_of"], selected_date)
        self.assertFalse(result["time_machine"]["future_data_used"])
        self.assertTrue(result["time_machine"]["outcome"]["available"])
        self.assertEqual(result["history"][-1]["date"], selected_date)
        self.assertEqual(result["news"], [])

    def test_time_machine_rejects_future_date(self):
        with self.assertRaises(InvalidDateError):
            self.service.analyze_as_of("TEST", "2099-01-01")

    def test_only_prior_calendar_sessions_are_immutable(self):
        now = pd.Timestamp("2025-06-10 20:00:00", tz="UTC")

        self.assertFalse(
            _session_is_complete(
                pd.Timestamp("2025-06-10", tz="UTC"),
                now=now,
            )
        )
        self.assertTrue(
            _session_is_complete(
                pd.Timestamp("2025-06-09", tz="UTC"),
                now=now,
            )
        )
        self.assertFalse(
            _session_is_complete(
                pd.Timestamp("2025-06-10"),
                timezone_name="America/New_York",
                now=pd.Timestamp("2025-06-11 00:30:00", tz="UTC"),
            )
        )

    def test_flat_return_is_not_a_correct_bearish_live_call(self):
        record = {
            "status": "graded",
            "realized_return": 0.0,
            "as_of_date": "2025-01-02",
            "horizon_date": "2025-02-03",
            "outcome_date": "2025-02-03",
            "entry_price": 100,
            "probability_up": 40,
            "baseline_up_rate": 50,
            "edge_points": -10,
            "direction": "bearish",
            "range": {"low": -5, "typical": -2, "high": 3},
            "evidence_score": 65,
            "validation_grade": "mixed",
            "horizon_label": "21 sessions",
        }

        result = _build_track_record("TEST", [record], [])

        self.assertEqual(result["summary"]["directional_accuracy"], 0)
        self.assertFalse(result["records"][0]["direction_correct"])

    def test_yahoo_bundle_fetches_independent_sources_concurrently(self):
        class ConcurrentProvider(YahooFinanceProvider):
            def __init__(self):
                super().__init__()
                self.barrier = threading.Barrier(4)
                self.thread_ids = set()
                self.thread_lock = threading.Lock()

            def _ticker(self, symbol):
                return symbol

            def _arrive(self):
                with self.thread_lock:
                    self.thread_ids.add(threading.get_ident())
                self.barrier.wait(timeout=2)

            def history(self, symbol, ticker, force_refresh=False):
                self._arrive()
                return make_history(), []

            def profile(self, ticker):
                self._arrive()
                return {"currency": "USD"}

            def news(self, ticker):
                self._arrive()
                return []

            def market_context(self):
                self._arrive()
                return None

        provider = ConcurrentProvider()

        history, profile, news, warnings, context = provider.fetch("TEST")

        self.assertFalse(history.empty)
        self.assertEqual(profile["currency"], "USD")
        self.assertEqual(news, [])
        self.assertEqual(warnings, [])
        self.assertIsNone(context)
        self.assertEqual(len(provider.thread_ids), 4)

    def test_optional_provider_failure_does_not_hide_price_result(self):
        class PartialProvider(YahooFinanceProvider):
            def _ticker(self, symbol):
                return symbol

            def history(self, symbol, ticker, force_refresh=False):
                return make_history(), []

            def profile(self, ticker):
                raise MarketDataError("Profile failed explicitly.")

            def news(self, ticker):
                return []

            def market_context(self):
                return None

        result = PartialProvider().fetch("TEST")

        self.assertFalse(result[0].empty)
        self.assertIn("Profile failed explicitly.", result[3])

    def test_partial_profile_failure_returns_explicit_warning(self):
        class PartialTicker:
            fast_info = {"currency": "USD"}

            def get_history_metadata(self):
                return {"exchangeName": "NMS"}

            def get_info(self):
                raise RuntimeError("profile unavailable")

        profile, warnings = YahooFinanceProvider().profile(PartialTicker())

        self.assertEqual(profile["currency"], "USD")
        self.assertIn("sector and earnings", " ".join(warnings))


if __name__ == "__main__":
    unittest.main()
