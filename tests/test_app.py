import unittest

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
