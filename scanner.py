import html
import math
import os
import random
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, time as clock_time, timezone
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import MarketDataError
from provenance import current_git_commit, runtime_config_hash
from scorecard import ScorecardService


ALGORITHM_VERSION = "opportunity-v2"
UNIVERSE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
MIN_UNIVERSE_SIZE = 450
MIN_NASDAQ_UNIVERSE_SIZE = 1500
DEFAULT_SCAN_TIME = "17:15"
DEFAULT_UNIVERSE_SCOPE = "nasdaq"
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.4
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_SECONDS = 2.0
DEFAULT_RETRY_MAX_SECONDS = 30.0
DEFAULT_RETRY_JITTER_SECONDS = 1.0

# Derivative share classes that are not ordinary tradable equity exposure.
_NON_COMMON_NAME = re.compile(
    r"\b("
    r"warrant|warrants|right|rights|unit|units|preferred|depositary share|"
    r"notes? due|debenture|subordinated|trust preferred|contingent value"
    r")\b",
    re.IGNORECASE,
)
_NON_COMMON_SUFFIX = ("W", "R", "U", "P")

# Minimum quality gates before a symbol may appear on the board at all.
MIN_BOARD_SCORE = 18.0
MIN_EXPECTED_MOVE = 1.5
# A blind 26,347-cell replay found sub-$5 signals returned 0.43 points *below*
# an equal-weight basket of the same names at more than double the dispersion.
# A $5 floor drops that bucket while keeping 91.5% of the board; raising it to
# $15 bought only 0.09 more points of excess for a third of the coverage.
MIN_PRICE = 5.0
MIN_REWARD_RISK = 1.0

# The 20th-80th band cannot measure downside when it sits entirely on one side
# of zero: adverse movement reads as 0 and reward/risk explodes against a fixed
# floor. Those signals showed a median reward/risk of 15.1 while actually
# dipping 13.0% intraperiod, worse than signals whose band does cross zero.
# Falling back to a share of the band's own spread keeps the ratio on a scale
# the forecast actually measured.
MIN_RISK_FLOOR = 0.5
RISK_FLOOR_WIDTH_SHARE = 0.25

# Requiring reward/risk at entry rather than only capping the tier lifted the
# realized win rate from 53.9% to 58.2% out of sample, but it also removes
# roughly 85% of board entries. Opt in with PLAYBOOK_REQUIRE_REWARD_RISK=1.
REQUIRE_REWARD_RISK_ENV = "PLAYBOOK_REQUIRE_REWARD_RISK"


def _reward_risk_required():
    return os.environ.get(REQUIRE_REWARD_RISK_ENV, "").strip() in {"1", "true", "yes"}


class UniverseError(RuntimeError):
    pass


class ScanGateError(RuntimeError):
    pass


class _RequestPacer:
    """Reserve globally spaced request starts across scanner workers."""

    def __init__(self, interval_seconds, sleep=time.sleep, monotonic=time.monotonic):
        self.interval_seconds = max(0.0, float(interval_seconds))
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self):
        if not self.interval_seconds:
            return
        with self._lock:
            current = self._monotonic()
            delay = max(0.0, self._next_start - current)
            self._next_start = max(current, self._next_start) + self.interval_seconds
        if delay:
            self._sleep(delay)


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


def _fetch_text(opener, url, timeout_seconds):
    request = Request(
        url,
        headers={"User-Agent": "Playbook opportunity scanner/2.0"},
    )
    with opener(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
        stamp = response.headers.get("Last-Modified")
    return body, stamp


def _looks_like_common_stock(symbol, name):
    if not symbol or not name:
        return False
    if "$" in symbol or "^" in symbol:
        return False
    if _NON_COMMON_NAME.search(name):
        return False
    # A fifth character on a Nasdaq ticker encodes the issue type; W/R/U/P are
    # warrants, rights, units, and preferreds rather than ordinary shares.
    if len(symbol) == 5 and symbol[-1] in _NON_COMMON_SUFFIX:
        return False
    return True


def _clean_company_name(name):
    trimmed = name.split(" - ")[0].strip()
    return trimmed or name.strip()


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
                "list": "S&P 500",
            }
        )
    if len(constituents) < MIN_UNIVERSE_SIZE:
        raise UniverseError(
            f"Only {len(constituents)} constituents were parsed; "
            "the universe was rejected as incomplete."
        )
    return constituents


def _parse_symbol_directory(document, columns, exchange_label):
    lines = [line for line in document.splitlines() if line.strip()]
    if not lines:
        raise UniverseError(f"The {exchange_label} symbol directory was empty.")
    headers = [part.strip() for part in lines[0].split("|")]
    try:
        indexes = {key: headers.index(header) for key, header in columns.items()}
    except ValueError as exc:
        raise UniverseError(
            f"The {exchange_label} symbol directory format changed."
        ) from exc
    required = max(indexes.values())
    constituents = []
    seen = set()
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) <= required:
            continue
        if parts[indexes["test_issue"]].upper() == "Y":
            continue
        if parts[indexes["etf"]].upper() == "Y":
            continue
        status_index = indexes.get("financial_status")
        if status_index is not None:
            status = parts[status_index].upper()
            if status and status not in {"N"}:
                continue
        display_symbol = parts[indexes["symbol"]].upper()
        name = parts[indexes["name"]]
        if not _looks_like_common_stock(display_symbol, name):
            continue
        symbol = display_symbol.replace(".", "-")
        if symbol in seen:
            continue
        seen.add(symbol)
        constituents.append(
            {
                "symbol": symbol,
                "display_symbol": display_symbol,
                "name": _clean_company_name(name),
                "sector": "",
                "list": exchange_label,
            }
        )
    return constituents


def parse_nasdaq_listed(document):
    constituents = _parse_symbol_directory(
        document,
        {
            "symbol": "Symbol",
            "name": "Security Name",
            "test_issue": "Test Issue",
            "financial_status": "Financial Status",
            "etf": "ETF",
        },
        "Nasdaq",
    )
    if len(constituents) < MIN_NASDAQ_UNIVERSE_SIZE:
        raise UniverseError(
            f"Only {len(constituents)} Nasdaq common stocks were parsed; "
            "the universe was rejected as incomplete."
        )
    return constituents


def parse_other_listed(document):
    return _parse_symbol_directory(
        document,
        {
            "symbol": "ACT Symbol",
            "name": "Security Name",
            "test_issue": "Test Issue",
            "etf": "ETF",
        },
        "NYSE / NYSE American",
    )


def merge_universes(*groups):
    merged = {}
    for group in groups:
        for item in group or []:
            symbol = item["symbol"]
            existing = merged.get(symbol)
            if existing is None:
                merged[symbol] = dict(item)
                continue
            # Prefer the richer record: the S&P table carries sector metadata.
            if not existing.get("sector") and item.get("sector"):
                existing["sector"] = item["sector"]
            if item.get("list") == "S&P 500":
                existing["list"] = "S&P 500"
    return [merged[symbol] for symbol in sorted(merged)]


class MarketUniverseProvider:
    """Builds the daily scan universe from public listing directories."""

    def __init__(
        self,
        store,
        scope=None,
        timeout_seconds=30,
        opener=None,
        max_symbols=None,
    ):
        self.store = store
        self.scope = (scope or os.getenv("PLAYBOOK_UNIVERSE", DEFAULT_UNIVERSE_SCOPE)).lower()
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urlopen
        env_max = os.getenv("PLAYBOOK_MAX_SYMBOLS") or ""
        self.max_symbols = (
            int(max_symbols)
            if max_symbols is not None
            else (int(env_max) if env_max.strip().isdigit() else None)
        )

    def _sources(self):
        if self.scope == "sp500":
            return [(UNIVERSE_URL, parse_sp500_constituents, True)]
        if self.scope == "us":
            return [
                (NASDAQ_LISTED_URL, parse_nasdaq_listed, True),
                (OTHER_LISTED_URL, parse_other_listed, False),
                (UNIVERSE_URL, parse_sp500_constituents, False),
            ]
        return [
            (NASDAQ_LISTED_URL, parse_nasdaq_listed, True),
            (UNIVERSE_URL, parse_sp500_constituents, False),
        ]

    def load(self, now=None):
        groups = []
        sources = []
        stamps = []
        soft_failures = []
        try:
            for url, parser, required in self._sources():
                try:
                    body, stamp = _fetch_text(
                        self.opener, url, self.timeout_seconds
                    )
                    groups.append(parser(body))
                    sources.append(url)
                    if stamp:
                        stamps.append(stamp)
                except (OSError, TimeoutError, UnicodeError, UniverseError):
                    if required:
                        raise
                    soft_failures.append(url)
            constituents = merge_universes(*groups)
            if not constituents:
                raise UniverseError("No listed common stocks were parsed.")
            if self.max_symbols:
                constituents = constituents[: self.max_symbols]
            universe_id = self.store.save_scan_universe(
                constituents,
                source=" + ".join(sources),
                source_timestamp=stamps[0] if stamps else None,
                now=now,
            )
            warning = (
                "Part of the listing directory was unavailable; the universe "
                f"was built without {', '.join(soft_failures)}."
                if soft_failures
                else None
            )
            return {
                "id": universe_id,
                "constituents": constituents,
                "source": " + ".join(sources),
                "source_timestamp": stamps[0] if stamps else None,
                "fetched_at": _iso(now),
                "stale": False,
                "warning": warning,
            }
        except (OSError, TimeoutError, UnicodeError, UniverseError) as exc:
            cached = self.store.latest_scan_universe()
            if cached is None:
                raise UniverseError(
                    "The scan universe could not be verified and no "
                    "last-known-good snapshot exists."
                ) from exc
            return {
                **cached,
                "stale": True,
                "warning": (
                    "The live listing directory was unavailable; the last "
                    "verified universe snapshot is in use."
                ),
            }


class SP500UniverseProvider(MarketUniverseProvider):
    """Backward-compatible S&P 500 only universe."""

    def __init__(self, store, url=UNIVERSE_URL, timeout_seconds=20, opener=None):
        super().__init__(
            store,
            scope="sp500",
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        self.url = url

    def _sources(self):
        return [(self.url, parse_sp500_constituents, True)]


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


def _clamp(value, low, high):
    return max(low, min(high, value))


AUDIT_POINTS = {"positive": 14.0, "mixed": 9.0, "limited": 5.0, "weak": 2.0}
SIGNAL_LABELS = {
    ("long", "strong"): "STRONG BUY",
    ("long", "moderate"): "BUY",
    ("long", "speculative"): "WEAK BUY",
    ("short", "strong"): "STRONG SHORT",
    ("short", "moderate"): "SHORT",
    ("short", "speculative"): "WEAK SHORT",
}


def rank_analysis(analysis):
    """Score a completed analysis for both the long and short board."""

    play = analysis.get("playbook", {})
    if not play.get("available"):
        return {
            "eligible": False,
            "side": None,
            "reason": play.get("reason", "No analog forecast was available."),
        }

    forecast = play["forecast"]
    validation = play.get("validation", {})
    agreement = forecast.get("agreement", {})
    range_data = forecast["range_21d"]

    typical = float(range_data["typical"])
    low = float(range_data["low"])
    high = float(range_data["high"])
    width = max(0.0, high - low)
    edge = float(forecast["edge_points"])
    evidence = float(forecast["evidence_score"])
    agreement_score = float(agreement.get("score", 0))
    brier_skill = float(validation.get("brier_skill") or -100)
    grade = validation.get("grade")
    # The displayed win probability uses the recalibrated value; ranking and
    # tier gating below stay on raw edge_points so the board ordering and the
    # forecast ledger keep one definition.
    probability_up = float(
        forecast.get("calibrated_probability_up")
        if forecast.get("calibrated_probability_up") is not None
        else forecast.get("probability_up", 50)
    )
    analog_direction = forecast.get("analog_direction")
    direction = forecast.get("direction")
    price = float(analysis.get("quote", {}).get("price") or 0)

    if analog_direction == "bullish":
        side = "long"
    elif analog_direction == "bearish":
        side = "short"
    else:
        return {
            "eligible": False,
            "side": None,
            "reason": (
                "The closest historical setups did not lean far enough in "
                "either direction to be worth acting on."
            ),
        }

    news_conflict = direction != analog_direction
    if side == "long":
        expected_move = typical
        adverse_move = max(0.0, -low)
        favorable_move = max(0.0, high)
        win_probability = probability_up
    else:
        expected_move = -typical
        adverse_move = max(0.0, high)
        favorable_move = max(0.0, -low)
        win_probability = 100.0 - probability_up

    risk_floor = max(MIN_RISK_FLOOR, RISK_FLOOR_WIDTH_SHARE * width)
    reward_risk = expected_move / max(adverse_move, risk_floor)

    move_points = _clamp(expected_move / 12.0 * 30.0, 0.0, 30.0)
    edge_points = _clamp(abs(edge) / 15.0 * 22.0, 0.0, 22.0)
    evidence_points = _clamp(evidence / 100.0 * 16.0, 0.0, 16.0)
    agreement_points = _clamp(agreement_score / 100.0 * 10.0, 0.0, 10.0)
    audit_points = AUDIT_POINTS.get(grade, 0.0)
    skill_points = _clamp(max(0.0, brier_skill) / 25.0 * 8.0, 0.0, 8.0)
    risk_penalty = _clamp(adverse_move / 15.0 * 14.0, 0.0, 14.0)
    width_penalty = _clamp(width / 45.0 * 6.0, 0.0, 6.0)
    news_penalty = 8.0 if news_conflict else 0.0

    score = _clamp(
        move_points
        + edge_points
        + evidence_points
        + agreement_points
        + audit_points
        + skill_points
        - risk_penalty
        - width_penalty
        - news_penalty,
        0.0,
        100.0,
    )

    blockers = []
    if not validation.get("available"):
        blockers.append("The untouched walk-forward audit could not run.")
    if expected_move < MIN_EXPECTED_MOVE:
        blockers.append(
            f"The typical historical move was under {MIN_EXPECTED_MOVE:.1f}%."
        )
    if price and price < MIN_PRICE:
        blockers.append(f"The share price is below ${MIN_PRICE:.0f}.")
    if _reward_risk_required() and reward_risk < MIN_REWARD_RISK:
        blockers.append(
            "The expected move does not clear the adverse move, so reward "
            "versus risk is below the required minimum."
        )
    if score < MIN_BOARD_SCORE:
        blockers.append("Risk and uncertainty cancelled the historical edge.")

    strong = (
        not blockers
        and not news_conflict
        and grade == "positive"
        and abs(edge) >= 6
        and evidence >= 55
        and agreement_score >= 55
        and score >= 62
        and reward_risk >= MIN_REWARD_RISK
    )
    moderate = (
        not blockers
        and grade in {"positive", "mixed"}
        and abs(edge) >= 4
        and score >= 40
        and reward_risk >= MIN_REWARD_RISK
    )
    tier = "strong" if strong else "moderate" if moderate else "speculative"
    signal = SIGNAL_LABELS[(side, tier)]

    factors = {
        "expected_move": round(expected_move, 2),
        "adverse_move": round(adverse_move, 2),
        "favorable_move": round(favorable_move, 2),
        "reward_risk": round(reward_risk, 2),
        "analog_edge": round(edge, 1),
        "evidence_score": round(evidence),
        "agreement_score": round(agreement_score),
        "brier_skill": round(brier_skill, 1),
        "interval_width": round(width, 2),
        "predicted_increase": round(typical, 2),
        "downside_estimate": round(max(0.0, -low), 2),
    }

    return {
        "eligible": not blockers,
        "side": side,
        "tier": tier,
        "signal": signal,
        "news_conflict": news_conflict,
        "opportunity_score": round(score, 1),
        "expected_move": round(expected_move, 2),
        "adverse_move": round(adverse_move, 2),
        "reward_risk": round(reward_risk, 2),
        "win_probability": round(win_probability),
        "reason": (
            " ".join(blockers)
            if blockers
            else _plain_reason(
                side,
                tier,
                win_probability,
                expected_move,
                adverse_move,
                reward_risk,
                grade,
                news_conflict,
                forecast.get("horizon_label", "the next month"),
            )
        ),
        "ranking_factors": factors,
    }


def _plain_reason(
    side,
    tier,
    win_probability,
    expected_move,
    adverse_move,
    reward_risk,
    grade,
    news_conflict,
    horizon_label,
):
    action = "rose" if side == "long" else "fell"
    lead = (
        f"{round(win_probability)}% of the closest historical setups {action} "
        f"over {horizon_label}, by about {expected_move:.1f}% in the typical "
        f"case against roughly {adverse_move:.1f}% of adverse movement."
    )
    if tier == "strong":
        support = " The untouched audit graded this matcher positively and every component agrees."
    elif tier == "moderate":
        support = f" The untouched audit graded this matcher {grade or 'inconclusive'}."
    elif reward_risk < MIN_REWARD_RISK:
        support = (
            " The expected move is smaller than the adverse move, so the "
            "reward/risk coherence floor limits this to a watchlist idea."
        )
    else:
        support = " Evidence is thin, so treat this as a watchlist idea rather than a signal."
    conflict = (
        " Current headlines push against the historical lean."
        if news_conflict
        else ""
    )
    return lead + support + conflict


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
        request_interval_seconds=0,
        retry_attempts=1,
        retry_base_seconds=0,
        retry_max_seconds=0,
        retry_jitter_seconds=0,
        sleep=time.sleep,
        random_uniform=random.uniform,
        progress_callback=None,
        progress_every=25,
    ):
        self.service = service
        self.store = store
        self.universe_provider = universe_provider or MarketUniverseProvider(store)
        self.workers = max(1, min(16, int(workers)))
        self.lease_seconds = max(60, int(lease_seconds))
        self.scan_time = scan_time
        self.algorithm_version = algorithm_version
        self.model_version = os.getenv(
            "PLAYBOOK_MODEL_VERSION",
            self.algorithm_version,
        )
        self.git_commit = current_git_commit()
        self.config_hash = runtime_config_hash(
            {
                "algorithm_version": self.algorithm_version,
                "model_version": self.model_version,
                "scan_time": self.scan_time,
                "minimum_board_score": MIN_BOARD_SCORE,
                "minimum_expected_move": MIN_EXPECTED_MOVE,
                "minimum_price": MIN_PRICE,
                "minimum_reward_risk": MIN_REWARD_RISK,
            }
        )
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(
            self.retry_base_seconds,
            float(retry_max_seconds),
        )
        self.retry_jitter_seconds = max(0.0, float(retry_jitter_seconds))
        self._sleep = sleep
        self._random_uniform = random_uniform
        self._pacer = _RequestPacer(request_interval_seconds, sleep=sleep)
        self.progress_callback = progress_callback
        self.progress_every = max(1, int(progress_every))

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
            self._ensure_scorecard_snapshot(latest)
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
            if run["status"] in {"completed", "partial"}:
                self._ensure_scorecard_snapshot(run)
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
            processed = (
                int(run.get("completed_count", 0))
                + int(run.get("failed_count", 0))
                + int(run.get("skipped_count", 0))
            )
            total = int(run.get("total_count", len(pending)))
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
                        universe["id"],
                        force_refresh,
                        operation_now,
                    )
                    futures[future] = item["symbol"]
                for future in as_completed(futures):
                    outcome = future.result()
                    processed += 1
                    if self.progress_callback and (
                        processed % self.progress_every == 0 or processed == total
                    ):
                        self._report_progress(
                            f"Scan progress: {processed}/{total} processed; "
                            f"latest {outcome['symbol']} {outcome['status']}."
                        )
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
            self._ensure_scorecard_snapshot(result, now=operation_now)
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

    def _ensure_scorecard_snapshot(self, run, now=None):
        if not run or run.get("status") not in {"completed", "partial"}:
            return False
        report = ScorecardService(self.store).current(
            as_of_date=run["session_date"],
            as_of_timestamp=run.get("completed_at"),
        )
        provenance = self.store.forecast_provenance_for_scan(run["id"])
        return self.store.append_scorecard_snapshot(
            scan_run_id=run["id"],
            session_date=run["session_date"],
            report=report,
            model_version=(
                provenance.get("model_version")
                or run.get("algorithm_version")
                or "unknown"
            ),
            git_commit=provenance.get("git_commit") or "unknown",
            config_hash=provenance.get("config_hash") or "unknown",
            data_vintage=report["data_vintage"],
            universe_id=run.get("universe_id"),
            now=now,
        )

    def _report_progress(self, message):
        """Keep a detached or broken console from invalidating scan work."""

        try:
            self.progress_callback(message)
        except OSError:
            self.progress_callback = None

    def _scan_symbol(
        self,
        run_id,
        item,
        owner,
        expected_session,
        universe_id,
        force_refresh,
        operation_now,
    ):
        symbol = item["symbol"]
        for attempt in range(1, self.retry_attempts + 1):
            self._pacer.wait()
            try:
                context_factory = getattr(
                    self.service,
                    "forecast_context",
                    None,
                )
                context = (
                    context_factory(
                        model_version=self.model_version,
                        git_commit=self.git_commit,
                        config_hash=self.config_hash,
                        universe_id=int(universe_id),
                        scan_run_id=int(run_id),
                    )
                    if context_factory is not None
                    else nullcontext()
                )
                with context:
                    analysis = self.service.analyze(
                        symbol,
                        force_refresh=force_refresh,
                        include_validation=True,
                    )
                break
            except MarketDataError as exc:
                if attempt == self.retry_attempts:
                    detail = str(exc)
                    if self.retry_attempts > 1:
                        detail = (
                            f"{detail} (failed after {self.retry_attempts} attempts)"
                        )
                    self.store.save_scan_result(
                        run_id,
                        symbol,
                        "failed",
                        owner,
                        error=detail,
                        now=operation_now,
                    )
                    return {
                        "symbol": symbol,
                        "status": "failed",
                        "attempts": attempt,
                    }
                backoff = min(
                    self.retry_max_seconds,
                    self.retry_base_seconds * (2 ** (attempt - 1)),
                )
                jitter = self._random_uniform(0, self.retry_jitter_seconds)
                self._sleep(backoff + jitter)
            except Exception as exc:
                self.store.save_scan_result(
                    run_id,
                    symbol,
                    "failed",
                    owner,
                    error=f"Unexpected analysis failure: {exc}",
                    now=operation_now,
                )
                return {
                    "symbol": symbol,
                    "status": "failed",
                    "attempts": attempt,
                }
        try:
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
            return {"symbol": symbol, "status": status, "attempts": attempt}
        except MarketDataError as exc:
            self.store.save_scan_result(
                run_id,
                symbol,
                "failed",
                owner,
                error=str(exc),
                now=operation_now,
            )
            return {"symbol": symbol, "status": "failed", "attempts": attempt}
        except Exception as exc:
            self.store.save_scan_result(
                run_id,
                symbol,
                "failed",
                owner,
                error=f"Unexpected analysis failure: {exc}",
                now=operation_now,
            )
            return {"symbol": symbol, "status": "failed", "attempts": attempt}


BOARD_METHODOLOGY = (
    "Nasdaq-listed common stocks plus current S&P 500 constituents run the same "
    "audited historical-analog engine by default. A name reaches the buy board "
    "only when its closest past setups leaned up, and the short board only when "
    "they leaned down. Expected move, probability edge, evidence, agreement, "
    "and audited skill add score; adverse movement, uncertainty, and conflicting "
    "news subtract it. Reward/risk below 1.0 limits a name to the speculative tier."
)


class OpportunityBoardService:
    def __init__(self, store):
        self.store = store

    def latest(self, limit=50, side=None):
        latest = self.store.latest_completed_scan(include_results=True)
        active = self.store.active_scan_run()
        if latest is None:
            return {
                "available": False,
                "active_run": _public_run(active),
                "message": (
                    "No completed market scan exists yet. Run "
                    "python scan_sp500.py --once after the close."
                ),
                "longs": [],
                "shorts": [],
                "opportunities": [],
            }
        public = _public_run(latest)
        results = latest.get("results", [])
        bounded = max(1, min(250, int(limit)))
        longs = [
            _public_result(item)
            for item in results
            if item.get("eligible") and item.get("side") == "long"
        ]
        shorts = [
            _public_result(item)
            for item in results
            if item.get("eligible") and item.get("side") == "short"
        ]
        payload = {
            "available": True,
            "run": public,
            "active_run": (
                _public_run(active)
                if active and active["id"] != latest["id"]
                else None
            ),
            "long_count": len(longs),
            "short_count": len(shorts),
            "eligible_count": len(longs) + len(shorts),
            "longs": longs[:bounded] if side in (None, "long") else [],
            "shorts": shorts[:bounded] if side in (None, "short") else [],
            "methodology": BOARD_METHODOLOGY,
        }
        # Backward-compatible flat list used by the previous board API.
        payload["opportunities"] = payload["longs"]
        return payload

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
            "side",
            "tier",
            "signal",
            "news_conflict",
            "opportunity_score",
            "expected_move",
            "adverse_move",
            "reward_risk",
            "win_probability",
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


def build_default_scanner(store, service=None, workers=None, scan_time=None):
    """Create the scanner used by both the CLI and the hosted web process."""

    worker_count = int(workers or os.getenv("PLAYBOOK_SCAN_WORKERS", "3"))
    if service is None:
        from market_data import MarketIntelligenceService, YahooFinanceProvider

        service = MarketIntelligenceService(
            YahooFinanceProvider(store=store),
            cache_seconds=0,
            max_cache_entries=max(4, min(16, worker_count) * 2),
            source_cache_seconds=0,
        )
    return OpportunityScanner(
        service,
        store,
        workers=worker_count,
        scan_time=scan_time or os.getenv("PLAYBOOK_SCAN_TIME", DEFAULT_SCAN_TIME),
        request_interval_seconds=float(
            os.getenv(
                "PLAYBOOK_SCAN_REQUEST_INTERVAL",
                str(DEFAULT_REQUEST_INTERVAL_SECONDS),
            )
        ),
        retry_attempts=int(
            os.getenv("PLAYBOOK_SCAN_RETRY_ATTEMPTS", str(DEFAULT_RETRY_ATTEMPTS))
        ),
        retry_base_seconds=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_BASE_SECONDS",
                str(DEFAULT_RETRY_BASE_SECONDS),
            )
        ),
        retry_max_seconds=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_MAX_SECONDS",
                str(DEFAULT_RETRY_MAX_SECONDS),
            )
        ),
        retry_jitter_seconds=float(
            os.getenv(
                "PLAYBOOK_SCAN_RETRY_JITTER_SECONDS",
                str(DEFAULT_RETRY_JITTER_SECONDS),
            )
        ),
        progress_callback=lambda message: print(message, flush=True),
    )


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
            except Exception as exc:  # keep the daemon alive
                print(f"Scheduled scan failed: {exc}", flush=True)
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
