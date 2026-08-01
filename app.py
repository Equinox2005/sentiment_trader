import os

from flask import Flask, jsonify, render_template, request

from market_data import (
    InvalidSymbolError,
    MarketDataError,
    MarketIntelligenceService,
    YahooFinanceProvider,
)


def create_app(service=None):
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False
    app.extensions["market_service"] = service or MarketIntelligenceService(
        YahooFinanceProvider()
    )

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "divergence"})

    @app.get("/api/analyze/<symbol>")
    def analyze(symbol):
        refresh = request.args.get("refresh") == "1"
        try:
            result = app.extensions["market_service"].analyze(
                symbol, force_refresh=refresh
            )
        except InvalidSymbolError as exc:
            return jsonify({"error": str(exc), "code": "invalid_symbol"}), 400
        except MarketDataError as exc:
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "code": "market_data_unavailable",
                    }
                ),
                502,
            )

        response = jsonify(result)
        response.headers["Cache-Control"] = "private, max-age=60"
        return response

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "API endpoint not found", "code": "not_found"}), 404
        return render_template("index.html"), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
