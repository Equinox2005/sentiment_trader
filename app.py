import hmac
import math
import os
import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from market_data import (
    InvalidDateError,
    InvalidSymbolError,
    MarketDataError,
    MarketIntelligenceService,
    YahooFinanceProvider,
    normalize_symbol,
)
from performance import LivePerformanceService
from scorecard import ScorecardService
from scanner import (
    OpportunityBoardService,
    ScanGateError,
    UniverseError,
    build_default_scanner,
    compact_scan_result,
    run_scheduler,
)
from storage import PlaybookStore


_scan_lock = threading.Lock()
_scan_state = {"running": False, "last_result": None, "last_error": None}
_ANALYSIS_ENDPOINTS = {
    "analyze",
    "analyze_quick",
    "analyze_audit",
    "analyze_as_of",
    "signal",
    "track_record",
    "scorecard_page",
    "scorecard_api",
    "performance_page",
    "performance_api",
}


class _SlidingWindowLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key, limit, window_seconds):
        limit = max(1, int(limit))
        window_seconds = max(1.0, float(window_seconds))
        current = self._clock()
        cutoff = current - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, math.ceil(events[0] + window_seconds - current))
                return False, retry_after
            events.append(current)
        return True, 0


def _run_scan_in_background(store):
    """Run one full market scan without blocking the request thread."""

    with _scan_lock:
        if _scan_state["running"] or store.active_scan_run() is not None:
            return False
        _scan_state["running"] = True

    def worker():
        try:
            # Keep the multi-hour scan's short-lived analysis cache separate
            # from the interactive ticker checker.
            scanner = build_default_scanner(store)
            result = scanner.run_once()
            _scan_state["last_result"] = {
                "session_date": result.get("session_date"),
                "status": result.get("status"),
                "completed_count": result.get("completed_count"),
                "failed_count": result.get("failed_count"),
                "skipped_count": result.get("skipped_count"),
                "started": result.get("started"),
            }
            _scan_state["last_error"] = None
        except (ScanGateError, UniverseError, MarketDataError) as exc:
            _scan_state["last_error"] = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            _scan_state["last_error"] = f"Unexpected scan failure: {exc}"
        finally:
            with _scan_lock:
                _scan_state["running"] = False

    threading.Thread(target=worker, name="playbook-scan", daemon=True).start()
    return True


def create_app(
    service=None,
    board_service=None,
    scorecard_service=None,
    performance_service=None,
):
    app = Flask(__name__, instance_relative_config=True)
    trusted_proxy_hops = max(
        0,
        int(os.getenv("PLAYBOOK_TRUSTED_PROXY_HOPS", "0")),
    )
    if trusted_proxy_hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=trusted_proxy_hops)
    app.config["JSON_SORT_KEYS"] = False
    app.config["ANALYSIS_RATE_LIMIT"] = int(
        os.getenv("PLAYBOOK_ANALYSIS_RATE_LIMIT", "60")
    )
    app.config["REFRESH_RATE_LIMIT"] = int(
        os.getenv("PLAYBOOK_REFRESH_RATE_LIMIT", "6")
    )
    app.config["RATE_LIMIT_WINDOW_SECONDS"] = int(
        os.getenv("PLAYBOOK_RATE_LIMIT_WINDOW_SECONDS", "60")
    )
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
    store = app.extensions.get("playbook_store") or getattr(
        getattr(service, "provider", None),
        "store",
        None,
    )
    if board_service is None:
        board_service = OpportunityBoardService(store) if store else None
    app.extensions["opportunity_board"] = board_service
    if scorecard_service is None:
        scorecard_service = ScorecardService(store) if store else None
    app.extensions["scorecard"] = scorecard_service
    if performance_service is None:
        performance_service = LivePerformanceService(store) if store else None
    app.extensions["live_performance"] = performance_service
    app.config["SCAN_TOKEN"] = os.getenv("PLAYBOOK_SCAN_TOKEN", "")
    app.extensions["analysis_rate_limiter"] = _SlidingWindowLimiter()

    @app.before_request
    def limit_public_analysis():
        if request.endpoint not in _ANALYSIS_ENDPOINTS:
            return None
        refresh = request.args.get("refresh") == "1"
        bucket = "refresh" if refresh else "analysis"
        limit = app.config[
            "REFRESH_RATE_LIMIT" if refresh else "ANALYSIS_RATE_LIMIT"
        ]
        client = request.remote_addr or "unknown"
        allowed, retry_after = app.extensions["analysis_rate_limiter"].allow(
            (client, bucket),
            limit,
            app.config["RATE_LIMIT_WINDOW_SECONDS"],
        )
        if allowed:
            return None
        response = jsonify(
            {
                "error": "Too many analysis requests. Please try again later.",
                "code": "rate_limited",
            }
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    if os.getenv("PLAYBOOK_ENABLE_SCHEDULER") == "1":
        _start_embedded_scheduler(app)

    @app.get("/")
    def index():
        return render_template("board.html")

    @app.get("/opportunities")
    def opportunities_alias():
        return render_template("board.html")

    @app.get("/scorecard")
    def scorecard_page():
        scorecard_service = app.extensions["scorecard"]
        if scorecard_service is None:
            return render_template("scorecard.html", report=None), 503
        return render_template(
            "scorecard.html",
            report=scorecard_service.current(),
        )

    @app.get("/api/scorecard")
    def scorecard_api():
        scorecard_service = app.extensions["scorecard"]
        if scorecard_service is None:
            return jsonify(
                {
                    "error": "Persistent scorecard storage is not configured.",
                    "code": "no_storage",
                }
            ), 503
        response = jsonify(scorecard_service.current())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/performance")
    def performance_page():
        performance_service = app.extensions["live_performance"]
        if performance_service is None:
            return render_template("performance.html", report=None), 503
        return render_template(
            "performance.html",
            report=performance_service.current(),
        )

    @app.get("/api/performance")
    def performance_api():
        performance_service = app.extensions["live_performance"]
        if performance_service is None:
            return jsonify(
                {
                    "error": "Persistent forecast storage is not configured.",
                    "code": "no_storage",
                }
            ), 503
        response = jsonify(performance_service.current())
        response.headers["Cache-Control"] = "no-store"
        return response

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

    @app.get("/api/opportunities/latest")
    def opportunities_latest():
        board = app.extensions["opportunity_board"]
        if board is None:
            return jsonify(
                {
                    "available": False,
                    "message": "Persistent opportunity storage is not configured.",
                    "longs": [],
                    "shorts": [],
                    "opportunities": [],
                }
            )
        limit = request.args.get("limit", "50")
        try:
            limit_value = max(1, min(250, int(limit)))
        except ValueError:
            return jsonify(
                {"error": "Limit must be an integer.", "code": "invalid_limit"}
            ), 400
        side = request.args.get("side") or None
        if side not in (None, "long", "short"):
            return jsonify(
                {"error": "Side must be long or short.", "code": "invalid_side"}
            ), 400
        response = jsonify(board.latest(limit=limit_value, side=side))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/opportunities/history")
    def opportunities_history():
        board = app.extensions["opportunity_board"]
        if board is None:
            return jsonify({"runs": []})
        response = jsonify(board.history())
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/opportunities/status")
    def opportunities_status():
        board = app.extensions["opportunity_board"]
        board_status = board.latest(limit=1) if board is not None else {}
        active_run = board_status.get("active_run")
        response = jsonify(
            {
                "scan_running": _scan_state["running"] or bool(active_run),
                "active_run": active_run,
                "latest_run": board_status.get("run"),
                "last_result": _scan_state["last_result"],
                "last_error": _scan_state["last_error"],
                "trigger_enabled": bool(app.config["SCAN_TOKEN"]),
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/opportunities/run")
    def opportunities_run():
        """Token-protected trigger so an external scheduler can start a scan."""

        expected = app.config["SCAN_TOKEN"]
        if not expected:
            return (
                jsonify(
                    {
                        "error": (
                            "Remote scan triggering is disabled. Set "
                            "PLAYBOOK_SCAN_TOKEN to enable it."
                        ),
                        "code": "trigger_disabled",
                    }
                ),
                503,
            )
        supplied = (
            request.headers.get("X-Playbook-Scan-Token")
            or ""
        )
        if not hmac.compare_digest(supplied, expected):
            return jsonify({"error": "Invalid scan token.", "code": "forbidden"}), 403
        store = app.extensions.get("playbook_store")
        if store is None:
            return (
                jsonify(
                    {
                        "error": "Persistent storage is not configured.",
                        "code": "no_storage",
                    }
                ),
                503,
            )
        started = _run_scan_in_background(store)
        return (
            jsonify(
                {
                    "started": started,
                    "message": (
                        "The market scan is running in the background."
                        if started
                        else "A scan is already running."
                    ),
                }
            ),
            202 if started else 409,
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

    @app.get("/api/signal/<symbol>")
    def signal(symbol):
        """Score one symbol with the exact board logic, for the inline checker."""

        refresh = request.args.get("refresh") == "1"

        def scored(value, force_refresh=False):
            analysis = app.extensions["market_service"].analyze(
                value,
                force_refresh=force_refresh,
                include_validation=True,
            )
            history = analysis.get("history") or []
            return {
                "symbol": analysis["symbol"],
                "display_symbol": analysis["symbol"],
                # Fallbacks the compact payload omits when no forecast exists.
                "name": analysis.get("name") or analysis["symbol"],
                "sector": analysis.get("sector", ""),
                "currency": analysis.get("currency", "USD"),
                "as_of": history[-1]["date"] if history else None,
                "quote": analysis.get("quote", {}),
                "history_years": analysis.get("history_years"),
                "spark": [point["close"] for point in history[-140:]],
                "warnings": analysis.get("warnings", []),
                "playbook_available": bool(
                    analysis.get("playbook", {}).get("available")
                ),
                **compact_scan_result(analysis),
            }

        return _analysis_response(scored, symbol, refresh)

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
        return render_template("board.html"), 404

    return app


def _start_embedded_scheduler(app):
    """Run the daily scan inside the web process.

    The SQLite lease makes this safe even when several workers boot: only the
    process that acquires the lease performs work, and the rest defer.
    """

    store = app.extensions.get("playbook_store")
    if store is None:
        return
    def worker():
        scanner = build_default_scanner(store)
        run_scheduler(scanner)

    threading.Thread(
        target=worker,
        name="playbook-scheduler",
        daemon=True,
    ).start()


if __name__ == "__main__":
    app = create_app()
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("FLASK_DEBUG") == "1",
    )
