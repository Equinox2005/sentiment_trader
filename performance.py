"""Running mark-to-market over the forecast ledger.

The scorecard only speaks once a forecast reaches its horizon, which leaves the
record silent for the whole 21-session window after a scan. This module marks
every open forecast against the latest stored close so the site can show what
the live cohort is doing today, using the same entry convention the grader
commits to: buy the next session's open, measure to the session close.
"""

import statistics
import threading
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from scorecard import ForecastObservation, direction_adjusted_return

LEADERBOARD_SIZE = 50
# A session only counts as a market session once a broad share of the tracked
# symbols traded in it. Without this, a single crypto ticker's weekend bar
# would advance the whole board a day early and mark every equity as flat.
MINIMUM_SESSION_COVERAGE = 0.2

# Cached bars are not retroactively split-adjusted, so a reverse split lands in
# the history as an overnight jump: a 1-for-40 split reads as a +3,900% gain and
# one such name would move the average across a thousand forecasts by more than
# the signal itself. A position whose holding window contains a move this large
# is withheld from the average and reported instead of being trusted.
MAXIMUM_SESSION_GAIN = 300.0
MAXIMUM_SESSION_LOSS = -75.0

OPEN = "open"
MATURED = "matured"
AWAITING_ENTRY = "awaiting_entry"
UNPRICED = "unpriced"
SUSPECT = "suspect"


@dataclass(frozen=True)
class Position:
    symbol: str
    side: str | None
    tier: str
    direction: str | None
    as_of_date: str
    horizon_days: int
    state: str
    entry_date: str | None = None
    entry_price: float | None = None
    mark_date: str | None = None
    mark_price: float | None = None
    sessions_elapsed: int = 0
    price_return: float | None = None
    signed_return: float | None = None

    @property
    def is_marked(self):
        return self.state in {OPEN, MATURED}


def latest_market_session(bars, on_or_before=None, coverage=MINIMUM_SESSION_COVERAGE):
    """The most recent session in which a broad share of symbols traded."""
    counts = Counter()
    for symbol_bars in bars.values():
        for session, _open_price, _close_price in symbol_bars:
            if on_or_before is None or session <= on_or_before:
                counts[session] += 1
    if not counts:
        return None
    threshold = max(1, int(len(bars) * coverage))
    traded = [session for session, count in counts.items() if count >= threshold]
    return max(traded) if traded else None


def mark_position(observation, symbol_bars, evaluation_session):
    """Value one forecast against the bars available up to a session."""
    base = {
        "symbol": observation.symbol,
        "side": observation.side,
        "tier": observation.tier,
        "direction": _direction_of(observation),
        "as_of_date": observation.as_of_date,
        "horizon_days": observation.horizon_days,
    }
    # A graded forecast already has its answer in the ledger. Reading it back
    # rather than recomputing keeps this page and the scorecard from ever
    # quoting two different numbers for the same settled forecast, including
    # after old price bars are pruned.
    if observation.status == "graded" and observation.realized_return is not None:
        return _graded_position(observation, symbol_bars, base)

    sessions = [bar[0] for bar in symbol_bars]
    signal_index = bisect_left(sessions, observation.as_of_date)
    if (
        evaluation_session is None
        or signal_index >= len(sessions)
        or sessions[signal_index] != observation.as_of_date
    ):
        return Position(state=UNPRICED, **base)

    entry_index = signal_index + 1
    outcome_index = signal_index + observation.horizon_days
    available_index = bisect_right(sessions, evaluation_session) - 1
    mark_index = min(available_index, outcome_index)
    if entry_index >= len(sessions) or mark_index < entry_index:
        return Position(state=AWAITING_ENTRY, **base)

    entry_price = symbol_bars[entry_index][1]
    mark_price = symbol_bars[mark_index][2]
    if entry_price is None or entry_price <= 0 or mark_price is None:
        return Position(state=UNPRICED, **base)

    price_return = ((mark_price / entry_price) - 1) * 100
    if has_price_break(symbol_bars, entry_index, mark_index):
        return Position(
            state=SUSPECT,
            entry_date=sessions[entry_index],
            entry_price=round(float(entry_price), 4),
            mark_date=sessions[mark_index],
            mark_price=round(float(mark_price), 4),
            sessions_elapsed=mark_index - signal_index,
            price_return=_rounded(price_return),
            **base,
        )
    return Position(
        state=MATURED if mark_index >= outcome_index else OPEN,
        entry_date=sessions[entry_index],
        entry_price=round(float(entry_price), 4),
        mark_date=sessions[mark_index],
        mark_price=round(float(mark_price), 4),
        sessions_elapsed=mark_index - signal_index,
        price_return=_rounded(price_return),
        signed_return=_rounded(
            direction_adjusted_return(observation.side, price_return)
        ),
        **base,
    )


def has_price_break(symbol_bars, entry_index, mark_index):
    """True when a session-to-session move is too large to be a real move."""
    for index in range(entry_index + 1, mark_index + 1):
        previous_close = symbol_bars[index - 1][2]
        close = symbol_bars[index][2]
        if not previous_close or previous_close <= 0 or close is None:
            continue
        move = ((close / previous_close) - 1) * 100
        if move > MAXIMUM_SESSION_GAIN or move < MAXIMUM_SESSION_LOSS:
            return True
    return False


def _graded_position(observation, symbol_bars, base):
    prices = {session: (o, c) for session, o, c in symbol_bars}
    entry = prices.get(observation.entry_date)
    outcome = prices.get(observation.outcome_date)
    return Position(
        state=MATURED,
        entry_date=observation.entry_date,
        entry_price=round(entry[0], 4) if entry and entry[0] else None,
        mark_date=observation.outcome_date,
        mark_price=round(outcome[1], 4) if outcome and outcome[1] else None,
        sessions_elapsed=observation.horizon_days,
        price_return=_rounded(observation.realized_return),
        signed_return=_rounded(
            direction_adjusted_return(observation.side, observation.realized_return)
        ),
        **base,
    )


def build_live_performance(
    observations,
    bars,
    *,
    evaluation_date,
    leaderboard_size=LEADERBOARD_SIZE,
):
    observations = list(observations)
    evaluation_session = latest_market_session(bars, on_or_before=evaluation_date)
    positions = [
        mark_position(item, bars.get(item.symbol, ()), evaluation_session)
        for item in observations
    ]

    scored = [item for item in positions if item.signed_return is not None]
    # A neutral forecast makes no directional claim, so signing its move would
    # invent a call the model never made. It stays visible in the accounting
    # instead of quietly vanishing from the denominator.
    neutral = [
        item
        for item in positions
        if item.is_marked and item.side is None
    ]
    counts = {
        "total": len(positions),
        "scored": len(scored),
        "neutral": len(neutral),
        "awaiting_entry": sum(
            1 for item in positions if item.state == AWAITING_ENTRY
        ),
        "unpriced": sum(1 for item in positions if item.state == UNPRICED),
        "suspect": sum(1 for item in positions if item.state == SUSPECT),
        "matured": sum(1 for item in scored if item.state == MATURED),
        "open": sum(1 for item in scored if item.state == OPEN),
    }

    benchmark = _benchmark_windows(bars, scored)
    cohort_dates = sorted({item.as_of_date for item in observations})
    return {
        "evaluation_date": evaluation_date,
        "evaluation_session": evaluation_session,
        "counts": counts,
        "headline": _headline(scored, benchmark),
        "progress": _progress(scored or neutral),
        "sides": _side_breakdown(scored),
        "leaderboards": [
            _leaderboard(scored, "long", winners=True, limit=leaderboard_size),
            _leaderboard(scored, "short", winners=True, limit=leaderboard_size),
            _leaderboard(scored, "long", winners=False, limit=leaderboard_size),
            _leaderboard(scored, "short", winners=False, limit=leaderboard_size),
        ],
        "withheld": [
            _position_payload(item)
            for item in sorted(
                (item for item in positions if item.state == SUSPECT),
                key=lambda item: abs(item.price_return or 0.0),
                reverse=True,
            )[:leaderboard_size]
        ],
        "neutral_sample": len(neutral),
        "cohort_start": cohort_dates[0] if cohort_dates else None,
        "cohort_end": cohort_dates[-1] if cohort_dates else None,
        "limitations": (
            "This is the average price move per forecast, equally weighted and "
            "marked to the latest close. It is not a portfolio return: it "
            "excludes trading costs, position sizing, capital limits, and the "
            "overlap between forecasts issued on the same day."
        ),
    }


class LivePerformanceService:
    def __init__(self, store):
        self.store = store
        self._cache_lock = threading.Lock()
        self._cache_key = None
        self._cache_value = None

    def current(self, evaluation_date=None):
        session_date = evaluation_date or datetime.now(
            ZoneInfo("America/New_York")
        ).date().isoformat()
        cache_key = None
        ledger = getattr(self.store, "forecast_ledger_fingerprint", None)
        prices = getattr(self.store, "price_ledger_fingerprint", None)
        if ledger is not None and prices is not None:
            cache_key = (ledger(), prices(), session_date)
            with self._cache_lock:
                if cache_key == self._cache_key:
                    return self._cache_value
        report = self._build(session_date)
        if cache_key is not None:
            with self._cache_lock:
                self._cache_key = cache_key
                self._cache_value = report
        return report

    def _build(self, session_date):
        observations = [
            ForecastObservation.from_record(record)
            for record in self.store.list_all_forecasts()
            if record["as_of_date"] <= session_date
        ]
        if not observations:
            return build_live_performance(
                [],
                {},
                evaluation_date=session_date,
            )
        earliest = min(item.as_of_date for item in observations)
        bars = self.store.session_bars(
            [item.symbol for item in observations],
            earliest,
        )
        return build_live_performance(
            observations,
            bars,
            evaluation_date=session_date,
        )


def _headline(scored, benchmark):
    if not scored:
        return {
            "available": False,
            "reason": (
                "No forecast has a tradable entry yet. The first mark appears "
                "after the next session opens."
            ),
            "metrics": None,
        }
    returns = [item.signed_return for item in scored]
    paired_signal = []
    paired_benchmark = []
    for item in scored:
        window = benchmark.get((item.entry_date, item.mark_date))
        adjusted = direction_adjusted_return(item.side, window)
        if adjusted is not None:
            paired_signal.append(item.signed_return)
            paired_benchmark.append(adjusted)
    return {
        "available": True,
        "reason": None,
        "metrics": {
            "sample_size": len(returns),
            "average_return": _rounded(statistics.fmean(returns)),
            "median_return": _rounded(statistics.median(returns)),
            "standard_deviation": _rounded(
                statistics.stdev(returns) if len(returns) > 1 else 0.0
            ),
            "positive_share": _rounded(
                sum(1 for value in returns if value > 0) / len(returns) * 100
            ),
            "best_return": _rounded(max(returns)),
            "worst_return": _rounded(min(returns)),
            "benchmark_sample_size": len(paired_benchmark),
            "average_benchmark_return": (
                _rounded(statistics.fmean(paired_benchmark))
                if paired_benchmark
                else None
            ),
            "average_excess_return": (
                _rounded(
                    statistics.fmean(paired_signal)
                    - statistics.fmean(paired_benchmark)
                )
                if paired_benchmark
                else None
            ),
        },
    }


def _progress(positions):
    if not positions:
        return {
            "sessions_elapsed": 0,
            "sessions_total": None,
            "percent_complete": 0.0,
        }
    elapsed = max(item.sessions_elapsed for item in positions)
    total = max(item.horizon_days for item in positions)
    return {
        "sessions_elapsed": elapsed,
        "sessions_total": total,
        "percent_complete": _rounded(
            min(100.0, elapsed / total * 100) if total else 0.0
        ),
    }


def _leaderboard(scored, side, *, winners, limit):
    """One side's best or worst names, never padded with the wrong sign.

    A "top returns" table that fills empty rows with losses to reach a fixed
    length would report a winner that is not one, so a short list here means
    the cohort genuinely has that few names in profit.
    """
    qualified = [
        item
        for item in scored
        if item.side == side
        and (item.signed_return > 0 if winners else item.signed_return < 0)
    ]
    qualified.sort(key=lambda item: item.signed_return, reverse=winners)
    return {
        "key": f"{side}_{'winners' if winners else 'losers'}",
        "label": f"{'Top' if winners else 'Worst'} {side} returns",
        "side": side,
        "winners": winners,
        "total": len(qualified),
        "rows": [_position_payload(item) for item in qualified[:limit]],
    }


def _side_breakdown(scored):
    groups = defaultdict(list)
    for item in scored:
        groups[item.side].append(item.signed_return)
    return [
        {
            "label": label,
            "sample_size": len(values),
            "average_return": _rounded(statistics.fmean(values)),
            "positive_share": _rounded(
                sum(1 for value in values if value > 0) / len(values) * 100
            ),
        }
        for label, values in sorted(groups.items())
    ]


def _benchmark_windows(bars, scored):
    """Equal-weight average move of every tracked symbol, per mark window.

    The comparator holds every symbol in the ledger over the identical window,
    so it answers "did picking these names beat owning all of them" rather than
    comparing against a different period or a different index.
    """
    windows = {(item.entry_date, item.mark_date) for item in scored}
    if not windows:
        return {}
    indexed = {
        symbol: (
            [bar[0] for bar in symbol_bars],
            symbol_bars,
        )
        for symbol, symbol_bars in bars.items()
    }
    results = {}
    for entry_date, mark_date in windows:
        moves = []
        for sessions, symbol_bars in indexed.values():
            entry_index = _exact_index(sessions, entry_date)
            mark_index = _exact_index(sessions, mark_date)
            if entry_index is None or mark_index is None:
                continue
            entry_price = symbol_bars[entry_index][1]
            mark_price = symbol_bars[mark_index][2]
            if entry_price is None or entry_price <= 0 or mark_price is None:
                continue
            if has_price_break(symbol_bars, entry_index, mark_index):
                continue
            moves.append(((mark_price / entry_price) - 1) * 100)
        results[(entry_date, mark_date)] = (
            statistics.fmean(moves) if moves else None
        )
    return results


def _exact_index(sessions, session_date):
    index = bisect_left(sessions, session_date)
    if index >= len(sessions) or sessions[index] != session_date:
        return None
    return index


def _position_payload(position):
    return {
        "symbol": position.symbol,
        "side": position.side,
        "tier": position.tier,
        "state": position.state,
        "entry_date": position.entry_date,
        "entry_price": position.entry_price,
        "mark_date": position.mark_date,
        "mark_price": position.mark_price,
        "sessions_elapsed": position.sessions_elapsed,
        "horizon_days": position.horizon_days,
        "price_return": position.price_return,
        "signed_return": position.signed_return,
    }


def _direction_of(observation):
    if observation.side == "long":
        return "bullish"
    if observation.side == "short":
        return "bearish"
    return "neutral"


def _rounded(value):
    return None if value is None else round(float(value), 4)
