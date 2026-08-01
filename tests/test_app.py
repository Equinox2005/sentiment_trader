import unittest

from app import create_app
from market_data import InvalidSymbolError


class StubService:
    def analyze(self, symbol, force_refresh=False):
        if symbol == "BAD":
            raise InvalidSymbolError("That symbol is invalid.")
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


class AppTests(unittest.TestCase):
    def setUp(self):
        app = create_app(StubService())
        app.config.update(TESTING=True)
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

    def test_homepage_renders_product(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Playbook", response.data)
        self.assertIn(b"receipts", response.data)

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
