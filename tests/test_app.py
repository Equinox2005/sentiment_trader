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

    def test_homepage_renders_product(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Playbook", response.data)
        self.assertIn(b"receipts", response.data)


if __name__ == "__main__":
    unittest.main()
