import random
import statistics
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from provenance import content_hash


MINIMUM_HEADLINE_SAMPLE = 30
BOOTSTRAP_CONFIDENCE = 0.95
BOOTSTRAP_SAMPLES = 2_000

# Wide enough that live buckets fill in reasonable time, and matched to the
# bands the blind replay reported so forward drift is comparable to the fit.
# Edges are generated rather than written out: these group forecasts for
# display and must never read as a fixed decision threshold, which is what
# `_decision_hit` deliberately avoids by comparing against the base rate.
_BUCKET_EDGES = (0, *range(45, 71, 5), 100)
CALIBRATION_BUCKETS = tuple(zip(_BUCKET_EDGES, _BUCKET_EDGES[1:]))


@dataclass(frozen=True)
class ForecastObservation:
    id: int
    symbol: str
    as_of_date: str
    horizon_days: int
    horizon_date: str | None
    status: str
    realized_return: float | None
    outcome_date: str | None
    entry_date: str | None
    side: str | None
    tier: str
    probability_up: float | None
    baseline_up_rate: float | None
    universe_id: int | None
    data_vintage: str | None
    model_version: str | None = None
    # Optional and last: every forecast written before calibration shipped has
    # no stored value, and callers built before this field existed must keep
    # constructing an observation without it.
    calibrated_probability_up: float | None = None
    # None means the vintage predates the field, not that the name was rejected.
    # Only a recorded False is treated as "the board refused to publish this".
    eligible: bool | None = None

    @property
    def was_published(self):
        """Did the board actually show this name, as far as the record knows."""
        return self.eligible is not False

    @classmethod
    def from_record(cls, record):
        side = record.get("side")
        if side not in {"long", "short"}:
            direction = record.get("direction")
            side = (
                "long"
                if direction == "bullish"
                else "short"
                if direction == "bearish"
                else None
            )
        return cls(
            id=int(record["id"]),
            symbol=record["symbol"],
            as_of_date=record["as_of_date"],
            horizon_days=int(record["horizon_days"]),
            horizon_date=record.get("horizon_date"),
            status=record["status"],
            realized_return=_optional_float(record.get("realized_return")),
            outcome_date=record.get("outcome_date"),
            entry_date=record.get("entry_date"),
            side=side,
            tier=record.get("tier") or "unclassified",
            probability_up=_optional_float(record.get("probability_up")),
            baseline_up_rate=_optional_float(record.get("baseline_up_rate")),
            # Forecasts written before calibration shipped have no stored value.
            # Deriving one is only sound when the factor in force is also known,
            # so an unrecorded vintage stays None rather than being back-filled
            # against today's factor.
            calibrated_probability_up=_optional_float(
                record.get("calibrated_probability_up")
            ),
            eligible=(
                bool(record["eligible"])
                if record.get("eligible") is not None
                else None
            ),
            universe_id=(
                int(record["universe_id"])
                if record.get("universe_id") is not None
                else None
            ),
            data_vintage=record.get("data_vintage"),
            model_version=record.get("model_version"),
        )


def calibration_summary(matured, buckets=CALIBRATION_BUCKETS):
    """Measure the published probability against outcomes that already matured.

    This is the only calibration evidence that cannot be overfitted: every
    forecast here was written before its outcome existed. The panel in
    ``backtest/`` fitted the shrink factor; this reports whether the fit is
    holding on live forward data.

    The probability answers "does this finish higher", so the outcome is the raw
    realized return regardless of which side the board took.
    """
    graded = [
        item
        for item in matured
        if item.probability_up is not None and item.realized_return is not None
    ]
    if not graded:
        return {
            "available": False,
            "reason": "No matured forecast carries a recorded probability yet.",
        }

    outcomes = [1.0 if item.realized_return > 0 else 0.0 for item in graded]
    base_rate = sum(outcomes) / len(outcomes)

    def _brier(values):
        return sum(
            (probability / 100.0 - outcome) ** 2
            for probability, outcome in zip(values, outcomes)
        ) / len(outcomes)

    raw = _brier([item.probability_up for item in graded])
    reference = sum((base_rate - outcome) ** 2 for outcome in outcomes) / len(outcomes)

    published = [
        item.calibrated_probability_up
        if item.calibrated_probability_up is not None
        else item.probability_up
        for item in graded
    ]
    calibrated = _brier(published)
    recorded = sum(
        1 for item in graded if item.calibrated_probability_up is not None
    )

    rows = []
    for low, high in buckets:
        members = [
            (probability, outcome)
            for probability, outcome in zip(published, outcomes)
            if low <= probability < high
        ]
        if not members:
            continue
        predicted = sum(probability for probability, _ in members) / len(members)
        realized = sum(outcome for _, outcome in members) / len(members) * 100
        rows.append(
            {
                "bucket": f"{low}-{high}",
                "count": len(members),
                "predicted_up": _rounded(predicted),
                "realized_up": _rounded(realized),
                "gap_points": _rounded(realized - predicted),
            }
        )

    return {
        "available": True,
        "sample": len(graded),
        "calibrated_sample": recorded,
        "realized_up_rate": _rounded(base_rate * 100),
        "brier_raw": round(raw, 4),
        "brier_published": round(calibrated, 4),
        "brier_base_rate": round(reference, 4),
        # Every matured forecast resolved the same way, so a base rate of 0 or
        # 100% has nothing to be skilful against. Report no skill rather than
        # dividing by zero.
        "skill_raw_points": (
            _rounded(100 * (1 - raw / reference)) if reference > 0 else None
        ),
        "skill_published_points": (
            _rounded(100 * (1 - calibrated / reference)) if reference > 0 else None
        ),
        "buckets": rows,
    }


def direction_adjusted_return(side, realized_return):
    if realized_return is None:
        return None
    value = float(realized_return)
    if side == "long":
        return value
    if side == "short":
        return -value
    return None


def build_scorecard(
    observations,
    benchmark_returns=None,
    *,
    as_of_date,
    minimum_sample=MINIMUM_HEADLINE_SAMPLE,
    bootstrap_samples=BOOTSTRAP_SAMPLES,
):
    observations = list(observations)
    benchmark_returns = dict(benchmark_returns or {})
    matured = [
        item
        for item in observations
        if item.status == "graded" and item.realized_return is not None
    ]
    # The headline answers "how did the signals I was shown do", so names the
    # board refused are excluded from it. They stay in `counts` rather than
    # disappearing. A forecast whose vintage never recorded eligibility cannot
    # be filtered and is counted separately so the gap is visible.
    published = [item for item in matured if item.was_published]
    scored = [
        item
        for item in published
        if direction_adjusted_return(item.side, item.realized_return) is not None
    ]
    rejected = [item for item in matured if item.eligible is False]
    unknown_eligibility = [item for item in matured if item.eligible is None]
    expired = [
        item
        for item in observations
        if item.status == "pending"
        and item.horizon_date is not None
        and item.horizon_date < as_of_date
    ]
    # Identity lookup, not membership over a list: the ledger grows by the
    # size of the universe every scan, so an O(pending x expired) scan here
    # degrades the page as the record accumulates.
    expired_ids = {item.id for item in expired}
    pending = [
        item
        for item in observations
        if item.status == "pending" and item.id not in expired_ids
    ]
    counts = {
        "total": len(observations),
        "pending": len(pending),
        "matured": len(matured),
        "expired_ungraded": len(expired),
        "scored_matured": len(scored),
        "board_rejected": len(rejected),
        "eligibility_unrecorded": len(unknown_eligibility),
    }
    headline_metrics = _metric_summary(
        scored,
        benchmark_returns,
        minimum_sample=minimum_sample,
        bootstrap_samples=bootstrap_samples,
    )
    available = headline_metrics is not None
    headline = {
        "available": available,
        "minimum_sample": int(minimum_sample),
        "reason": (
            None
            if available
            else (
                f"Headline suppressed until {int(minimum_sample)} matured "
                "long/short forecasts are available."
            )
        ),
        "metrics": headline_metrics,
    }
    breakdowns = {
        "side": _breakdown(
            scored,
            benchmark_returns,
            lambda item: item.side or "unclassified",
            minimum_sample,
            bootstrap_samples,
        ),
        "tier": _breakdown(
            scored,
            benchmark_returns,
            lambda item: item.tier,
            minimum_sample,
            bootstrap_samples,
        ),
        "horizon": _breakdown(
            scored,
            benchmark_returns,
            lambda item: f"{item.horizon_days} sessions",
            minimum_sample,
            bootstrap_samples,
        ),
        "cohort": _breakdown(
            scored,
            benchmark_returns,
            lambda item: item.as_of_date[:7],
            minimum_sample,
            bootstrap_samples,
        ),
        # Forecasts recorded before the model measured the tradable window
        # predicted a different quantity than they are graded on, so the
        # record has to stay separable by the model that produced it.
        "model_version": _breakdown(
            scored,
            benchmark_returns,
            lambda item: item.model_version or "pre-provenance",
            minimum_sample,
            bootstrap_samples,
        ),
    }
    cohort_dates = sorted(item.as_of_date for item in observations)
    vintages = sorted(
        {item.data_vintage for item in observations if item.data_vintage}
    )
    return {
        "as_of_date": as_of_date,
        "counts": counts,
        "headline": headline,
        # Measured on every matured forecast, not just the long/short scored
        # subset: a neutral call still stated a probability and still resolved.
        "calibration": calibration_summary(matured),
        "breakdowns": breakdowns,
        "cohort_start": cohort_dates[0] if cohort_dates else None,
        "cohort_end": cohort_dates[-1] if cohort_dates else None,
        "data_vintage": content_hash(vintages),
        "limitations": (
            "This is an average per-forecast price move, not a portfolio "
            "return. It excludes costs, position sizing, capital limits, "
            "and overlap between simultaneous forecasts."
        ),
    }


class ScorecardService:
    def __init__(self, store):
        self.store = store
        self._cache_lock = threading.Lock()
        self._cache_key = None
        self._cache_value = None

    def current(self, as_of_date=None, as_of_timestamp=None):
        evaluation_date = as_of_date or datetime.now(
            ZoneInfo("America/New_York")
        ).date().isoformat()
        cutoff = _parse_timestamp(as_of_timestamp)
        cache_key = None
        fingerprint = getattr(self.store, "forecast_ledger_fingerprint", None)
        if fingerprint is not None:
            cache_key = (fingerprint(), evaluation_date, as_of_timestamp)
            with self._cache_lock:
                if cache_key == self._cache_key:
                    return self._cache_value
        report = self._build(evaluation_date, cutoff)
        if cache_key is not None:
            with self._cache_lock:
                self._cache_key = cache_key
                self._cache_value = report
        return report

    def _build(self, evaluation_date, cutoff):
        records = []
        for stored in self.store.list_all_forecasts():
            record = dict(stored)
            if record["as_of_date"] > evaluation_date:
                continue
            created_at = _parse_timestamp(record.get("created_at"))
            if cutoff is not None and created_at is not None and created_at > cutoff:
                continue
            if record.get("status") == "graded":
                graded_at = _parse_timestamp(record.get("graded_at"))
                outcome_date = record.get("outcome_date")
                outcome_was_available = (
                    outcome_date is not None
                    and outcome_date <= evaluation_date
                    and (
                        cutoff is None
                        or (graded_at is not None and graded_at <= cutoff)
                    )
                )
                if not outcome_was_available:
                    record.update(
                        status="pending",
                        realized_return=None,
                        realized_price=None,
                        outcome_date=None,
                        graded_at=None,
                    )
            records.append(record)
        observations = [
            ForecastObservation.from_record(record)
            for record in records
        ]
        benchmark_returns = {}
        cache = {}
        for item in observations:
            if (
                item.status != "graded"
                or item.side not in {"long", "short"}
                or item.universe_id is None
                or item.entry_date is None
                or item.outcome_date is None
            ):
                continue
            key = (item.universe_id, item.entry_date, item.outcome_date)
            if key not in cache:
                cache[key] = self.store.equal_weight_benchmark_return(*key)
            benchmark = cache[key]
            if benchmark["return"] is not None:
                benchmark_returns[item.id] = benchmark
        return build_scorecard(
            observations,
            benchmark_returns,
            as_of_date=evaluation_date,
        )


def _metric_summary(
    observations,
    benchmark_returns,
    *,
    minimum_sample,
    bootstrap_samples,
):
    if len(observations) < int(minimum_sample):
        return None
    signal_returns = [
        direction_adjusted_return(item.side, item.realized_return)
        for item in observations
    ]
    signal_returns = [value for value in signal_returns if value is not None]
    paired_benchmarks = []
    paired_signals = []
    paired_excess = []
    benchmark_coverage = []
    hits = []
    for item in observations:
        signal_return = direction_adjusted_return(
            item.side,
            item.realized_return,
        )
        benchmark = benchmark_returns.get(item.id)
        if isinstance(benchmark, dict):
            raw_benchmark = benchmark.get("return")
            constituent_count = benchmark.get("constituent_count")
            universe_count = benchmark.get("universe_count")
        else:
            raw_benchmark = benchmark
            constituent_count = None
            universe_count = None
        adjusted_benchmark = direction_adjusted_return(
            item.side,
            raw_benchmark,
        )
        if adjusted_benchmark is not None and signal_return is not None:
            paired_signals.append(signal_return)
            paired_benchmarks.append(adjusted_benchmark)
            paired_excess.append(signal_return - adjusted_benchmark)
            if (
                constituent_count is not None
                and universe_count is not None
                and int(universe_count) > 0
            ):
                benchmark_coverage.append(
                    float(constituent_count) / float(universe_count) * 100
                )
        hit = _decision_hit(item)
        if hit is not None:
            hits.append(hit)
    return {
        "sample_size": len(signal_returns),
        "mean_signal_return": _rounded(statistics.fmean(signal_returns)),
        "median_signal_return": _rounded(statistics.median(signal_returns)),
        "standard_deviation": _rounded(
            statistics.stdev(signal_returns) if len(signal_returns) > 1 else 0.0
        ),
        "bootstrap_interval": _bootstrap_interval(
            observations,
            bootstrap_samples,
        ),
        "hit_rate": (
            _rounded(statistics.fmean(hits) * 100) if hits else None
        ),
        "hit_sample_size": len(hits),
        "mean_benchmark_return": (
            _rounded(statistics.fmean(paired_benchmarks))
            if paired_benchmarks
            else None
        ),
        "mean_paired_signal_return": (
            _rounded(statistics.fmean(paired_signals))
            if paired_signals
            else None
        ),
        "benchmark_sample_size": len(paired_benchmarks),
        "benchmark_coverage_median": (
            _rounded(statistics.median(benchmark_coverage))
            if benchmark_coverage
            else None
        ),
        "benchmark_coverage_minimum": (
            _rounded(min(benchmark_coverage))
            if benchmark_coverage
            else None
        ),
        "mean_excess_return": (
            _rounded(statistics.fmean(paired_excess))
            if paired_excess
            else None
        ),
    }


def _decision_hit(item):
    if (
        item.probability_up is None
        or item.baseline_up_rate is None
        or item.realized_return is None
        or item.probability_up == item.baseline_up_rate
    ):
        return None
    predicted_up = item.probability_up > item.baseline_up_rate
    actual_up = item.realized_return > 0
    return 1.0 if predicted_up == actual_up else 0.0


def _breakdown(
    observations,
    benchmark_returns,
    key,
    minimum_sample,
    bootstrap_samples,
):
    groups = defaultdict(list)
    for item in observations:
        groups[str(key(item))].append(item)
    return [
        {
            "label": label,
            "sample_size": len(items),
            "metrics": _metric_summary(
                items,
                benchmark_returns,
                minimum_sample=minimum_sample,
                bootstrap_samples=bootstrap_samples,
            ),
        }
        for label, items in sorted(groups.items())
    ]


def _bootstrap_interval(observations, samples):
    if not observations:
        return None
    clusters = defaultdict(list)
    for item in observations:
        value = direction_adjusted_return(item.side, item.realized_return)
        if value is not None:
            clusters[item.as_of_date].append(value)
    labels = sorted(clusters)
    if not labels:
        return None
    rng = random.Random(17_290_811)
    estimates = []
    for _ in range(max(1, int(samples))):
        values = []
        for label in rng.choices(labels, k=len(labels)):
            values.extend(clusters[label])
        estimates.append(statistics.fmean(values))
    tail = (1.0 - BOOTSTRAP_CONFIDENCE) / 2.0
    return [
        _rounded(_quantile(estimates, tail)),
        _rounded(_quantile(estimates, 1.0 - tail)),
    ]


def _quantile(values, probability):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _optional_float(value):
    return float(value) if value is not None else None


def _parse_timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _rounded(value):
    return round(float(value), 4)
