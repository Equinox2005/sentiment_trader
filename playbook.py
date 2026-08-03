"""Explainable historical-analog forecasting engine.

The engine compares today's complete market state with independent historical
episodes, reports the paths that followed, and walk-forward tests the same
matching pipeline before presenting a forecast.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from calibration import (
    CALIBRATION_PROVENANCE,
    calibrate_probability,
    shrink_factor,
)


FEATURE_WARMUP = 260
FORWARD_DAYS = 21
SHAPE_DAYS = 21
RECENT_EXCLUSION = SHAPE_DAYS + FORWARD_DAYS
MATCH_SPACING = 42
MAX_MATCHES = 30
MIN_MATCHES = 8
GHOST_PATHS = 12
MAX_HISTORY_DAYS = 366 * 20
PRIOR_STRENGTH = 6.0
MAX_NEWS_ADJUSTMENT = 5.0
EVALUATION_STEP = 5
MAX_EVALUATION_RECORDS = 260

# Tradable entry convention. A signal formed from the completed close of
# session t is filled at the open of session t + 1 and exited at the close of
# session t + horizon. Analog outcomes, baseline rates, and walk-forward
# targets all measure that same window, so the prediction that is audited is
# the prediction that is displayed and later graded.
ENTRY_SESSION_OFFSET = 1


FEATURE_LABELS = {
    "r21": "1-month momentum",
    "r63": "3-month momentum",
    "rsi": "RSI pressure",
    "trend_short": "Short trend",
    "trend_medium": "Medium trend",
    "trend_long": "Long trend",
    "vol21": "Current volatility",
    "vol_ratio": "Volatility shift",
    "drawdown": "Distance from high",
    "atr": "Daily range",
    "candle_pressure": "Buying pressure",
    "volume_ratio": "Volume surge",
    "market_r21": "Market momentum",
    "market_trend": "Market trend",
    "vix_percentile": "Market fear",
}


WEIGHT_PROFILES = {
    "balanced": {
        "label": "Balanced",
        "shape": 1.25,
        "weights": {
            "r21": 1.1, "r63": 0.8, "rsi": 0.55,
            "trend_short": 0.75, "trend_medium": 0.85, "trend_long": 1.0,
            "vol21": 0.7, "vol_ratio": 0.8, "drawdown": 0.85,
            "atr": 0.45, "candle_pressure": 0.35, "volume_ratio": 0.35,
            "market_r21": 0.8, "market_trend": 0.9, "vix_percentile": 0.65,
        },
    },
    "price_structure": {
        "label": "Price structure",
        "shape": 1.7,
        "weights": {
            "r21": 1.35, "r63": 1.0, "rsi": 0.55,
            "trend_short": 1.1, "trend_medium": 1.2, "trend_long": 1.25,
            "vol21": 0.45, "vol_ratio": 0.45, "drawdown": 0.7,
            "atr": 0.3, "candle_pressure": 0.25, "volume_ratio": 0.2,
            "market_r21": 0.45, "market_trend": 0.55, "vix_percentile": 0.35,
        },
    },
    "regime": {
        "label": "Market regime",
        "shape": 0.8,
        "weights": {
            "r21": 0.65, "r63": 0.75, "rsi": 0.35,
            "trend_short": 0.55, "trend_medium": 0.75, "trend_long": 1.35,
            "vol21": 1.15, "vol_ratio": 1.2, "drawdown": 1.1,
            "atr": 0.7, "candle_pressure": 0.2, "volume_ratio": 0.3,
            "market_r21": 1.15, "market_trend": 1.35, "vix_percentile": 1.2,
        },
    },
    "flow": {
        "label": "Price and participation",
        "shape": 1.1,
        "weights": {
            "r21": 0.9, "r63": 0.6, "rsi": 0.75,
            "trend_short": 0.9, "trend_medium": 0.8, "trend_long": 0.7,
            "vol21": 0.55, "vol_ratio": 0.7, "drawdown": 0.6,
            "atr": 0.8, "candle_pressure": 1.25, "volume_ratio": 1.3,
            "market_r21": 0.45, "market_trend": 0.5, "vix_percentile": 0.4,
        },
    },
}


@dataclass(frozen=True)
class PreparedMatrices:
    feature_names: tuple
    feature_columns: dict
    feature_values: np.ndarray
    shapes: np.ndarray
    open: np.ndarray
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    dates: np.ndarray


def build_playbook(
    history,
    context=None,
    news_score=0.0,
    news_count=0,
    earnings_at=None,
    include_validation=True,
):
    """Build a historical analog forecast from OHLCV market history."""
    frame = _normalize_history(history)
    config = _sampling_config(frame)
    if len(frame) < config["warmup"] + config["recent_exclusion"] + 10:
        return {
            "available": False,
            "reason": (
                "This symbol does not have enough trading history yet. "
                "Playbook needs at least about 15 months of daily prices."
            ),
        }

    features = _compute_features(frame, context, config)
    shapes = _compute_shapes(frame["Close"], config["shape_days"])
    prepared = _prepare_matrices(frame, features, shapes)
    today_position = len(frame) - 1
    current_features = features.iloc[today_position]
    if current_features.dropna().shape[0] < 8:
        return {
            "available": False,
            "reason": "Today's setup could not be measured reliably from the available data.",
        }

    if include_validation:
        (
            profile_key,
            profile_selection,
            calibration_predictions,
        ) = _select_weight_profile(
            frame, features, shapes, config, prepared
        )
    else:
        profile_key = "balanced"
        profile_selection = {
            "tuning_forecasts": 0,
            "profiles_tested": 0,
            "reason": "Preliminary forecast; adaptive audit is loading.",
        }
        calibration_predictions = []
    matches = _rank_matches(
        frame,
        features,
        shapes,
        today_position,
        profile_key,
        include_paths=True,
        config=config,
        prepared=prepared,
    )
    if len(matches) < MIN_MATCHES:
        return {
            "available": False,
            "reason": (
                f"Only {len(matches)} independent comparable setups were found. "
                f"Playbook requires at least {MIN_MATCHES} before making a forecast."
            ),
        }

    validation = (
        _walk_forward_validation(
            frame,
            features,
            shapes,
            profile_key,
            profile_selection,
            config,
            prepared,
            calibration_predictions,
        )
        if include_validation
        else {
            "available": False,
            "pending": True,
            "reason": "Walk-forward audit is loading.",
            "selection": profile_selection,
        }
    )
    summary = _summarize_matches(matches)
    forecast = _build_forecast(
        summary, news_score, news_count, validation, config
    )
    verdict = _build_verdict(forecast, summary)
    plan = _build_trade_plan(
        float(frame["Close"].iloc[-1]),
        matches,
        summary,
        verdict,
        config,
    )
    regime = _classify_regime(current_features)
    catalyst = _catalyst_risk(earnings_at)

    return {
        "available": True,
        "preliminary": not include_validation,
        "setup": _describe_setup(current_features, regime),
        "fingerprint": _build_fingerprint(current_features, regime, features),
        "matching": {
            "profile": WEIGHT_PROFILES[profile_key]["label"],
            "profile_key": profile_key,
            "features_used": _features_used(current_features, profile_key),
            "shape_window_days": config["shape_days"],
            "independence_days": config["spacing"],
            "sampling": config["sampling"],
            "candidate_years": _calendar_years(frame.index),
            "match_count": len(matches),
            "median_quality": round(_weighted_quantile(
                [match["quality"] for match in matches],
                [match["weight"] for match in matches],
                0.5,
            )),
            "explanation": (
                (
                    "Weights were selected on older walk-forward forecasts. "
                    "The latest validation period was kept untouched."
                )
                if include_validation
                else "Balanced preliminary weights are shown while the adaptive audit loads."
            ),
        },
        "forecast": forecast,
        "verdict": verdict,
        "stats": summary["public"],
        "validation": validation,
        "trade_plan": plan,
        "projection": _projection(
            matches,
            config,
            validation.get("conformal", {}).get("adjustment_points", 0.0),
        ),
        "ghost_paths": [
            {
                "date": _date_string(match["date"]),
                "quality": match["quality"],
                "offsets": [round(value, 3) for value in match["path"]],
            }
            for match in matches[:GHOST_PATHS]
        ],
        "matches": [_public_match(match) for match in matches],
        "catalyst": catalyst,
    }


def _normalize_history(history):
    if isinstance(history, pd.Series):
        frame = pd.DataFrame({"Close": history})
    elif isinstance(history, pd.DataFrame):
        frame = history.copy()
    else:
        frame = pd.DataFrame(history)

    if "Close" not in frame:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    close = pd.to_numeric(frame["Close"], errors="coerce")
    normalized = pd.DataFrame(index=frame.index)
    normalized["Close"] = close
    for column in ("Open", "High", "Low"):
        normalized[column] = pd.to_numeric(
            frame[column] if column in frame else close,
            errors="coerce",
        ).fillna(close)
    normalized["Volume"] = pd.to_numeric(
        frame["Volume"] if "Volume" in frame else float("nan"),
        errors="coerce",
    )
    normalized = normalized[normalized["Close"].gt(0)].sort_index()
    normalized = normalized[~normalized.index.duplicated(keep="last")]
    if isinstance(normalized.index, pd.DatetimeIndex) and not normalized.empty:
        cutoff = normalized.index[-1] - pd.DateOffset(years=20)
        normalized = normalized[normalized.index >= cutoff]
    return normalized.tail(MAX_HISTORY_DAYS)


def _sampling_config(frame):
    calendar_daily = False
    if isinstance(frame.index, pd.DatetimeIndex) and len(frame) >= 60:
        weekend_share = float((frame.index.dayofweek >= 5).mean())
        calendar_daily = weekend_share >= 0.15
    if calendar_daily:
        forward = 30
        shape = 30
        return {
            "sampling": "calendar_daily",
            "horizon_days": forward,
            "horizon_label": "30 calendar days",
            "month": 30,
            "quarter": 90,
            "year": 365,
            "short_trend": 30,
            "medium_trend": 75,
            "long_trend": 300,
            "vol_short": 30,
            "vol_long": 90,
            "annualizer": 365,
            "candle_window": 7,
            "volume_short": 7,
            "volume_long": 30,
            "shape_days": shape,
            "warmup": 380,
            "recent_exclusion": shape + forward,
            "spacing": 60,
        }
    return {
        "sampling": "trading_sessions",
        "horizon_days": FORWARD_DAYS,
        "horizon_label": "21 trading days",
        "month": 21,
        "quarter": 63,
        "year": 252,
        "short_trend": 20,
        "medium_trend": 50,
        "long_trend": 200,
        "vol_short": 21,
        "vol_long": 63,
        "annualizer": 252,
        "candle_window": 5,
        "volume_short": 5,
        "volume_long": 20,
        "shape_days": SHAPE_DAYS,
        "warmup": FEATURE_WARMUP,
        "recent_exclusion": RECENT_EXCLUSION,
        "spacing": MATCH_SPACING,
    }


def _compute_features(frame, context=None, config=None):
    config = config or _sampling_config(frame)
    close = frame["Close"]
    returns = close.pct_change()
    sma20 = close.rolling(config["short_trend"]).mean()
    sma50 = close.rolling(config["medium_trend"]).mean()
    sma200 = close.rolling(config["long_trend"]).mean()

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = losses.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_loss == 0) & (avg_gain == 0), 50.0)

    high = frame["High"]
    low = frame["Low"]
    open_price = frame["Open"]
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    candle_range = (high - low).replace(0, float("nan"))
    candle_pressure = (
        ((close - open_price) / candle_range)
        .rolling(config["candle_window"])
        .mean()
        * 100
    )

    volume = frame["Volume"].where(frame["Volume"].gt(0))
    volume_ratio = (
        volume.rolling(config["volume_short"]).mean()
        / volume.rolling(config["volume_long"]).mean()
        - 1
    ) * 100
    vol21 = (
        returns.rolling(config["vol_short"]).std()
        * math.sqrt(config["annualizer"])
        * 100
    )
    vol63 = (
        returns.rolling(config["vol_long"]).std()
        * math.sqrt(config["annualizer"])
        * 100
    )

    features = pd.DataFrame(
        {
            "r21": close.pct_change(config["month"]) * 100,
            "r63": close.pct_change(config["quarter"]) * 100,
            "rsi": rsi,
            "trend_short": (close / sma20 - 1) * 100,
            "trend_medium": (sma20 / sma50 - 1) * 100,
            "trend_long": (sma50 / sma200 - 1) * 100,
            "vol21": vol21,
            "vol_ratio": (vol21 / vol63 - 1) * 100,
            "drawdown": (
                close
                / close.rolling(
                    config["year"],
                    min_periods=max(60, config["year"] // 4),
                ).max()
                - 1
            ) * 100,
            "atr": true_range.rolling(14).mean() / close * 100,
            "candle_pressure": candle_pressure,
            "volume_ratio": volume_ratio,
        },
        index=frame.index,
    )

    aligned_context = _align_context(context, frame.index)
    if aligned_context is not None:
        market = aligned_context.get("Market")
        if market is not None:
            market = pd.to_numeric(market, errors="coerce").where(lambda value: value > 0)
            features["market_r21"] = market.pct_change(
                config["month"], fill_method=None
            ) * 100
            features["market_trend"] = (
                market / market.rolling(config["long_trend"]).mean() - 1
            ) * 100
        vix = aligned_context.get("VIX")
        if vix is not None:
            vix = pd.to_numeric(vix, errors="coerce").where(lambda value: value > 0)
            features["vix_percentile"] = (
                vix.rolling(
                    config["year"],
                    min_periods=max(60, config["year"] // 4),
                ).rank(pct=True) * 100
            )
    return features


def _align_context(context, target_index):
    if context is None or not isinstance(context, pd.DataFrame) or context.empty:
        return None
    aligned = context.copy()
    try:
        source_dates = pd.DatetimeIndex(
            [pd.Timestamp(value).date() for value in aligned.index]
        )
        target_dates = pd.DatetimeIndex(
            [pd.Timestamp(value).date() for value in target_index]
        )
    except (TypeError, ValueError):
        return None
    aligned.index = source_dates
    aligned = aligned[~aligned.index.duplicated(keep="last")].sort_index()
    # A previous US session is available at every global market's close.
    # Same-date US closes would leak future context into earlier-closing markets.
    aligned = aligned.shift(1)
    aligned = aligned.reindex(target_dates, method="ffill")
    aligned.index = target_index
    return aligned


def _compute_shapes(close, shape_days=SHAPE_DAYS):
    values = close.to_numpy(dtype=float)
    shapes = np.full((len(values), shape_days + 1), np.nan, dtype=float)
    if len(values) <= shape_days:
        return shapes
    windows = np.lib.stride_tricks.sliding_window_view(
        values, shape_days + 1
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        paths = np.log(windows / windows[:, :1])
    means = np.nanmean(paths, axis=1, keepdims=True)
    spreads = np.nanstd(paths, axis=1, keepdims=True)
    valid = (
        np.isfinite(paths).all(axis=1)
        & np.isfinite(spreads[:, 0])
        & (spreads[:, 0] >= 1e-9)
    )
    normalized = np.zeros_like(paths, dtype=np.float64)
    np.divide(
        paths - means,
        spreads,
        out=normalized,
        where=valid[:, None],
    )
    normalized[~np.isfinite(paths).all(axis=1)] = np.nan
    shapes[shape_days:] = normalized
    return shapes


def _entry_price_at(opens, closes, position):
    """Price of the first session that a signal at ``position`` could reach.

    A signal formed from the completed close of session ``t`` cannot be filled
    at that close, so every forward return in this module is measured from the
    open of session ``t + 1``. The close of that same session is used when an
    open is missing so the entry still lands strictly after the signal bar.
    """
    entry_position = position + ENTRY_SESSION_OFFSET
    if entry_position >= len(closes):
        return None
    for candidate in (opens[entry_position], closes[entry_position]):
        value = float(candidate)
        if np.isfinite(value) and value > 0:
            return value
    return None


def _prepare_matrices(frame, features, shapes):
    names = tuple(features.columns)
    return PreparedMatrices(
        feature_names=names,
        feature_columns={name: index for index, name in enumerate(names)},
        feature_values=np.ascontiguousarray(
            features.to_numpy(dtype=np.float64), dtype=np.float64
        ),
        shapes=np.ascontiguousarray(shapes, dtype=np.float64),
        open=np.ascontiguousarray(
            frame["Open"].to_numpy(dtype=np.float64)
        ),
        close=np.ascontiguousarray(
            frame["Close"].to_numpy(dtype=np.float64)
        ),
        high=np.ascontiguousarray(
            frame["High"].to_numpy(dtype=np.float64)
        ),
        low=np.ascontiguousarray(
            frame["Low"].to_numpy(dtype=np.float64)
        ),
        dates=np.asarray(frame.index, dtype=object),
    )


def _select_weight_profile(frame, features, shapes, config, prepared=None):
    prepared = prepared or _prepare_matrices(frame, features, shapes)
    tuning, _evaluation = _audit_anchors(len(frame), config)
    if len(tuning) < 10:
        return (
            "balanced",
            {
                "tuning_forecasts": len(tuning),
                "profiles_tested": 1,
                "reason": (
                    "Not enough older checkpoints to separate profile selection "
                    "from interval calibration."
                ),
            },
            [],
        )

    calibration_size = max(5, len(tuning) // 3)
    profile_anchors = tuning[:-calibration_size]
    calibration_anchors = tuning[-calibration_size:]
    scores = {}
    for profile_key in WEIGHT_PROFILES:
        predictions = _historical_predictions(
            frame,
            features,
            shapes,
            profile_key,
            profile_anchors,
            config,
            prepared,
        )
        scores[profile_key] = _brier_score(predictions)

    selected = min(
        scores,
        key=lambda key: scores[key] if scores[key] is not None else float("inf"),
    )
    if scores[selected] is None:
        selected = "balanced"
    return (
        selected,
        {
            "tuning_forecasts": len(profile_anchors),
            "interval_calibration_forecasts": len(calibration_anchors),
            "profiles_tested": len(WEIGHT_PROFILES),
            "profile_brier": {
                key: round(value, 4) if value is not None else None
                for key, value in scores.items()
            },
            "tuning_period": {
                "start": _date_string(frame.index[profile_anchors[0]]),
                "end": _date_string(frame.index[profile_anchors[-1]]),
            },
            "interval_calibration_period": {
                "start": _date_string(frame.index[calibration_anchors[0]]),
                "end": _date_string(frame.index[calibration_anchors[-1]]),
            },
        },
        _historical_predictions(
            frame,
            features,
            shapes,
            selected,
            calibration_anchors,
            config,
            prepared,
        ),
    )


def _walk_forward_validation(
    frame,
    features,
    shapes,
    profile_key,
    selection,
    config,
    prepared=None,
    calibration_predictions=None,
):
    prepared = prepared or _prepare_matrices(frame, features, shapes)
    tuning, evaluation = _audit_anchors(len(frame), config)
    predictions = _historical_predictions(
        frame, features, shapes, profile_key, evaluation, config, prepared
    )
    if len(predictions) < 5:
        return {
            "available": False,
            "reason": "Not enough untouched historical checkpoints for a reliability check.",
            "selection": selection,
        }

    correct = sum(
        (item["probability_up"] >= 0.5) == item["actual_up"]
        for item in predictions
    )
    baseline_correct = sum(
        (item["baseline_up_rate"] >= 0.5) == item["actual_up"]
        for item in predictions
    )
    actionable = [
        item for item in predictions
        if abs(item["edge"]) >= 0.04 and abs(item["median_return"]) >= 0.5
    ]
    actionable_correct = sum(
        (item["probability_up"] >= 0.5) == item["actual_up"]
        for item in actionable
    )
    model_brier = _brier_score(predictions)
    baseline_brier = sum(
        (item["baseline_up_rate"] - float(item["actual_up"])) ** 2
        for item in predictions
    ) / len(predictions)
    accuracy = correct / len(predictions)
    baseline_accuracy = baseline_correct / len(predictions)
    independent = _non_overlapping_predictions(predictions, config)
    independent_correct = sum(
        (item["probability_up"] >= 0.5) == item["actual_up"]
        for item in independent
    )
    interval = _wilson_interval(independent_correct, len(independent))
    brier_skill = (
        (1 - model_brier / baseline_brier) * 100
        if baseline_brier > 1e-12
        else 0.0
    )
    if calibration_predictions is None:
        calibration_predictions = _historical_predictions(
            frame,
            features,
            shapes,
            profile_key,
            tuning,
            config,
            prepared,
        )
    conformal = _conformal_diagnostics(
        calibration_predictions,
        predictions,
    )

    if len(predictions) < 12:
        grade = "limited"
        label = "Early evidence"
    elif model_brier < baseline_brier * 0.95 and accuracy > baseline_accuracy:
        grade = "positive"
        label = "Historically useful"
    elif model_brier <= baseline_brier * 1.05:
        grade = "mixed"
        label = "Mixed evidence"
    else:
        grade = "weak"
        label = "No proven edge"

    return {
        "available": True,
        "grade": grade,
        "label": label,
        "sample_size": len(predictions),
        "independent_sample_size": len(independent),
        "correct": correct,
        "accuracy": round(accuracy * 100),
        "accuracy_low": round(interval[0] * 100),
        "accuracy_high": round(interval[1] * 100),
        "baseline_accuracy": round(baseline_accuracy * 100),
        "brier": round(model_brier, 3),
        "baseline_brier": round(baseline_brier, 3),
        "brier_skill": round(brier_skill, 1),
        "conformal": conformal,
        "actionable_count": len(actionable),
        "actionable_accuracy": (
            round(actionable_correct / len(actionable) * 100)
            if actionable else None
        ),
        "evaluation_frequency_sessions": EVALUATION_STEP,
        "evaluation_period": {
            "start": _date_string(frame.index[evaluation[0]]),
            "end": _date_string(
                frame.index[evaluation[-1] + config["horizon_days"]]
            ),
        },
        "calibration": _calibration_buckets(predictions),
        "edge_strata": _prediction_strata(
            predictions,
            (
                ("Under 4 pts", lambda item: abs(item["edge"]) < 0.04),
                (
                    "4-8 pts",
                    lambda item: 0.04 <= abs(item["edge"]) < 0.08,
                ),
                ("8+ pts", lambda item: abs(item["edge"]) >= 0.08),
            ),
        ),
        "regime_strata": _prediction_strata(
            predictions,
            tuple(
                (
                    label,
                    lambda item, expected=label: (
                        item["regime"]["trend"] == expected
                    ),
                )
                for label in ("Uptrend", "Transition", "Downtrend")
            ),
        ),
        "strategy": _strategy_audit(predictions, config, frame),
        "records": [
            _public_evaluation_record(item)
            for item in predictions
        ],
        "selection": selection,
        "explanation": (
            "Each checkpoint used only information available on that date. "
            "Weight tuning used an older period; these five-session records use "
            "the newer untouched period. Because adjacent outcomes overlap, the "
            "95% accuracy range uses only non-overlapping checkpoints."
        ),
    }


def _validation_anchors(length, config):
    tuning, evaluation = _audit_anchors(length, config)
    return tuning + evaluation


def _audit_anchors(length, config):
    first = (
        config["warmup"]
        + config["recent_exclusion"]
        + MIN_MATCHES * config["spacing"]
    )
    last = length - config["horizon_days"] - 1
    if last <= first:
        return [], []

    minimum_evaluation_span = (
        config["horizon_days"] + EVALUATION_STEP * 12
    )
    latest_split = last - minimum_evaluation_span
    if latest_split <= first:
        return [], []
    available_span = last - first
    split = min(
        first + max(config["spacing"] * 5, round(available_span * 0.4)),
        latest_split,
    )
    tuning = list(range(first, split + 1, config["spacing"]))[-30:]
    evaluation_start = split + config["horizon_days"]
    evaluation = list(
        range(evaluation_start, last + 1, EVALUATION_STEP)
    )[-MAX_EVALUATION_RECORDS:]
    return tuning, evaluation


def _historical_predictions(
    frame, features, shapes, profile_key, anchors, config, prepared=None
):
    prepared = prepared or _prepare_matrices(frame, features, shapes)
    predictions = []
    close = frame["Close"]
    match_sets = _rank_matches_batch(
        frame,
        features,
        shapes,
        anchors,
        profile_key,
        include_paths=False,
        config=config,
        prepared=prepared,
    )
    for target_position in anchors:
        matches = match_sets.get(target_position, [])
        if len(matches) < MIN_MATCHES:
            continue
        summary = _summarize_matches(matches)
        entry_price = _entry_price_at(
            frame["Open"].to_numpy(dtype=float),
            close.to_numpy(dtype=float),
            target_position,
        )
        if entry_price is None:
            continue
        actual_return = (
            float(close.iloc[target_position + config["horizon_days"]])
            / entry_price
            - 1
        ) * 100
        predictions.append(
            {
                "position": target_position,
                "date": frame.index[target_position],
                "horizon_date": frame.index[
                    target_position + config["horizon_days"]
                ],
                "probability_up": summary["analog_probability_up"] / 100,
                "baseline_up_rate": summary["baseline_up_rate"] / 100,
                "edge": (
                    summary["analog_probability_up"] - summary["baseline_up_rate"]
                ) / 100,
                "median_return": summary["public"]["median_21d"],
                "actual_return": actual_return,
                "actual_up": actual_return > 0,
                "interval_low": summary["public"]["p20_21d"],
                "interval_high": summary["public"]["p80_21d"],
                "regime": _classify_regime(features.iloc[target_position]),
                "match_count": summary["public"]["count"],
                "effective_matches": summary["public"]["effective_matches"],
            }
        )
    return predictions


def _non_overlapping_predictions(predictions, config):
    selected = []
    next_position = -1
    for item in predictions:
        if item["position"] < next_position:
            continue
        selected.append(item)
        next_position = item["position"] + config["horizon_days"]
    return selected


def _calibration_buckets(predictions):
    buckets = (
        ("Under 40%", 0.0, 0.4),
        ("40-50%", 0.4, 0.5),
        ("50-60%", 0.5, 0.6),
        ("60%+", 0.6, 1.000001),
    )
    result = []
    for label, low, high in buckets:
        items = [
            item
            for item in predictions
            if low <= item["probability_up"] < high
        ]
        if not items:
            continue
        predicted = sum(item["probability_up"] for item in items) / len(items)
        observed = sum(item["actual_up"] for item in items) / len(items)
        result.append(
            {
                "label": label,
                "count": len(items),
                "predicted_up": round(predicted * 100, 1),
                "observed_up": round(observed * 100, 1),
                "gap_points": round((predicted - observed) * 100, 1),
            }
        )
    return result


def _conformal_diagnostics(calibration, evaluation, target_coverage=0.80):
    if len(calibration) < 5 or not evaluation:
        return {
            "available": False,
            "reason": "Not enough older forecasts to calibrate interval coverage.",
            "adjustment_points": 0.0,
        }
    scores = sorted(
        max(
            item["interval_low"] - item["actual_return"],
            item["actual_return"] - item["interval_high"],
            0.0,
        )
        for item in calibration
    )
    rank = math.ceil((len(scores) + 1) * target_coverage) - 1
    adjustment = scores[min(len(scores) - 1, max(0, rank))]
    adjustment = math.ceil(adjustment * 100) / 100
    raw_covered = sum(
        item["interval_low"]
        <= item["actual_return"]
        <= item["interval_high"]
        for item in evaluation
    )
    adjusted_covered = sum(
        item["interval_low"] - adjustment
        <= item["actual_return"]
        <= item["interval_high"] + adjustment
        for item in evaluation
    )
    raw_width = sum(
        item["interval_high"] - item["interval_low"]
        for item in evaluation
    ) / len(evaluation)
    return {
        "available": True,
        "target_coverage": round(target_coverage * 100),
        "calibration_sample": len(calibration),
        "evaluation_sample": len(evaluation),
        "raw_coverage": round(raw_covered / len(evaluation) * 100),
        "adjusted_coverage": round(
            adjusted_covered / len(evaluation) * 100
        ),
        "adjustment_points": adjustment,
        "average_raw_width": round(raw_width, 2),
        "average_adjusted_width": round(raw_width + adjustment * 2, 2),
        "method": (
            "Split conformal expansion calibrated only on the older tuning "
            "period, then measured on untouched evaluation forecasts."
        ),
    }


def _prediction_strata(predictions, definitions):
    result = []
    for label, predicate in definitions:
        items = [item for item in predictions if predicate(item)]
        if not items:
            continue
        correct = sum(
            (item["probability_up"] >= 0.5) == item["actual_up"]
            for item in items
        )
        brier = _brier_score(items)
        result.append(
            {
                "label": label,
                "count": len(items),
                "accuracy": round(correct / len(items) * 100),
                "brier": round(brier, 3),
                "average_edge_points": round(
                    sum(abs(item["edge"]) for item in items)
                    / len(items)
                    * 100,
                    1,
                ),
                "average_return": round(
                    sum(item["actual_return"] for item in items)
                    / len(items),
                    2,
                ),
            }
        )
    return result


def _historical_signal(item):
    if item["edge"] >= 0.04 and item["median_return"] > 0.5:
        return "bullish"
    if item["edge"] <= -0.04 and item["median_return"] < -0.5:
        return "bearish"
    return "neutral"


def _strategy_audit(predictions, config, frame):
    periods = _non_overlapping_predictions(predictions, config)
    if not periods:
        return {
            "available": False,
            "periods": 0,
            "trades": 0,
            "curve": [],
            "hold_curve": [],
        }

    start_position = periods[0]["position"]
    end_position = (
        periods[-1]["position"] + config["horizon_days"]
    )
    close = frame["Close"].to_numpy(dtype=float)
    opens = frame["Open"].to_numpy(dtype=float)
    held = np.zeros(end_position - start_position + 1, dtype=bool)
    # Offset -> fill price, so the first held session is marked from the open
    # it was actually entered at rather than from the signal close.
    entry_prices = {}
    trades = 0
    wins = 0
    for item in periods:
        if _historical_signal(item) != "bullish":
            continue
        entry_price = _entry_price_at(opens, close, item["position"])
        if entry_price is None:
            continue
        trades += 1
        wins += item["actual_return"] > 0
        entry_prices[item["position"] - start_position + 1] = entry_price
        interval_start = item["position"] - start_position + 1
        interval_end = (
            item["position"]
            - start_position
            + config["horizon_days"]
            + 1
        )
        held[interval_start:interval_end] = True

    strategy_equity = 100.0
    start_date = _date_string(frame.index[start_position])
    strategy_curve = [{"date": start_date, "value": 100.0}]
    hold_curve = [{"date": start_date, "value": 100.0}]
    for position in range(start_position + 1, end_position + 1):
        offset = position - start_position
        if held[offset]:
            basis = entry_prices.get(offset, close[position - 1])
            strategy_equity *= close[position] / basis
        hold_equity = close[position] / close[start_position] * 100
        date = _date_string(frame.index[position])
        strategy_curve.append(
            {"date": date, "value": round(strategy_equity, 2)}
        )
        hold_curve.append({"date": date, "value": round(hold_equity, 2)})

    hold_equity = hold_curve[-1]["value"]
    strategy_drawdown = _curve_drawdown(strategy_curve)
    hold_drawdown = _curve_drawdown(hold_curve)
    return {
        "available": True,
        "periods": len(periods),
        "trades": trades,
        "trade_win_rate": round(wins / trades * 100) if trades else None,
        "exposure": round(float(held[1:].mean()) * 100),
        "strategy_return": round(strategy_equity - 100, 1),
        "hold_return": round(hold_equity - 100, 1),
        "excess_return": round(strategy_equity - hold_equity, 1),
        "max_drawdown": strategy_drawdown,
        "hold_max_drawdown": hold_drawdown,
        "curve": _downsample_curve(strategy_curve),
        "hold_curve": _downsample_curve(hold_curve),
        "note": (
            "Entered at the session open after each signal. "
            "Daily mark-to-market, non-overlapping long-or-cash signals versus "
            "continuous buy-and-hold; excludes fees, spreads, and taxes."
        ),
    }


def _curve_drawdown(curve):
    peak = 100.0
    worst = 0.0
    for point in curve:
        value = point["value"]
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value / peak - 1) * 100)
    return round(worst, 1)


def _downsample_curve(curve, max_points=120):
    if len(curve) <= max_points:
        return curve
    positions = np.linspace(
        0,
        len(curve) - 1,
        max_points,
        dtype=int,
    )
    return [curve[position] for position in np.unique(positions)]


def _public_evaluation_record(item):
    signal = _historical_signal(item)
    if signal == "bullish":
        signal_correct = item["actual_return"] > 0
    elif signal == "bearish":
        signal_correct = item["actual_return"] < 0
    else:
        signal_correct = None
    return {
        "date": _date_string(item["date"]),
        "horizon_date": _date_string(item["horizon_date"]),
        "probability_up": round(item["probability_up"] * 100, 1),
        "baseline_up_rate": round(item["baseline_up_rate"] * 100, 1),
        "edge_points": round(item["edge"] * 100, 1),
        "median_return": round(item["median_return"], 2),
        "actual_return": round(item["actual_return"], 2),
        "actual_up": item["actual_up"],
        "probability_correct": (
            (item["probability_up"] >= 0.5) == item["actual_up"]
        ),
        "signal": signal,
        "signal_correct": signal_correct,
        "regime": item["regime"]["label"],
        "match_count": item["match_count"],
        "effective_matches": item["effective_matches"],
    }


def _rank_matches_batch(
    frame,
    features,
    shapes,
    target_positions,
    profile_key,
    include_paths,
    config,
    prepared=None,
):
    prepared = prepared or _prepare_matrices(frame, features, shapes)
    return {
        target_position: _rank_matches(
            frame,
            features,
            shapes,
            target_position,
            profile_key,
            include_paths=include_paths,
            config=config,
            prepared=prepared,
        )
        for target_position in target_positions
    }


def _rank_matches(
    frame,
    features,
    shapes,
    target_position,
    profile_key,
    include_paths,
    config=None,
    prepared=None,
):
    config = config or _sampling_config(frame)
    prepared = prepared or _prepare_matrices(frame, features, shapes)
    candidate_end = target_position - config["recent_exclusion"]
    if candidate_end <= config["warmup"]:
        return []
    positions = np.arange(config["warmup"], candidate_end + 1, dtype=int)
    profile = WEIGHT_PROFILES[profile_key]
    target_values_all = prepared.feature_values[target_position]

    usable = []
    usable_indices = []
    feature_weights = []
    for name, weight in profile["weights"].items():
        column = prepared.feature_columns.get(name)
        if column is None or not np.isfinite(target_values_all[column]):
            continue
        coverage = np.isfinite(
            prepared.feature_values[positions, column]
        ).mean()
        if coverage >= 0.75:
            usable.append(name)
            usable_indices.append(column)
            feature_weights.append(weight)
    if len(usable) < 6:
        return []

    candidate_values = prepared.feature_values[
        np.ix_(positions, np.asarray(usable_indices, dtype=int))
    ]
    target_values = target_values_all[usable_indices]
    medians = np.nanmedian(candidate_values, axis=0)
    deviations = np.nanmedian(np.abs(candidate_values - medians), axis=0) * 1.4826
    standard = np.nanstd(candidate_values, axis=0)
    scales = np.where(
        np.isfinite(deviations) & (deviations > 1e-9),
        deviations,
        np.where(np.isfinite(standard) & (standard > 1e-9), standard, 1.0),
    )
    normalized_candidates = np.clip(
        (candidate_values - medians) / scales, -4.0, 4.0
    )
    normalized_target = np.clip((target_values - medians) / scales, -4.0, 4.0)

    weights = np.asarray(feature_weights, dtype=float)
    valid = np.isfinite(normalized_candidates)
    weighted_coverage = valid @ weights
    differences = np.where(
        valid,
        (normalized_candidates - normalized_target) ** 2,
        0.0,
    )
    distance_numerator = differences @ weights
    minimum_coverage = float(weights.sum()) * 0.75

    target_shape = prepared.shapes[target_position]
    shape_weight = profile["shape"]
    shape_distance = np.full(len(positions), np.nan, dtype=float)
    shape_valid = np.zeros(len(positions), dtype=bool)
    if np.isfinite(target_shape).all():
        candidate_shapes = prepared.shapes[positions]
        shape_valid = np.isfinite(candidate_shapes).all(axis=1)
        shape_distance = np.sqrt(
            np.nanmean((candidate_shapes - target_shape) ** 2, axis=1)
        )
        distance_numerator += np.where(
            shape_valid, shape_weight * shape_distance ** 2, 0.0
        )
        weighted_coverage += np.where(shape_valid, shape_weight, 0.0)
        minimum_coverage += shape_weight * 0.75

    distances = np.sqrt(
        distance_numerator / np.maximum(weighted_coverage, 1e-9)
    )
    eligible = np.isfinite(distances) & (weighted_coverage >= minimum_coverage)
    ranked = sorted(
        zip(positions[eligible].tolist(), distances[eligible].tolist()),
        key=lambda item: item[1],
    )

    selected = []
    used_positions = []
    for position, distance in ranked:
        if any(abs(position - used) < config["spacing"] for used in used_positions):
            continue
        record = (
            _match_record(
                frame, features, position, distance, True, config
            )
            if include_paths
            else _match_record_fast(prepared, position, distance, config)
        )
        if record is None:
            # No usable fill exists on the session after this episode.
            continue
        selected.append(record)
        used_positions.append(position)
        if len(selected) == MAX_MATCHES:
            break

    if not selected:
        return []
    match_weights = _stable_distance_weights(
        [match["distance"] for match in selected]
    )
    for match, weight in zip(selected, match_weights):
        match["weight"] = weight
        match["quality"] = max(
            1, min(99, round(100 * math.exp(-0.5 * match["distance"])))
        )
        match["_horizon_days"] = config["horizon_days"]
        match["_sampling"] = config["sampling"]

    if include_paths:
        position_offset = int(positions[0])
        for match in selected:
            candidate_index = match["position"] - position_offset
            supports = []
            for feature_index, (name, feature_weight) in enumerate(
                zip(usable, feature_weights)
            ):
                if not valid[candidate_index, feature_index]:
                    continue
                difference = abs(
                    normalized_candidates[candidate_index, feature_index]
                    - normalized_target[feature_index]
                )
                closeness = math.exp(-0.5 * difference)
                supports.append(
                    {
                        "label": FEATURE_LABELS[name],
                        "closeness": round(closeness * 100),
                        "_support": feature_weight * closeness,
                    }
                )
            if shape_valid[candidate_index]:
                closeness = math.exp(
                    -0.5 * shape_distance[candidate_index]
                )
                supports.append(
                    {
                        "label": (
                            f"{config['shape_days']}-day chart shape"
                        ),
                        "closeness": round(closeness * 100),
                        "_support": shape_weight * closeness,
                    }
                )
            total_support = sum(item["_support"] for item in supports) or 1.0
            match["contributions"] = [
                {
                    "label": item["label"],
                    "closeness": item["closeness"],
                    "share": round(item["_support"] / total_support * 100),
                }
                for item in sorted(
                    supports,
                    key=lambda item: item["_support"],
                    reverse=True,
                )[:6]
            ]
    close = frame["Close"]
    baseline_opens = frame["Open"].to_numpy(dtype=float)
    baseline_closes = close.to_numpy(dtype=float)
    baseline_rates = {}
    for horizon in sorted({5, 10, config["horizon_days"]}):
        baseline_positions = range(
            config["warmup"],
            candidate_end + 1,
            horizon,
        )
        baseline_entries = (
            (position, _entry_price_at(baseline_opens, baseline_closes, position))
            for position in baseline_positions
            if position + horizon < target_position
        )
        baseline_outcomes = [
            float(close.iloc[position + horizon]) > entry
            for position, entry in baseline_entries
            if entry is not None
        ]
        baseline_rates[horizon] = (
            (sum(baseline_outcomes) + 0.5)
            / (len(baseline_outcomes) + 1)
            if baseline_outcomes
            else 0.5
        )
    for match in selected:
        match["_baseline_up_rates"] = baseline_rates
        match["_baseline_up_rate"] = baseline_rates[config["horizon_days"]]
    return selected


def _match_record(
    frame, features, position, distance, include_path, config=None
):
    config = config or _sampling_config(frame)
    horizon = config["horizon_days"]
    close = frame["Close"]
    entry = _entry_price_at(
        frame["Open"].to_numpy(dtype=float),
        close.to_numpy(dtype=float),
        position,
    )
    if entry is None:
        return None
    future = close.iloc[position : position + horizon + 1]
    path = ((future / entry) - 1).to_numpy(dtype=float) * 100
    # Index 0 is the signal bar, which sits before the fill; the entry itself
    # is flat by construction.
    path[0] = 0.0
    high_path = (
        (frame["High"].iloc[position : position + horizon + 1] / entry) - 1
    ).to_numpy(dtype=float) * 100
    low_path = (
        (frame["Low"].iloc[position : position + horizon + 1] / entry) - 1
    ).to_numpy(dtype=float) * 100
    feature_row = features.iloc[position]
    regime = _classify_regime(feature_row)
    return {
        "position": position,
        "date": frame.index[position],
        "distance": distance,
        "fwd_5d": float(path[5]),
        "fwd_10d": float(path[10]),
        "fwd_21d": float(path[horizon]),
        "max_upside": float(np.max(high_path[1:])),
        "max_drawdown": float(np.min(low_path[1:])),
        "path": path.tolist() if include_path else [],
        "high_path": high_path.tolist() if include_path else [],
        "low_path": low_path.tolist() if include_path else [],
        "regime": regime,
    }


def _match_record_fast(prepared, position, distance, config):
    horizon = config["horizon_days"]
    entry = _entry_price_at(prepared.open, prepared.close, position)
    if entry is None:
        return None
    future = prepared.close[position : position + horizon + 1]
    path = (future / entry - 1) * 100
    path[0] = 0.0
    high_path = (
        prepared.high[position + 1 : position + horizon + 1] / entry - 1
    ) * 100
    low_path = (
        prepared.low[position + 1 : position + horizon + 1] / entry - 1
    ) * 100
    return {
        "position": position,
        "date": prepared.dates[position],
        "distance": distance,
        "fwd_5d": float(path[5]),
        "fwd_10d": float(path[10]),
        "fwd_21d": float(path[horizon]),
        "max_upside": float(np.max(high_path)),
        "max_drawdown": float(np.min(low_path)),
        "path": [],
        "high_path": [],
        "low_path": [],
        "regime": {},
    }


def _stable_distance_weights(distances):
    if len(distances) == 1:
        return [1.0]
    shifted = np.asarray(distances, dtype=float) - min(distances)
    target_ess = max(3.0, len(distances) * 0.72)
    low = 1e-4
    high = max(float(np.max(shifted)) * 10, 1.0)
    for _ in range(50):
        bandwidth = (low + high) / 2
        weights = np.exp(-shifted / bandwidth)
        ess = float(weights.sum() ** 2 / np.sum(weights ** 2))
        if ess < target_ess:
            low = bandwidth
        else:
            high = bandwidth
    weights = np.exp(-shifted / high)
    return (weights / weights.sum()).tolist()


def _summarize_matches(matches):
    weights = [match["weight"] for match in matches]
    fwd5 = [match["fwd_5d"] for match in matches]
    fwd10 = [match["fwd_10d"] for match in matches]
    fwd21 = [match["fwd_21d"] for match in matches]

    positions = [match["position"] for match in matches]
    distinct_years = len({_year(match["date"]) for match in matches})
    raw_ess = 1 / sum(weight ** 2 for weight in weights)
    cluster_ceiling = max(4.0, distinct_years * 1.5)
    effective_n = max(1.0, min(raw_ess, cluster_ceiling, len(matches)))

    primary_days = matches[0].get("_horizon_days", 21)
    sampling = matches[0].get("_sampling", "sessions")
    baselines = matches[0].get(
        "_baseline_up_rates",
        {primary_days: matches[0].get("_baseline_up_rate", 0.5)},
    )
    horizon_inputs = (
        (5, fwd5),
        (10, fwd10),
        (primary_days, fwd21),
    )
    horizons = []
    seen = set()
    for days, values in horizon_inputs:
        if days in seen:
            continue
        seen.add(days)
        baseline = baselines.get(days, 0.5)
        raw_rate = sum(
            weight * (value > 0)
            for weight, value in zip(weights, values)
        )
        alpha = baseline * PRIOR_STRENGTH + raw_rate * effective_n
        beta = (
            (1 - baseline) * PRIOR_STRENGTH
            + (1 - raw_rate) * effective_n
        )
        horizons.append(
            {
                "days": days,
                "label": (
                    f"{days} calendar days"
                    if sampling == "calendar_daily"
                    else f"{days} sessions"
                ),
                "raw_probability_up": raw_rate * 100,
                "probability_up": alpha / (alpha + beta) * 100,
                "probability_low": _beta_ppf(0.10, alpha, beta) * 100,
                "probability_high": _beta_ppf(0.90, alpha, beta) * 100,
                "baseline_up_rate": baseline * 100,
                "median_return": _weighted_quantile(values, weights, 0.5),
                "low_return": _weighted_quantile(values, weights, 0.2),
                "high_return": _weighted_quantile(values, weights, 0.8),
                "wins": sum(value > 0 for value in values),
            }
        )
    primary = next(
        item for item in horizons
        if item["days"] == primary_days
    )

    public = {
        "count": len(matches),
        "wins_21d": sum(value > 0 for value in fwd21),
        "win_rate_21d": round(primary["raw_probability_up"]),
        "wins_5d": sum(value > 0 for value in fwd5),
        "win_rate_5d": round(
            sum(weight * (value > 0) for weight, value in zip(weights, fwd5)) * 100
        ),
        "median_5d": round(_weighted_quantile(fwd5, weights, 0.5), 2),
        "median_10d": round(_weighted_quantile(fwd10, weights, 0.5), 2),
        "median_21d": round(_weighted_quantile(fwd21, weights, 0.5), 2),
        "p20_21d": round(_weighted_quantile(fwd21, weights, 0.2), 2),
        "p25_21d": round(_weighted_quantile(fwd21, weights, 0.25), 2),
        "p75_21d": round(_weighted_quantile(fwd21, weights, 0.75), 2),
        "p80_21d": round(_weighted_quantile(fwd21, weights, 0.8), 2),
        "best_21d": round(max(fwd21), 2),
        "worst_21d": round(min(fwd21), 2),
        "effective_matches": round(effective_n, 1),
        "distinct_years": distinct_years,
        "horizons": [
            {
                **item,
                "raw_probability_up": round(item["raw_probability_up"]),
                "probability_up": round(item["probability_up"]),
                "probability_low": round(item["probability_low"]),
                "probability_high": round(item["probability_high"]),
                "baseline_up_rate": round(item["baseline_up_rate"]),
                "median_return": round(item["median_return"], 2),
                "low_return": round(item["low_return"], 2),
                "high_return": round(item["high_return"], 2),
            }
            for item in horizons
        ],
    }
    return {
        "public": public,
        "weights": weights,
        "horizons": horizons,
        "raw_match_probability_up": primary["raw_probability_up"],
        "analog_probability_up": primary["probability_up"],
        "probability_low": primary["probability_low"],
        "probability_high": primary["probability_high"],
        "baseline_up_rate": primary["baseline_up_rate"],
    }


def _build_forecast(summary, news_score, news_count, validation, config):
    analog = max(1.0, min(99.0, summary["analog_probability_up"]))
    baseline = max(1.0, min(99.0, summary["baseline_up_rate"]))
    raw_match_probability = max(
        1.0,
        min(99.0, summary["raw_match_probability_up"]),
    )
    consistency = min(1.0, max(0.0, news_count) / 5) ** 0.5
    requested_news_adjustment = max(
        -MAX_NEWS_ADJUSTMENT,
        min(MAX_NEWS_ADJUSTMENT, news_score * consistency * MAX_NEWS_ADJUSTMENT),
    )
    combined = max(
        1.0,
        min(99.0, analog + requested_news_adjustment),
    )
    news_adjustment = combined - analog
    edge = analog - baseline
    stats = summary["public"]

    analog_direction = "neutral"
    if edge >= 4 and stats["median_21d"] > 0.5:
        analog_direction = "bullish"
    elif edge <= -4 and stats["median_21d"] < -0.5:
        analog_direction = "bearish"

    direction = analog_direction
    if analog_direction == "bullish" and combined < baseline + 2:
        direction = "neutral"
    elif analog_direction == "bearish" and combined > baseline - 2:
        direction = "neutral"

    validation_factor = {
        "positive": 1.0, "mixed": 0.75, "limited": 0.55, "weak": 0.4
    }.get(validation.get("grade"), 0.5)
    evidence_score = (
        min(1, stats["effective_matches"] / 18) * 35
        + min(1, abs(edge) / 12) * 35
        + min(1, stats["distinct_years"] / 8) * 15
        + validation_factor * 15
    )
    conformal = validation.get("conformal", {})
    interval_adjustment = (
        float(conformal.get("adjustment_points", 0.0))
        if conformal.get("available")
        else 0.0
    )
    horizon_forecasts = []
    for item in stats["horizons"]:
        horizon_edge = item["probability_up"] - item["baseline_up_rate"]
        horizon_direction = "neutral"
        if horizon_edge >= 4 and item["median_return"] > 0.25:
            horizon_direction = "bullish"
        elif horizon_edge <= -4 and item["median_return"] < -0.25:
            horizon_direction = "bearish"
        horizon_forecasts.append(
            {
                **item,
                "edge_points": round(horizon_edge, 1),
                "direction": horizon_direction,
            }
        )

    # Presentation-only recalibration. The raw probability is monotonic but far
    # too dispersed, so the published number is pulled toward the asset's own
    # as-of up-rate. Ranking, tier gating, and the ledger keep the raw values.
    calibrated = calibrate_probability(combined, baseline)

    return {
        "horizon_days": config["horizon_days"],
        "horizon_label": config["horizon_label"],
        "sampling": config["sampling"],
        "direction": direction,
        "analog_direction": analog_direction,
        "probability_up": round(combined),
        "calibrated_probability_up": (
            round(calibrated) if calibrated is not None else None
        ),
        "calibration": {
            "shrink_factor": shrink_factor(),
            **CALIBRATION_PROVENANCE,
        },
        "analog_probability_up": round(analog),
        "baseline_up_rate": round(baseline),
        "edge_points": round(edge, 1),
        "news_adjustment_points": round(news_adjustment, 1),
        "probability_low": round(summary["probability_low"]),
        "probability_high": round(summary["probability_high"]),
        "evidence_score": max(10, min(95, round(evidence_score))),
        "range_21d": {
            "low": round(stats["p20_21d"] - interval_adjustment, 2),
            "typical": stats["median_21d"],
            "high": round(stats["p80_21d"] + interval_adjustment, 2),
        },
        "raw_range_21d": {
            "low": stats["p20_21d"],
            "typical": stats["median_21d"],
            "high": stats["p80_21d"],
        },
        "interval_adjustment_points": round(interval_adjustment, 2),
        "horizons": horizon_forecasts,
        "waterfall": [
            {
                "label": "Asset base rate",
                "value": round(baseline, 1),
                "delta": 0.0,
            },
            {
                "label": "Raw matched paths",
                "value": round(raw_match_probability, 1),
                "delta": round(
                    raw_match_probability - baseline,
                    1,
                ),
            },
            {
                "label": "Base-rate shrinkage",
                "value": round(analog, 1),
                "delta": round(
                    analog - raw_match_probability,
                    1,
                ),
            },
            {
                "label": "Current news",
                "value": round(combined, 1),
                "delta": round(combined - analog, 1),
            },
            *(
                [
                    {
                        "label": "Measured calibration",
                        "value": round(calibrated, 1),
                        "delta": round(calibrated - combined, 1),
                    }
                ]
                if calibrated is not None
                else []
            ),
        ],
        "agreement": _agreement_summary(
            summary,
            stats,
            news_score,
            news_count,
            analog_direction,
        ),
        "explanation": (
            f"Closest setups imply {round(analog)}% odds of finishing higher, "
            f"versus this asset's {round(baseline)}% normal historical rate. "
            f"Recent headlines adjust the displayed estimate by {news_adjustment:+.1f} points. "
            f"A blind market-wide replay showed raw analog odds are far too "
            f"spread out, so the published figure is pulled back toward the base "
            f"rate, giving {round(calibrated)}%."
            if calibrated is not None
            else (
                f"Closest setups imply {round(analog)}% odds of finishing higher, "
                f"versus this asset's {round(baseline)}% normal historical rate. "
                f"Recent headlines adjust the displayed estimate by "
                f"{news_adjustment:+.1f} points."
            )
        ),
    }


def _agreement_summary(
    summary,
    stats,
    news_score,
    news_count,
    analog_direction,
):
    components = [
        {
            "label": "Matched path vote",
            "state": _signal_state(
                summary["raw_match_probability_up"] - 50,
                4,
            ),
            "detail": (
                f"{round(summary['raw_match_probability_up'])}% of weighted "
                "paths finished higher"
            ),
            "weight": 2.0,
        },
        {
            "label": "Typical path",
            "state": _signal_state(stats["median_21d"], 0.5),
            "detail": f"{stats['median_21d']:+.1f}% median finish",
            "weight": 1.5,
        },
    ]
    if news_count:
        components.append(
            {
                "label": "Current headlines",
                "state": _signal_state(news_score, 0.15),
                "detail": f"{news_count} recent articles scored",
                "weight": 0.5,
            }
        )
    reference = analog_direction
    if reference == "neutral":
        directional = [
            item["state"]
            for item in components
            if item["state"] != "neutral"
        ]
        bullish_count = directional.count("bullish")
        bearish_count = directional.count("bearish")
        reference = (
            "bullish"
            if bullish_count > bearish_count
            else "bearish"
            if bearish_count > bullish_count
            else "neutral"
        )
    total_weight = sum(item["weight"] for item in components)
    aligned = sum(
        item["weight"]
        * (
            1.0
            if item["state"] == reference
            else 0.5
            if item["state"] == "neutral"
            else 0.0
        )
        for item in components
    )
    score = round(aligned / total_weight * 100) if total_weight else 50
    label = (
        "Broad agreement"
        if score >= 75
        else "Partial agreement"
        if score >= 50
        else "Conflicting evidence"
    )
    return {
        "score": score,
        "label": label,
        "reference": reference,
        "components": [
            {
                key: value
                for key, value in item.items()
                if key != "weight"
            }
            for item in components
        ],
    }


def _signal_state(value, threshold):
    if value >= threshold:
        return "bullish"
    if value <= -threshold:
        return "bearish"
    return "neutral"


def _build_verdict(forecast, summary):
    stats = summary["public"]
    direction = forecast["direction"]
    if direction == "bullish":
        headline = "The closest setups lean UP"
        explanation = (
            f"The analogs show a {forecast['edge_points']:+.1f}-point edge over "
            f"this asset's normal up-rate. Their typical one-month move was "
            f"{stats['median_21d']:+.1f}%."
        )
    elif direction == "bearish":
        headline = "The closest setups lean DOWN"
        explanation = (
            f"The analogs show a {forecast['edge_points']:+.1f}-point edge versus "
            f"this asset's normal up-rate. Their typical one-month move was "
            f"{stats['median_21d']:+.1f}%."
        )
    elif forecast["analog_direction"] != "neutral":
        headline = "History leans, but today adds doubt"
        explanation = (
            "The historical analogs had a directional edge, but current headlines "
            "weaken it enough that waiting is the honest call."
        )
    else:
        headline = "No meaningful analog edge right now"
        explanation = (
            f"The closest setups do not beat this asset's normal {forecast['baseline_up_rate']}% "
            "up-rate by enough to justify a directional prediction."
        )
    return {
        "direction": direction,
        "headline": headline,
        "explanation": explanation,
        "confidence": forecast["evidence_score"],
        "evidence_score": forecast["evidence_score"],
    }


def _build_trade_plan(price, matches, summary, verdict, config):
    if verdict["direction"] != "bullish":
        action = "avoid_or_exit" if verdict["direction"] == "bearish" else "wait"
        return {
            "action": action,
            "bias": "flat",
            "entry": round(price, 4),
            "horizon_days": config["horizon_days"],
            "horizon_label": config["horizon_label"],
            "note": (
                "There is no validated long setup here. Preserve capital and wait "
                "for the analog edge to improve."
            ),
        }

    weights = [match["weight"] for match in matches]
    adverse = [match["max_drawdown"] for match in matches]
    favorable = [match["max_upside"] for match in matches]
    stop_pct = _weighted_quantile(adverse, weights, 0.25)
    target_pct = _weighted_quantile(favorable, weights, 0.60)
    if stop_pct >= -0.25 or target_pct <= 0.25:
        return {
            "action": "wait",
            "bias": "flat",
            "entry": round(price, 4),
            "horizon_days": config["horizon_days"],
            "horizon_label": config["horizon_label"],
            "note": (
                "The analog paths do not produce defensible stop and target levels, "
                "so Playbook will not manufacture a trade plan."
            ),
        }

    first_touch_weight = 0.0
    expectancy = 0.0
    for match in matches:
        outcome = _first_touch(
            match["low_path"],
            match["high_path"],
            stop_pct,
            target_pct,
        )
        if outcome == "target":
            first_touch_weight += match["weight"]
            realized = target_pct
        elif outcome == "stop":
            realized = stop_pct
        else:
            realized = match["path"][-1]
        expectancy += match["weight"] * realized
    hit_rate = first_touch_weight
    risk_reward = target_pct / abs(stop_pct)
    if risk_reward < 1.1 or expectancy <= 0:
        return {
            "action": "wait",
            "bias": "flat",
            "entry": round(price, 4),
            "horizon_days": config["horizon_days"],
            "horizon_label": config["horizon_label"],
            "note": (
                "The direction leans up, but the matched paths do not support a "
                "positive risk/reward plan. A forecast is not automatically a trade."
            ),
        }

    return {
        "action": "consider_buying",
        "bias": "long",
        "entry": round(price, 4),
        "stop": round(price * (1 + stop_pct / 100), 4),
        "target": round(price * (1 + target_pct / 100), 4),
        "stop_pct": round(stop_pct, 1),
        "target_pct": round(target_pct, 1),
        "risk_reward": round(risk_reward, 1),
        "matched_path_hit_rate": round(hit_rate * 100),
        "expectancy_pct": round(expectancy, 1),
        "horizon_days": config["horizon_days"],
        "horizon_label": config["horizon_label"],
        "note": (
            "Levels come from actual intramonth paths, not month-end returns. "
            "The hit rate describes these same analogs; it is not an out-of-sample promise."
        ),
    }


def _projection(matches, config, interval_adjustment=0.0):
    path_matches = [match for match in matches if match["path"]]
    if not path_matches:
        return {"days": [], "low": [], "median": [], "high": []}
    days = list(range(config["horizon_days"] + 1))
    weights = [match["weight"] for match in path_matches]
    low = []
    median = []
    high = []
    for day in days:
        values = [match["path"][day] for match in path_matches]
        day_adjustment = interval_adjustment * math.sqrt(
            day / max(1, config["horizon_days"])
        )
        low.append(
            round(
                _weighted_quantile(values, weights, 0.2)
                - day_adjustment,
                3,
            )
        )
        median.append(round(_weighted_quantile(values, weights, 0.5), 3))
        high.append(
            round(
                _weighted_quantile(values, weights, 0.8)
                + day_adjustment,
                3,
            )
        )
    return {
        "days": days,
        "low": low,
        "median": median,
        "high": high,
        "interval_adjustment_points": round(interval_adjustment, 2),
    }


def _public_match(match):
    reasons = [
        f"same {match['regime']['trend'].lower()}",
        f"{match['regime']['volatility'].lower()} volatility",
    ]
    return {
        "date": _date_string(match["date"]),
        "quality": match["quality"],
        "similarity": match["quality"],
        "weight": round(match["weight"], 4),
        "regime": match["regime"]["label"],
        "reasons": reasons,
        "fwd_5d": round(match["fwd_5d"], 2),
        "fwd_10d": round(match["fwd_10d"], 2),
        "fwd_21d": round(match["fwd_21d"], 2),
        "max_upside": round(match["max_upside"], 2),
        "max_drawdown": round(match["max_drawdown"], 2),
        "contributions": match.get("contributions", []),
    }


def _build_fingerprint(today, regime, features):
    vol_reference = float(features["vol21"].median()) if "vol21" in features else 0.0
    cards = [
        {
            "label": "Price path",
            "value": _momentum_label(float(today["r21"])),
            "detail": f"{float(today['r21']):+.1f}% over 21 trading days",
        },
        {
            "label": "Trend regime",
            "value": regime["trend"],
            "detail": (
                f"Short {float(today['trend_short']):+.1f}% · "
                f"Long {float(today['trend_long']):+.1f}%"
            ),
        },
        {
            "label": "Volatility",
            "value": regime["volatility"],
            "detail": (
                f"{float(today['vol21']):.1f}% annualized"
                + (f" vs {vol_reference:.1f}% typical" if vol_reference else "")
            ),
        },
        {
            "label": "Drawdown",
            "value": regime["drawdown"],
            "detail": f"{float(today['drawdown']):.1f}% from the one-year high",
        },
        {
            "label": "RSI pressure",
            "value": _rsi_label(float(today["rsi"])),
            "detail": f"RSI {float(today['rsi']):.0f}",
        },
    ]
    if np.isfinite(today.get("volume_ratio", float("nan"))):
        cards.append(
            {
                "label": "Participation",
                "value": _volume_label(float(today["volume_ratio"])),
                "detail": f"{float(today['volume_ratio']):+.0f}% vs 20-day volume",
            }
        )
    if np.isfinite(today.get("market_trend", float("nan"))):
        cards.append(
            {
                "label": "Broad market",
                "value": _market_label(float(today["market_trend"])),
                "detail": f"SPY {float(today['market_trend']):+.1f}% vs its 200-day trend",
            }
        )
    return {"regime": regime, "cards": cards}


def _features_used(today, profile_key):
    return [
        FEATURE_LABELS[name]
        for name in WEIGHT_PROFILES[profile_key]["weights"]
        if name in today and np.isfinite(today[name])
    ] + ["21-day chart shape"]


def _classify_regime(row):
    short = float(row.get("trend_short", 0) or 0)
    long = float(row.get("trend_long", 0) or 0)
    vol_ratio = float(row.get("vol_ratio", 0) or 0)
    drawdown = float(row.get("drawdown", 0) or 0)
    if short > 1 and long > 0:
        trend = "Uptrend"
    elif short < -1 and long < 0:
        trend = "Downtrend"
    else:
        trend = "Transition"
    if vol_ratio >= 25:
        volatility = "Expanding"
    elif vol_ratio <= -20:
        volatility = "Calm"
    else:
        volatility = "Normal"
    if drawdown <= -20:
        drawdown_label = "Deep drawdown"
    elif drawdown <= -8:
        drawdown_label = "Below highs"
    else:
        drawdown_label = "Near highs"
    return {
        "trend": trend,
        "volatility": volatility,
        "drawdown": drawdown_label,
        "label": f"{trend} · {volatility} volatility · {drawdown_label}",
    }


def _describe_setup(today, regime):
    return (
        f"Today is a {regime['trend'].lower()} with "
        f"{regime['volatility'].lower()} volatility, "
        f"{float(today['r21']):+.1f}% one-month momentum, and price "
        f"{abs(float(today['drawdown'])):.1f}% below its one-year high."
    )


def _catalyst_risk(earnings_at):
    if not earnings_at:
        return {"known": False, "near": False}
    try:
        value = pd.Timestamp(earnings_at)
        if value.tzinfo is None:
            value = value.tz_localize("UTC")
        now = pd.Timestamp.now(tz="UTC")
        days = (value.tz_convert("UTC") - now).total_seconds() / 86400
    except (TypeError, ValueError):
        return {"known": False, "near": False}
    near = -1 <= days <= 7
    return {
        "known": True,
        "near": near,
        "date": value.date().isoformat(),
        "days_away": round(days),
        "warning": (
            "Earnings are within seven days. That event is not represented by "
            "ordinary price analogs, so confidence should be reduced."
            if near else ""
        ),
    }


def _first_touch(low_path, high_path, stop_pct, target_pct):
    for low, high in zip(low_path[1:], high_path[1:]):
        # Daily data cannot reveal intraday order when both levels trade.
        # Treat that ambiguous bar as a stop to keep the estimate conservative.
        if low <= stop_pct:
            return "stop"
        if high >= target_pct:
            return "target"
    return None


def _weighted_quantile(values, weights, fraction):
    if not values:
        return 0.0
    pairs = sorted(zip(values, weights), key=lambda item: item[0])
    total = sum(max(0.0, weight) for _, weight in pairs)
    if total <= 0:
        return float(pairs[len(pairs) // 2][0])
    threshold = fraction * total
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += max(0.0, weight)
        if cumulative >= threshold:
            return float(value)
    return float(pairs[-1][0])


def _brier_score(predictions):
    if not predictions:
        return None
    return sum(
        (item["probability_up"] - float(item["actual_up"])) ** 2
        for item in predictions
    ) / len(predictions)


def _wilson_interval(successes, total, z=1.96):
    if total <= 0:
        return 0.0, 1.0
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _beta_ppf(probability, alpha, beta):
    low = 0.0
    high = 1.0
    for _ in range(60):
        middle = (low + high) / 2
        if _regularized_beta(middle, alpha, beta) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _regularized_beta(value, alpha, beta):
    if value <= 0:
        return 0.0
    if value >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(alpha + beta)
        - math.lgamma(alpha)
        - math.lgamma(beta)
        + alpha * math.log(value)
        + beta * math.log1p(-value)
    )
    if value < (alpha + 1) / (alpha + beta + 2):
        return front * _beta_fraction(value, alpha, beta) / alpha
    return 1 - front * _beta_fraction(1 - value, beta, alpha) / beta


def _beta_fraction(value, alpha, beta):
    tiny = 1e-30
    qab = alpha + beta
    qap = alpha + 1
    qam = alpha - 1
    c = 1.0
    d = 1 - qab * value / qap
    if abs(d) < tiny:
        d = tiny
    d = 1 / d
    result = d
    for iteration in range(1, 201):
        even = 2 * iteration
        coefficient = (
            iteration * (beta - iteration) * value
            / ((qam + even) * (alpha + even))
        )
        d = 1 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        result *= d * c

        coefficient = -(
            (alpha + iteration) * (qab + iteration) * value
            / ((alpha + even) * (qap + even))
        )
        d = 1 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1 / d
        change = d * c
        result *= change
        if abs(change - 1) < 3e-12:
            break
    return result


def _momentum_label(value):
    if value >= 8:
        return "Strong advance"
    if value >= 2:
        return "Rising"
    if value <= -8:
        return "Sharp decline"
    if value <= -2:
        return "Falling"
    return "Sideways"


def _rsi_label(value):
    if value >= 70:
        return "Overbought"
    if value <= 30:
        return "Oversold"
    if value >= 55:
        return "Buyers in control"
    if value <= 45:
        return "Sellers in control"
    return "Balanced"


def _volume_label(value):
    if value >= 40:
        return "Heavy volume"
    if value <= -30:
        return "Quiet volume"
    return "Normal volume"


def _market_label(value):
    if value >= 3:
        return "Market tailwind"
    if value <= -3:
        return "Market headwind"
    return "Neutral market"


def _date_string(value):
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value)[:10]


def _year(value):
    if hasattr(value, "year"):
        return value.year
    return str(value)[:4]


def _calendar_years(index):
    if len(index) < 2:
        return 0.0
    try:
        elapsed = (pd.Timestamp(index[-1]) - pd.Timestamp(index[0])).days
        return round(min(20.0, max(0.0, elapsed / 365.25)), 1)
    except (TypeError, ValueError):
        return round(min(20.0, len(index) / 252), 1)
