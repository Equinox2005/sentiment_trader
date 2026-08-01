import math
import unittest
from datetime import datetime, timezone

import pandas as pd

from market_data import (
    InvalidSymbolError,
    MarketIntelligenceService,
    normalize_symbol,
)


def make_history(periods=900, wave=True):
    index = pd.date_range("2022-01-03", periods=periods, freq="B", tz="UTC")
    closes = []
    for step in range(periods):
        base = 100 + step * 0.12
        ripple = 8 * math.sin(step / 17) + 4 * math.sin(step / 5) if wave else 0
        closes.append(base + ripple)
    return pd.DataFrame({"Close": closes}, index=index)


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def fetch(self, symbol):
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
        return history, profile, news, []


class StaleNewsProvider(FakeProvider):
    def fetch(self, symbol):
        history, profile, news, warnings = super().fetch(symbol)
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
        return history, profile, news, warnings


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
        self.assertIn(result["story"]["state"], {"confirms", "conflicts", "neutral"})
        self.assertLessEqual(len(result["history"]), 190)
        self.assertEqual(result["news"][0]["sentiment_label"], "Positive")

    def test_short_history_returns_unavailable_playbook(self):
        class ShortProvider(FakeProvider):
            def fetch(self, symbol):
                self.calls += 1
                return make_history(periods=90), {}, [], []

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
        self.assertNotIn("AAA", service._cache)


if __name__ == "__main__":
    unittest.main()
