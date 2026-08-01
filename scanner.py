import html
import math
import os
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as clock_time, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import MarketDataError


ALGORITHM_VERSION = "sp500-opportunity-v1"
UNIVERSE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
MIN_UNIVERSE_SIZE = 450
DEFAULT_SCAN_TIME = "17:15"


class UniverseError(RuntimeError):
    pass


class ScanGateError(RuntimeError):
    pass


class _ConstituentTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_table = False
        self.table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell_parts = []
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "constituents":
            self.in_table = True
            self.table_depth = 1
            return
        if not self.in_table:
            return
        if tag == "table":
            self.table_depth += 1
        elif tag == "tr":
            self.in_row = True
            self.row = []
        elif tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.cell_parts = []

    def handle_endtag(self, tag):
        if not self.in_table:
            return
        if tag in {"td", "th"} and self.in_cell:
            value = html.unescape("".join(self.cell_parts))
            self.row.append(" ".join(value.split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
        elif tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_table = False

    def handle_data(self, data):
        if self.in_table and self.in_cell:
            self.cell_parts.append(data)


class SP500UniverseProvider:
    def __init__(
        self,
        store,
        url=UNIVERSE_URL,
        timeout_seconds=20,
        opener=None,
    ):
        self.store = store
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urlopen

    def load(self, now=None):
        warning = None
        try:
            request = Request(
                self.url,
                headers={"User-Agent": "Playbook opportunity scanner/1.0"},
            )
            with self.opener(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                source_timestamp = response.headers.get("Last-Modified")
            constituents = parse_sp500_constituents(body)
            universe_id = self.store.save_scan_universe(
                constituents,
                source=self.url,
                source_timestamp=source_timestamp,
                now=now,
            )
            return {
                "id": universe_id,
                "constituents": constituents,
                "source": self.url,
                "source_timestamp": source_timestamp,
                "fetched_at": _iso(now),
                "stale": False,
                "warning": None,
            }
        except (OSError, TimeoutError, UnicodeError, UniverseError) as exc:
            warning = (
                "The live S&P 500 constituent list was unavailable; "
                "the last verified universe snapshot is in use."
            )
            cached = self.store.latest_scan_universe()
            if cached is None:
                raise UniverseError(
                    "The S&P 500 universe could not be verified and no "
                    "last-known-good snapshot exists."
                ) from exc
            return {
                **cached,
                "stale": True,
                "warning": warning,
            }


def parse_sp500_constituents(document):
    parser = _ConstituentTableParser()
    parser.feed(document)
    if not parser.rows:
        raise UniverseError("The S&P 500 constituent table was not found.")
    headers = parser.rows[0]
    try:
        symbol_index = headers.index("Symbol")
        name_index = headers.index("Security")
        sector_index = headers.index("GICS Sector")
    except ValueError as exc:
        raise UniverseError(
            "The S&P 500 constituent table format changed."
        ) from exc
    constituents = []
    seen = set()
    for row in parser.rows[1:]:
        if len(row) <= max(symbol_index, name_index, sector_index):
            continue
        display_symbol = row[symbol_index].strip().upper()
        symbol = display_symbol.replace(".", "-")
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        constituents.append(
            {
                "symbol": symbol,
                "display_symbol": display_symbol,
                "name": row[name_index].strip(),
                "sector": row[sector_index].strip(),
            }
        )
    if len(constituents) < MIN_UNIVERSE_SIZE:
        raise UniverseError(
            f"Only {len(constituents)} constituents were parsed; "
            "the universe was rejected as incomplete."
        )
    return constituents


def confirmed_scan_session(history, now=None, scan_time=DEFAULT_SCAN_TIME):
    if history is None or history.empty or "Close" not in history:
        raise ScanGateError("SPY prices are unavailable for session confirmation.")
    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if close.empty:
        raise ScanGateError("SPY prices are unavailable for session confirmation.")
    current = _utc(now).astimezone(ZoneInfo("America/New_York"))
    latest = pd.Timestamp(close.index[-1]).date()
    if latest > current.date():
        raise ScanGateError("The provider returned a future market session.")
    configured = _parse_scan_time(scan_time)
    if latest == current.date() and current.time().replace(tzinfo=None) < configured:
        raise ScanGateError(
            f"The {latest.isoformat()} regular session is not confirmed complete. "
            f"Run at or after {scan_time} America/New_York."
        )
    if (current.date() - latest).days > 4:
        raise ScanGateError(
            f"The latest SPY session ({latest.isoformat()}) is stale."
        )
    return latest.isoformat()


def rank_analysis(analysis):
    play = analysis.get("playbook", {})
    if not play.get("available"):
        return {
            "eligible": False,
            "reason": play.get("reason", "No analog forecast was available."),
        }
    forecast = play["forecast"]
    validation = play.get("validation", {})
    agreement = forecast.get("agreement", {})
    range_data = forecast["range_21d"]
    typical = float(range_data["typical"])
    downside = max(0.0, -float(range_data["low"]))
    width = max(0.0, float(range_data["high"]) - float(range_data["low"]))
    edge = float(forecast["edge_points"])
    evidence = float(forecast["evidence_score"])
    agreement_score = float(agreement.get("score", 0))
    brier_skill = float(validation.get("brier_skill", -100))
    reasons = []
    checks = (
        (validation.get("available"), "The untouched audit is unavailable."),
        (
            validation.get("grade") == "positive",
            "The untouched audit is not positively graded.",
        ),
        (
            forecast.get("analog_direction") == "bullish",
            "Historical analogs do not show a bullish edge.",
        ),
        (
            forecast.get("direction") == "bullish",
            "Current evidence cancels the bullish historical lean.",
        ),
        (typical > 0.5, "Projected median upside is too small."),
        (edge >= 4, "The analog edge is below four probability points."),
        (evidence >= 50, "Independent evidence is too thin."),
        (agreement_score >= 50, "The evidence components conflict."),
    )
    for passed, reason in checks:
        if not passed:
            reasons.append(reason)

    factors = {
        "predicted_increase": round(typical, 2),
        "analog_edge": round(edge, 1),
        "evidence_score": round(evidence),
        "agreement_score": round(agreement_score),
        "brier_skill": round(brier_skill, 1),
        "downside_estimate": round(downside, 2),
        "interval_width": round(width, 2),
    }
    upside_points = min(35.0, max(0.0, typical) / 15.0 * 35.0)
    edge_points = min(20.0, max(0.0, edge) / 15.0 * 20.0)
    evidence_points = min(15.0, evidence / 100.0 * 15.0)
    agreement_points = min(10.0, agreement_score / 100.0 * 10.0)
    skill_points = min(10.0, max(0.0, brier_skill) / 25.0 * 10.0)
    downside_penalty = min(15.0, downside / 15.0 * 15.0)
    width_penalty = min(5.0, width / 40.0 * 5.0)
    score = max(
        0.0,
        upside_points
        + edge_points
        + evidence_points
        + agreement_points
        + skill_points
        - downside_penalty
        - width_penalty,
    )
    return {
        "eligible": not reasons,
        "reason": " ".join(reasons) if reasons else (
            "Positive untouched audit, bullish analog edge, sufficient "
            "evidence, and non-conflicting path agreement."
        ),
        "opportunity_score": round(score, 1),
        "ranking_factors": factors,
    }


def compact_scan_result(analysis):
    ranking = rank_analysis(analysis)
    play = analysis.get("playbook", {})
    if not play.get("available"):
        return ranking
    forecast = play["forecast"]
    validation = play["validation"]
    return {
        **ranking,
        "name": analysis.get("name") or analysis["symbol"],
        "sector": analysis.get("sector", ""),
        "currency": analysis.get("currency", "USD"),
        "price": analysis["quote"]["price"],
        "horizon_days": forecast["horizon_days"],
        "horizon_label": forecast["horizon_label"],
        "probability_up": forecast["probability_up"],
        "analog_probability_up": forecast["analog_probability_up"],
        "baseline_up_rate": forecast["baseline_up_rate"],
        "edge_points": forecast["edge_points"],
        "evidence_score": forecast["evidence_score"],
        "range": forecast["range_21d"],
        "agreement": forecast.get("agreement", {}),
        "validation_grade": validation.get("grade"),
        "validation_label": validation.get("label"),
        "validation_sample_size": validation.get("sample_size", 0),
        "validation_accuracy": validation.get("accuracy"),
        "baseline_accuracy": validation.get("baseline_accuracy"),
        "brier_skill": validation.get("brier_skill"),
        "match_count": play.get("matching", {}).get("match_count"),
        "effective_matches": play.get("stats", {}).get("effective_matches"),
        "distinct_years": play.get("stats", {}).get("distinct_years"),
    }


class OpportunityScanner:
    def __init__(
        self,
        service,
        store,
        universe_provider=None,
        workers=3,
        lease_seconds=900,
        scan_time=DEFAULT_SCAN_TIME,
        algorithm_version=ALGORITHM_VERSION,
    ):
        self.service = service
        self.store = store
        self.universe_provider = universe_provider or SP500UniverseProvider(store)
        self.workers = max(1, min(8, int(workers)))
        self.lease_seconds = max(60, int(lease_seconds))
        self.scan_time = scan_time
        self.algorithm_version = algorithm_version

    def run_once(self, now=None, force_refresh=False):
        current = _utc(now)
        operation_now = current if now is not None else None
        spy_history, _warnings = self.service.provider.history(
            "SPY",
            self.service.provider._ticker("SPY"),
            force_refresh=force_refresh,
        )
        session_date = confirmed_scan_session(
            spy_history,
            now=current,
            scan_time=self.scan_time,
        )
        latest = self.store.latest_completed_scan(include_results=False)
        if (
            latest
            and latest["session_date"] == session_date
            and latest["algorithm_version"] == self.algorithm_version
        ):
            return {
                **latest,
                "started": False,
                "message": "This market session is already complete.",
            }
        universe = self.universe_provider.load(now=current)
        owner = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
        )
        run, acquired = self.store.acquire_scan_run(
            session_date=session_date,
            algorithm_version=self.algorithm_version,
            universe_id=universe["id"],
            constituents=universe["constituents"],
            owner=owner,
            lease_seconds=self.lease_seconds,
            now=current,
        )
        if not acquired:
            return {
                **run,
                "started": False,
                "message": (
                    "This market session is already complete."
                    if run["status"] in {"completed", "partial"}
                    else "Another scanner owns the active lease."
                ),
            }

        warnings = [universe["warning"]] if universe.get("warning") else []
        run_id = run["id"]
        try:
            pending = self.store.pending_scan_symbols(run_id)
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures = {}
                for item in pending:
                    if not self.store.claim_scan_symbol(
                        run_id,
                        item["symbol"],
                        owner,
                        now=current,
                    ):
                        continue
                    future = executor.submit(
                        self._scan_symbol,
                        run_id,
                        item,
                        owner,
                        session_date,
                        force_refresh,
                        operation_now,
                    )
                    futures[future] = item["symbol"]
                for future in as_completed(futures):
                    future.result()
                    if not self.store.heartbeat_scan_run(
                        run_id,
                        owner,
                        lease_seconds=self.lease_seconds,
                        now=operation_now,
                    ):
                        raise RuntimeError("The scanner lease was lost.")
            result = self.store.finish_scan_run(
                run_id,
                owner,
                warnings=warnings,
                now=operation_now,
            )
            result["started"] = True
            return result
        except Exception as exc:
            self.store.fail_scan_run(
                run_id,
                owner,
                str(exc),
                now=operation_now,
            )
            raise

    def _scan_symbol(
        self,
        run_id,
        item,
        owner,
        expected_session,
        force_refresh,
        operation_now,
    ):
        symbol = item["symbol"]
        try:
            analysis = self.service.analyze(
                symbol,
                force_refresh=force_refresh,
                include_validation=True,
            )
            analysis_session = (
                analysis.get("history", [{}])[-1].get("date")
                if analysis.get("history")
                else None
            )
            if analysis_session != expected_session:
                raise MarketDataError(
                    f"Latest adjusted price session is {analysis_session or 'unknown'}, "
                    f"not the confirmed scan session {expected_session}."
                )
            payload = compact_scan_result(analysis)
            status = "completed" if analysis.get("playbook", {}).get(
                "available"
            ) else "skipped"
            self.store.save_scan_result(
                run_id,
                symbol,
                status,
                owner,
                payload=payload,
                error=None if status == "completed" else payload.get("reason"),
                now=operation_now,
            )
        except MarketDataError as exc:
            self.store.save_scan_result(
                run_id,
                symbol,
                "failed",
                owner,
                error=str(exc),
                now=operation_now,
            )
        except Exception as exc:
            self.store.save_scan_result(
                run_id,
                symbol,
                "failed",
                owner,
                error=f"Unexpected analysis failure: {exc}",
                now=operation_now,
            )


class OpportunityBoardService:
    def __init__(self, store):
        self.store = store

    def latest(self, limit=25):
        latest = self.store.latest_completed_scan(include_results=True)
        active = self.store.active_scan_run()
        if latest is None:
            return {
                "available": False,
                "active_run": _public_run(active),
                "message": (
                    "No completed after-close S&P 500 scan exists yet. "
                    "Run python scan_sp500.py --once after the market closes."
                ),
                "opportunities": [],
            }
        public = _public_run(latest)
        all_eligible = [
            _public_result(item)
            for item in latest.get("results", [])
            if item.get("eligible")
        ]
        eligible = all_eligible[: max(1, min(100, int(limit)))]
        return {
            "available": True,
            "run": public,
            "active_run": (
                _public_run(active)
                if active and active["id"] != latest["id"]
                else None
            ),
            "eligible_count": len(all_eligible),
            "opportunities": eligible,
            "methodology": (
                "Ranks only bullish forecasts with a positive untouched audit. "
                "Projected median increase, analog edge, evidence, agreement, "
                "and Brier skill add points; downside and interval width subtract them."
            ),
        }

    def history(self, limit=20):
        return {
            "runs": [
                _public_run(run)
                for run in self.store.list_scan_runs(limit=limit)
            ]
        }


def _public_run(run):
    if run is None:
        return None
    completed = (
        int(run.get("completed_count", 0))
        + int(run.get("failed_count", 0))
        + int(run.get("skipped_count", 0))
    )
    total = int(run.get("total_count", 0))
    return {
        key: run.get(key)
        for key in (
            "id",
            "session_date",
            "algorithm_version",
            "status",
            "total_count",
            "completed_count",
            "failed_count",
            "skipped_count",
            "started_at",
            "updated_at",
            "completed_at",
            "runtime_seconds",
            "universe_source",
            "universe_source_timestamp",
            "universe_fetched_at",
            "warnings",
        )
    } | {
        "processed_count": completed,
        "progress_percent": round(completed / total * 100) if total else 0,
    }


def _public_result(item):
    return {
        key: item.get(key)
        for key in (
            "symbol",
            "display_symbol",
            "company_name",
            "sector",
            "rank",
            "opportunity_score",
            "name",
            "currency",
            "price",
            "horizon_days",
            "horizon_label",
            "probability_up",
            "analog_probability_up",
            "baseline_up_rate",
            "edge_points",
            "evidence_score",
            "range",
            "agreement",
            "validation_grade",
            "validation_label",
            "validation_sample_size",
            "validation_accuracy",
            "baseline_accuracy",
            "brier_skill",
            "match_count",
            "effective_matches",
            "distinct_years",
            "reason",
            "ranking_factors",
        )
    }


def seconds_until_scan(now=None, scan_time=DEFAULT_SCAN_TIME):
    current = _utc(now).astimezone(ZoneInfo("America/New_York"))
    target_time = _parse_scan_time(scan_time)
    target = datetime.combine(
        current.date(),
        target_time,
        tzinfo=current.tzinfo,
    )
    if current >= target:
        target += pd.Timedelta(days=1)
    return max(1, math.ceil((target - current).total_seconds()))


def run_scheduler(scanner, stop_event=None):
    event = stop_event or threading.Event()
    last_resolved_date = None
    while not event.is_set():
        local = datetime.now(timezone.utc).astimezone(
            ZoneInfo("America/New_York")
        )
        local_date = local.date().isoformat()
        after_target = local.time().replace(tzinfo=None) >= _parse_scan_time(
            scanner.scan_time
        )
        if after_target and last_resolved_date != local_date:
            try:
                result = scanner.run_once()
                if (
                    result.get("status") in {"completed", "partial"}
                    and (
                        result.get("session_date") == local_date
                        or local.weekday() >= 5
                    )
                ):
                    last_resolved_date = local_date
            except (ScanGateError, UniverseError, MarketDataError) as exc:
                print(f"Scheduled scan deferred: {exc}", flush=True)
        delay = (
            900
            if after_target
            else min(900, seconds_until_scan(scan_time=scanner.scan_time))
        )
        if event.wait(delay):
            break


def _parse_scan_time(value):
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return clock_time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
        raise ValueError("Scan time must use HH:MM in America/New_York.") from exc


def _utc(value=None):
    current = value or datetime.now(timezone.utc)
    if isinstance(current, pd.Timestamp):
        current = current.to_pydatetime()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(value=None):
    return _utc(value).isoformat()
