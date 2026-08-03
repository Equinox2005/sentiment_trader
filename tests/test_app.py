import unittest
from unittest.mock import patch

from app import create_app
from market_data import InvalidSymbolError


class StubService:
    def analyze(self, symbol, force_refresh=False, include_validation=True):
        if symbol == "BAD":
            raise InvalidSymbolError("That symbol is invalid.")
        if include_validation and symbol == "SIG":
            return {
                "symbol": "SIG",
                "name": "Signal Co",
                "quote": {"price": 100.0},
                "history": [{"date": "2025-06-10", "close": 100.0}],
                "playbook": {"available": False, "reason": "Not enough history."},
            }
        return {
            "symbol": symbol.upper(),
            "refreshed": force_refresh,
        }

    def analyze_quick(self, symbol, force_refresh=False):
        return {"symbol": symbol.upper(), "stage": "quick"}

    def analyze_audit(self, symbol, force_refresh=False, snapshot_id=None):
        return {
            "symbol": symbol.upper(),
            "stage": "audit",
            "snapshot_id": snapshot_id,
        }

    def analyze_as_of(self, symbol, as_of_date, force_refresh=False):
        return {
            "symbol": symbol.upper(),
            "stage": "time_machine",
            "as_of": as_of_date,
        }

    def track_record(self, symbol, force_refresh=False):
        return {
            "symbol": symbol.upper(),
            "available": False,
            "records": [],
        }


class StubBoard:
    def latest(self, limit=50, side=None):
        longs = [{"symbol": "AAA", "side": "long"}][:limit]
        shorts = [{"symbol": "ZZZ", "side": "short"}][:limit]
        return {
            "available": True,
            "run": {"session_date": "2025-06-10"},
            "active_run": None,
            "long_count": len(longs),
            "short_count": len(shorts),
            "longs": longs if side in (None, "long") else [],
            "shorts": shorts if side in (None, "short") else [],
            "opportunities": longs,
        }

    def history(self):
        return {"runs": [{"id": 1, "status": "completed"}]}


class StubScorecard:
    def current(self):
        return {
            "as_of_date": "2026-08-01",
            "counts": {
                "total": 1384,
                "pending": 1384,
                "matured": 0,
                "expired_ungraded": 0,
                "scored_matured": 0,
            },
            "headline": {
                "available": False,
                "minimum_sample": 30,
                "reason": (
                    "Headline suppressed until 30 matured long/short "
                    "forecasts are available."
                ),
                "metrics": None,
            },
            "breakdowns": {
                "side": [],
                "tier": [],
                "horizon": [],
                "cohort": [],
            },
            "cohort_start": "2026-07-31",
            "cohort_end": "2026-07-31",
            "data_vintage": "sha256:fixture",
            "limitations": (
                "This is an average per-forecast price move, not a portfolio "
                "return. It excludes costs, position sizing, capital limits, "
                "and overlap between simultaneous forecasts."
            ),
        }


class StubPerformance:
    def __init__(self, available=True):
        self.available = available

    def current(self):
        return {
            "evaluation_date": "2026-08-14",
            "evaluation_session": "2026-08-13",
            "counts": {
                "total": 2801,
                "scored": 1690 if self.available else 0,
                "neutral": 1107,
                "awaiting_entry": 0 if self.available else 2801,
                "unpriced": 0,
                "suspect": 4,
                "matured": 0,
                "open": 1690 if self.available else 0,
            },
            "headline": {
                "available": self.available,
                "reason": (
                    None
                    if self.available
                    else (
                        "No forecast has a tradable entry yet. The first mark "
                        "appears after the next session opens."
                    )
                ),
                "metrics": (
                    {
                        "sample_size": 1690,
                        "average_return": 2.4137,
                        "median_return": 1.8,
                        "standard_deviation": 9.12,
                        "positive_share": 56.4,
                        "best_return": 41.2,
                        "worst_return": -28.9,
                        "benchmark_sample_size": 1690,
                        "average_benchmark_return": 0.91,
                        "average_excess_return": 1.5037,
                    }
                    if self.available
                    else None
                ),
            },
            "progress": {
                "sessions_elapsed": 9,
                "sessions_total": 21,
                "percent_complete": 42.8571,
            },
            "sides": (
                [
                    {
                        "label": "long",
                        "sample_size": 940,
                        "average_return": 3.1,
                        "positive_share": 59.0,
                    }
                ]
                if self.available
                else []
            ),
            "leaderboards": [
                {
                    "key": "long_winners",
                    "label": "Top long returns",
                    "side": "long",
                    "winners": True,
                    "total": 312,
                    "rows": (
                        [
                            {
                                "symbol": "AAA",
                                "side": "long",
                                "tier": "strong",
                                "state": "open",
                                "entry_date": "2026-08-03",
                                "entry_price": 10.0,
                                "mark_date": "2026-08-13",
                                "mark_price": 14.12,
                                "sessions_elapsed": 9,
                                "horizon_days": 21,
                                "price_return": 41.2,
                                "signed_return": 41.2,
                            }
                        ]
                        if self.available
                        else []
                    ),
                },
                {
                    "key": "short_winners",
                    "label": "Top short returns",
                    "side": "short",
                    "winners": True,
                    "total": 0,
                    "rows": [],
                },
                {
                    "key": "long_losers",
                    "label": "Worst long returns",
                    "side": "long",
                    "winners": False,
                    "total": 0,
                    "rows": [],
                },
                {
                    "key": "short_losers",
                    "label": "Worst short returns",
                    "side": "short",
                    "winners": False,
                    "total": 1,
                    "rows": (
                        [
                            {
                                "symbol": "ZZZ",
                                "side": "short",
                                "tier": "moderate",
                                "state": "open",
                                "entry_date": "2026-08-03",
                                "entry_price": 20.0,
                                "mark_date": "2026-08-13",
                                "mark_price": 25.78,
                                "sessions_elapsed": 9,
                                "horizon_days": 21,
                                "price_return": 28.9,
                                "signed_return": -28.9,
                            }
                        ]
                        if self.available
                        else []
                    ),
                },
            ],
            "withheld": [
                {
                    "symbol": "SPLT",
                    "side": "long",
                    "tier": "speculative",
                    "state": "suspect",
                    "entry_date": "2026-08-03",
                    "entry_price": 0.5,
                    "mark_date": "2026-08-13",
                    "mark_price": 20.0,
                    "sessions_elapsed": 9,
                    "horizon_days": 21,
                    "price_return": 3900.0,
                    "signed_return": None,
                }
            ],
            "neutral_sample": 1107,
            "cohort_start": "2026-07-31",
            "cohort_end": "2026-07-31",
            "limitations": (
                "This is the average price move per forecast, equally weighted "
                "and marked to the latest close. It is not a portfolio return: "
                "it excludes trading costs, position sizing, capital limits, "
                "and the overlap between forecasts issued on the same day."
            ),
        }


class AppTests(unittest.TestCase):
    def setUp(self):
        app = create_app(StubService(), board_service=StubBoard())
        # Pinned so a developer's exported token cannot change the expectations.
        app.config.update(TESTING=True, SCAN_TOKEN="")
        self.client = app.test_client()

    def test_health_endpoint(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_analysis_endpoint_returns_service_result(self):
        response = self.client.get("/api/analyze/aapl?refresh=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"symbol": "AAPL", "refreshed": True},
        )
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_public_analysis_endpoints_are_rate_limited(self):
        app = create_app(StubService(), board_service=StubBoard())
        app.config.update(
            TESTING=True,
            SCAN_TOKEN="",
            ANALYSIS_RATE_LIMIT=1,
            REFRESH_RATE_LIMIT=1,
            RATE_LIMIT_WINDOW_SECONDS=60,
        )
        client = app.test_client()

        first = client.get("/api/analyze/AAPL")
        limited = client.get("/api/analyze/MSFT")
        first_refresh = client.get("/api/analyze/AAPL?refresh=1")
        limited_refresh = client.get("/api/analyze/MSFT?refresh=1")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.get_json()["code"], "rate_limited")
        self.assertIn("Retry-After", limited.headers)
        self.assertEqual(first_refresh.status_code, 200)
        self.assertEqual(limited_refresh.status_code, 429)

    def test_rate_limit_uses_client_address_behind_configured_proxy(self):
        with patch.dict(
            "os.environ",
            {"PLAYBOOK_TRUSTED_PROXY_HOPS": "1"},
        ):
            app = create_app(StubService(), board_service=StubBoard())
        app.config.update(
            TESTING=True,
            ANALYSIS_RATE_LIMIT=1,
            RATE_LIMIT_WINDOW_SECONDS=60,
        )
        client = app.test_client()

        first_client = client.get(
            "/api/analyze/AAPL",
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
        second_client = client.get(
            "/api/analyze/MSFT",
            headers={"X-Forwarded-For": "203.0.113.11"},
        )
        limited_second_client = client.get(
            "/api/analyze/GOOG",
            headers={"X-Forwarded-For": "203.0.113.11"},
        )

        self.assertEqual(first_client.status_code, 200)
        self.assertEqual(second_client.status_code, 200)
        self.assertEqual(limited_second_client.status_code, 429)

    def test_scorecard_computation_is_rate_limited(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            scorecard_service=StubScorecard(),
        )
        app.config.update(
            TESTING=True,
            ANALYSIS_RATE_LIMIT=1,
            RATE_LIMIT_WINDOW_SECONDS=60,
        )
        client = app.test_client()

        first = client.get("/api/scorecard")
        limited = client.get("/scorecard")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)

    def test_invalid_symbol_returns_structured_error(self):
        response = self.client.get("/api/analyze/BAD")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_symbol")

    def test_progressive_analysis_endpoints(self):
        quick = self.client.get("/api/analyze/AAPL/quick")
        audit = self.client.get("/api/analyze/AAPL/audit?snapshot=abc")

        self.assertEqual(quick.get_json()["stage"], "quick")
        self.assertEqual(audit.get_json()["stage"], "audit")
        self.assertEqual(audit.get_json()["snapshot_id"], "abc")

    def test_audit_endpoint_requires_snapshot_and_rejects_refresh(self):
        missing = self.client.get("/api/analyze/AAPL/audit")
        refresh = self.client.get(
            "/api/analyze/AAPL/audit?snapshot=abc&refresh=1"
        )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["code"], "missing_snapshot")
        self.assertEqual(refresh.status_code, 400)
        self.assertEqual(refresh.get_json()["code"], "unsupported_refresh")

    def test_time_machine_and_track_record_endpoints(self):
        missing = self.client.get("/api/analyze/AAPL/as-of")
        replay = self.client.get(
            "/api/analyze/AAPL/as-of?date=2024-06-03"
        )
        track = self.client.get("/api/track-record/AAPL")

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.get_json()["code"], "missing_date")
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.get_json()["stage"], "time_machine")
        self.assertEqual(track.status_code, 200)
        self.assertEqual(track.get_json()["symbol"], "AAPL")

    def test_homepage_is_the_signal_board_with_an_inline_checker(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Buy signals", response.data)
        self.assertIn(b"Short signals", response.data)
        self.assertIn(b"Check any ticker", response.data)
        self.assertIn(b"/static/favicon.svg", response.data)
        self.assertIn(b'href="/scorecard"', response.data)

    def test_global_scorecard_is_honest_when_every_forecast_is_pending(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            scorecard_service=StubScorecard(),
        )
        app.config.update(TESTING=True, SCAN_TOKEN="")
        client = app.test_client()

        page = client.get("/scorecard")
        api = client.get("/api/scorecard")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.get_json()["counts"]["pending"], 1384)
        self.assertIn(b'data-headline-available="false"', page.data)
        self.assertIn(b"Headline suppressed until 30 matured", page.data)
        self.assertIn(b"average per-forecast price move", page.data)
        self.assertIn(b"not a portfolio return", page.data)
        self.assertIn(
            b"excludes costs, position sizing, capital limits",
            page.data,
        )
        self.assertNotIn(b'id="headlineMean"', page.data)

    def test_live_return_page_publishes_the_running_average_and_leaderboards(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            scorecard_service=StubScorecard(),
            performance_service=StubPerformance(),
        )
        app.config.update(TESTING=True, SCAN_TOKEN="")
        client = app.test_client()

        page = client.get("/performance")
        api = client.get("/api/performance")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.headers["Cache-Control"], "no-store")
        self.assertAlmostEqual(
            api.get_json()["headline"]["metrics"]["average_return"],
            2.4137,
        )
        self.assertIn(b"+2.41%", page.data)
        self.assertIn(b"Session 9 of 21", page.data)
        self.assertIn(b"Top long returns", page.data)
        self.assertIn(b"Top short returns", page.data)
        self.assertIn(b"Worst long returns", page.data)
        self.assertIn(b"Worst short returns", page.data)
        self.assertIn(b"AAA", page.data)
        self.assertIn(b"ZZZ", page.data)
        self.assertIn(b"-28.90%", page.data)
        self.assertIn(b"not a portfolio return", page.data)
        # The full population is stated whenever the table is truncated.
        self.assertIn(b"Showing 1 of 312", page.data)
        self.assertIn(b"No short forecast is in profit yet.", page.data)

    def test_live_return_page_is_honest_before_the_first_entry(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            scorecard_service=StubScorecard(),
            performance_service=StubPerformance(available=False),
        )
        app.config.update(TESTING=True, SCAN_TOKEN="")
        client = app.test_client()

        page = client.get("/performance")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Not started yet", page.data)
        self.assertIn(b"after the next session opens", page.data)
        self.assertNotIn(b'id="averageReturn"', page.data)

    def test_live_return_page_reports_withheld_split_artifacts(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            scorecard_service=StubScorecard(),
            performance_service=StubPerformance(),
        )
        app.config.update(TESTING=True, SCAN_TOKEN="")

        page = app.test_client().get("/performance")

        self.assertIn(b"Withheld from the average", page.data)
        self.assertIn(b"SPLT", page.data)
        self.assertIn(b"reverse split", page.data)

    def test_live_return_endpoints_report_missing_storage(self):
        app = create_app(StubService(), board_service=StubBoard())
        app.config.update(TESTING=True, SCAN_TOKEN="")
        client = app.test_client()

        page = client.get("/performance")
        api = client.get("/api/performance")

        self.assertEqual(page.status_code, 503)
        self.assertEqual(api.status_code, 503)
        self.assertEqual(api.get_json()["code"], "no_storage")

    def test_live_return_computation_is_rate_limited(self):
        app = create_app(
            StubService(),
            board_service=StubBoard(),
            performance_service=StubPerformance(),
        )
        app.config.update(
            TESTING=True,
            ANALYSIS_RATE_LIMIT=1,
            RATE_LIMIT_WINDOW_SECONDS=60,
        )
        client = app.test_client()

        first = client.get("/api/performance")
        limited = client.get("/performance")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(limited.status_code, 429)

    def test_favicon_asset_is_available(self):
        with self.client.get("/static/favicon.svg") as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "image/svg+xml")


    def test_board_apis_expose_both_sides(self):
        page = self.client.get("/opportunities")
        latest = self.client.get("/api/opportunities/latest?limit=10")
        history = self.client.get("/api/opportunities/history")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(latest.status_code, 200)
        self.assertEqual(latest.get_json()["longs"][0]["symbol"], "AAA")
        self.assertEqual(latest.get_json()["shorts"][0]["symbol"], "ZZZ")
        self.assertEqual(latest.headers["Cache-Control"], "no-store")
        self.assertEqual(history.get_json()["runs"][0]["status"], "completed")

    def test_board_side_filter_and_limit_are_validated(self):
        bad_limit = self.client.get("/api/opportunities/latest?limit=nope")
        bad_side = self.client.get("/api/opportunities/latest?side=sideways")
        shorts_only = self.client.get("/api/opportunities/latest?side=short")

        self.assertEqual(bad_limit.status_code, 400)
        self.assertEqual(bad_limit.get_json()["code"], "invalid_limit")
        self.assertEqual(bad_side.status_code, 400)
        self.assertEqual(bad_side.get_json()["code"], "invalid_side")
        self.assertEqual(shorts_only.get_json()["longs"], [])

    def test_signal_endpoint_scores_one_symbol(self):
        response = self.client.get("/api/signal/SIG")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["symbol"], "SIG")
        self.assertFalse(payload["playbook_available"])
        self.assertFalse(payload["eligible"])
        self.assertEqual(payload["spark"], [100.0])

    def test_remote_scan_trigger_is_disabled_without_a_token(self):
        response = self.client.post("/api/opportunities/run")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "trigger_disabled")

    def test_remote_scan_trigger_rejects_a_wrong_token(self):
        app = create_app(StubService(), board_service=StubBoard())
        app.config.update(TESTING=True, SCAN_TOKEN="secret")
        client = app.test_client()

        response = client.post(
            "/api/opportunities/run",
            headers={"X-Playbook-Scan-Token": "guess"},
        )

        self.assertEqual(response.status_code, 403)

    def test_remote_scan_trigger_rejects_token_in_query_string(self):
        app = create_app(StubService(), board_service=StubBoard())
        app.config.update(TESTING=True, SCAN_TOKEN="secret")
        client = app.test_client()

        response = client.post("/api/opportunities/run?token=secret")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "forbidden")
        self.assertEqual(response.get_json()["code"], "forbidden")

    def test_scan_status_endpoint_reports_idle_state(self):
        response = self.client.get("/api/opportunities/status")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["scan_running"])
        self.assertIsNone(response.get_json()["active_run"])
        self.assertEqual(
            response.get_json()["latest_run"]["session_date"],
            "2025-06-10",
        )

    def test_scan_status_reports_a_persisted_cross_process_run(self):
        class ActiveBoard(StubBoard):
            def latest(self, limit=50, side=None):
                payload = super().latest(limit=limit, side=side)
                payload["active_run"] = {
                    "id": 2,
                    "status": "running",
                    "processed_count": 25,
                    "total_count": 100,
                }
                return payload

        app = create_app(StubService(), board_service=ActiveBoard())
        app.config.update(TESTING=True, SCAN_TOKEN="")

        payload = app.test_client().get("/api/opportunities/status").get_json()

        self.assertTrue(payload["scan_running"])
        self.assertEqual(payload["active_run"]["processed_count"], 25)

    def test_shareable_forecast_and_audit_pages_embed_route_state(self):
        forecast = self.client.get("/forecast/nvda")
        audit = self.client.get("/audit/BTC-USD")
        invalid = self.client.get("/forecast/TOO-LONG-SYMBOL")

        self.assertEqual(forecast.status_code, 200)
        self.assertIn(b'data-initial-symbol="NVDA"', forecast.data)
        self.assertIn(b'data-initial-view="forecast"', forecast.data)
        self.assertEqual(audit.status_code, 200)
        self.assertIn(b'data-initial-symbol="BTC-USD"', audit.data)
        self.assertIn(b'data-initial-view="audit"', audit.data)
        self.assertEqual(invalid.status_code, 404)
        self.assertIn(b'data-route-error="invalid_symbol"', invalid.data)


if __name__ == "__main__":
    unittest.main()
