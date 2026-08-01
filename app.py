import os

from flask import Flask, jsonify, render_template, request

from market_data import (
    InvalidDateError,
    InvalidSymbolError,
    MarketDataError,
    MarketIntelligenceService,
    YahooFinanceProvider,
    normalize_symbol,
)
from storage import PlaybookStore


def create_app(service=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config["JSON_SORT_KEYS"] = False
    if service is None:
        os.makedirs(app.instance_path, exist_ok=True)
        store_path = os.getenv(
            "PLAYBOOK_DATA_CACHE",
            os.path.join(app.instance_path, "playbook.sqlite3"),
        )
        store = PlaybookStore(store_path)
        service = MarketIntelligenceService(
            YahooFinanceProvider(store=store)
        )
        app.extensions["playbook_store"] = store
    app.extensions["market_service"] = service

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            initial_symbol="",
            initial_view="forecast",
            route_error="",
        )

    @app.get("/forecast/<symbol>")
    def forecast_page(symbol):
        try:
            initial_symbol = normalize_symbol(symbol)
        except InvalidSymbolError:
            return render_template(
                "index.html",
                initial_symbol="",
                initial_view="forecast",
                route_error="invalid_symbol",
            ), 404
        return render_template(
            "index.html",
            initial_symbol=initial_symbol,
            initial_view="forecast",
            route_error="",
        )

    @app.get("/audit/<symbol>")
    def audit_page(symbol):
        try:
            initial_symbol = normalize_symbol(symbol)
        except InvalidSymbolError:
            return render_template(
                "index.html",
                initial_symbol="",
                initial_view="audit",
                route_error="invalid_symbol",
            ), 404
        return render_template(
            "index.html",
            initial_symbol=initial_symbol,
            initial_view="audit",
            route_error="",
        )

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "playbook"})

    @app.get("/api/analyze/<symbol>")
    def analyze(symbol):
        refresh = request.args.get("refresh") == "1"
        return _analysis_response(
            app.extensions["market_service"].analyze,
            symbol,
            refresh,
        )

    @app.get("/api/analyze/<symbol>/quick")
    def analyze_quick(symbol):
        refresh = request.args.get("refresh") == "1"
        return _analysis_response(
            app.extensions["market_service"].analyze_quick,
            symbol,
            refresh,
        )

    @app.get("/api/analyze/<symbol>/audit")
    def analyze_audit(symbol):
        refresh = request.args.get("refresh") == "1"
        snapshot_id = request.args.get("snapshot")
        if not snapshot_id:
            return (
                jsonify(
                    {
                        "error": (
                            "Run the quick forecast first and pass its snapshot token."
                        ),
                        "code": "missing_snapshot",
                    }
                ),
                400,
            )
        if refresh:
            return (
                jsonify(
                    {
                        "error": (
                            "Refresh the quick forecast first, then audit its new snapshot."
                        ),
                        "code": "unsupported_refresh",
                    }
                ),
                400,
            )
        return _analysis_response(
            lambda value, force_refresh=False: app.extensions[
                "market_service"
            ].analyze_audit(
                value,
                force_refresh=force_refresh,
                snapshot_id=snapshot_id,
            ),
            symbol,
            refresh,
        )

    @app.get("/api/analyze/<symbol>/as-of")
    def analyze_as_of(symbol):
        refresh = request.args.get("refresh") == "1"
        as_of_date = request.args.get("date")
        if not as_of_date:
            return (
                jsonify(
                    {
                        "error": "Choose a Time Machine date.",
                        "code": "missing_date",
                    }
                ),
                400,
            )
        return _analysis_response(
            lambda value, force_refresh=False: app.extensions[
                "market_service"
            ].analyze_as_of(
                value,
                as_of_date=as_of_date,
                force_refresh=force_refresh,
            ),
            symbol,
            refresh,
        )

    @app.get("/api/track-record/<symbol>")
    def track_record(symbol):
        refresh = request.args.get("refresh") == "1"
        return _analysis_response(
            app.extensions["market_service"].track_record,
            symbol,
            refresh,
        )

    def _analysis_response(method, symbol, refresh):
        try:
            result = method(symbol, force_refresh=refresh)
        except InvalidDateError as exc:
            return jsonify({"error": str(exc), "code": "invalid_date"}), 400
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
        response.headers["Cache-Control"] = (
            "no-store" if refresh else "private, max-age=60"
        )
        return response

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "API endpoint not found", "code": "not_found"}), 404
        return render_template(
            "index.html",
            initial_symbol="",
            initial_view="forecast",
            route_error="not_found",
        ), 404

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
