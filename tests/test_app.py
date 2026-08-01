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

    def test_invalid_symbol_returns_structured_error(self):
        response = self.client.get("/api/analyze/BAD")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_symbol")

    def test_homepage_renders_product(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Playbook", response.data)
        self.assertIn(b"receipts", response.data)


if __name__ == "__main__":
    unittest.main()
