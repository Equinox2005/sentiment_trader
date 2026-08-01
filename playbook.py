"""Explainable historical-analog forecasting engine.

The engine compares today's complete market state with independent historical
episodes, reports the paths that followed, and walk-forward tests the same
matching pipeline before presenting a forecast.
"""

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd


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


def build_playbook(
    history,
    context=None,
    news_score=0.0,
    news_count=0,
    earnings_at=None,
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
    today_position = len(frame) - 1
    current_features = features.iloc[today_position]
    if current_features.dropna().shape[0] < 8:
        return {
            "available": False,
            "reason": "Today's setup could not be measured reliably from the available data.",
        }

    profile_key, profile_selection = _select_weight_profile(
        frame, features, shapes, config
    )
    matches = _rank_matches(
        frame,
        features,
        shapes,
        today_position,
        profile_key,
        include_paths=True,
        config=config,
    )
    if len(matches) < MIN_MATCHES:
        return {
            "available": False,
            "reason": (
                f"Only {len(matches)} independent comparable setups were found. "
                f"Playbook requires at least {MIN_MATCHES} before making a forecast."
            ),
        }

    validation = _walk_forward_validation(
        frame,
        features,
        shapes,
        profile_key,
        profile_selection,
        config,
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
                "Weights were selected on older walk-forward forecasts. "
                "The latest validation period was kept untouched."
            ),
        },
        "forecast": forecast,
        "verdict": verdict,
        "stats": summary["public"],
        "validation": validation,
        "trade_plan": plan,
        "projection": _projection(matches, config),
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
    for end in range(shape_days, len(values)):
        path = np.log(values[end - shape_days : end + 1] / values[end - shape_days])
        spread = float(np.std(path))
        if not np.isfinite(spread) or spread < 1e-9:
            shapes[end] = np.zeros(shape_days + 1)
        else:
            shapes[end] = (path - float(np.mean(path))) / spread
    return shapes


def _select_weight_profile(frame, features, shapes, config):
    anchors = _validation_anchors(len(frame), config)
    if len(anchors) < 10:
        return "balanced", {
            "tuning_forecasts": len(anchors),
            "profiles_tested": 1,
            "reason": "Not enough older checkpoints to tune weights safely.",
        }

    split = max(5, len(anchors) // 2)
    tuning = anchors[:split]
    scores = {}
    for profile_key in WEIGHT_PROFILES:
        predictions = _historical_predictions(
            frame, features, shapes, profile_key, tuning, config
        )
        scores[profile_key] = _brier_score(predictions)

    selected = min(
        scores,
        key=lambda key: scores[key] if scores[key] is not None else float("inf"),
    )
    if scores[selected] is None:
        selected = "balanced"
    return selected, {
        "tuning_forecasts": len(tuning),
        "profiles_tested": len(WEIGHT_PROFILES),
        "profile_brier": {
            key: round(value, 4) if value is not None else None
            for key, value in scores.items()
        },
    }


def _walk_forward_validation(
    frame, features, shapes, profile_key, selection, config
):
    anchors = _validation_anchors(len(frame), config)
    split = max(5, len(anchors) // 2)
    evaluation = anchors[split:] if len(anchors) >= 10 else []
    predictions = _historical_predictions(
        frame, features, shapes, profile_key, evaluation, config
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
    interval = _wilson_interval(correct, len(predictions))

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
        "correct": correct,
        "accuracy": round(accuracy * 100),
        "accuracy_low": round(interval[0] * 100),
        "accuracy_high": round(interval[1] * 100),
        "baseline_accuracy": round(baseline_accuracy * 100),
        "brier": round(model_brier, 3),
        "baseline_brier": round(baseline_brier, 3),
        "actionable_count": len(actionable),
        "actionable_accuracy": (
            round(actionable_correct / len(actionable) * 100)
            if actionable else None
        ),
        "selection": selection,
        "explanation": (
            "Each checkpoint used only information available on that date. "
            "Weight tuning used older checkpoints; these results use the newer "
            "untouched checkpoints."
        ),
    }


def _validation_anchors(length, config):
    first = (
        config["warmup"]
        + config["recent_exclusion"]
        + MIN_MATCHES * config["spacing"]
    )
    last = length - config["horizon_days"] - 1
    if last <= first:
        return []
    anchors = list(range(first, last + 1, config["spacing"]))
    return anchors[-40:]


def _historical_predictions(
    frame, features, shapes, profile_key, anchors, config
):
    predictions = []
    close = frame["Close"]
    for target_position in anchors:
        matches = _rank_matches(
            frame,
            features,
            shapes,
            target_position,
            profile_key,
            include_paths=False,
            config=config,
        )
        if len(matches) < MIN_MATCHES:
            continue
        summary = _summarize_matches(matches)
        actual_return = (
            float(close.iloc[target_position + config["horizon_days"]])
            / float(close.iloc[target_position])
            - 1
        ) * 100
        predictions.append(
            {
                "probability_up": summary["analog_probability_up"] / 100,
                "baseline_up_rate": summary["baseline_up_rate"] / 100,
                "edge": (
                    summary["analog_probability_up"] - summary["baseline_up_rate"]
                ) / 100,
                "median_return": summary["public"]["median_21d"],
                "actual_up": actual_return > 0,
            }
        )
    return predictions


def _rank_matches(
    frame,
    features,
    shapes,
    target_position,
    profile_key,
    include_paths,
    config=None,
):
    config = config or _sampling_config(frame)
    candidate_end = target_position - config["recent_exclusion"]
    if candidate_end <= config["warmup"]:
        return []
    positions = np.arange(config["warmup"], candidate_end + 1, dtype=int)
    profile = WEIGHT_PROFILES[profile_key]
    target = features.iloc[target_position]

    usable = []
    feature_weights = []
    for name, weight in profile["weights"].items():
        if name not in features or not np.isfinite(target.get(name, float("nan"))):
            continue
        coverage = features.iloc[positions][name].notna().mean()
        if coverage >= 0.75:
            usable.append(name)
            feature_weights.append(weight)
    if len(usable) < 6:
        return []

    candidate_values = features.iloc[positions][usable].to_numpy(dtype=float)
    target_values = target[usable].to_numpy(dtype=float)
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

    target_shape = shapes[target_position]
    shape_weight = profile["shape"]
    if np.isfinite(target_shape).all():
        candidate_shapes = shapes[positions]
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
        selected.append(_match_record(
            frame, features, position, distance, include_paths, config
        ))
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
    close = frame["Close"]
    baseline_positions = range(
        config["warmup"],
        candidate_end + 1,
        config["horizon_days"],
    )
    baseline_outcomes = [
        float(close.iloc[position + config["horizon_days"]])
        > float(close.iloc[position])
        for position in baseline_positions
        if position + config["horizon_days"] < target_position
    ]
    baseline = (
        (sum(baseline_outcomes) + 0.5) / (len(baseline_outcomes) + 1)
        if baseline_outcomes else 0.5
    )
    for match in selected:
        match["_baseline_up_rate"] = baseline
    return selected


def _match_record(
    frame, features, position, distance, include_path, config=None
):
    config = config or _sampling_config(frame)
    horizon = config["horizon_days"]
    close = frame["Close"]
    entry = float(close.iloc[position])
    future = close.iloc[position : position + horizon + 1]
    path = ((future / entry) - 1).to_numpy(dtype=float) * 100
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
    base_values = [1.0 if value > 0 else 0.0 for value in fwd21]
    weighted_rate = sum(weight * value for weight, value in zip(weights, base_values))

    positions = [match["position"] for match in matches]
    distinct_years = len({_year(match["date"]) for match in matches})
    raw_ess = 1 / sum(weight ** 2 for weight in weights)
    cluster_ceiling = max(4.0, distinct_years * 1.5)
    effective_n = max(1.0, min(raw_ess, cluster_ceiling, len(matches)))

    # This as-of base rate uses non-overlapping outcomes available before the target.
    baseline = matches[0].get("_baseline_up_rate", 0.5)

    alpha = baseline * PRIOR_STRENGTH + weighted_rate * effective_n
    beta = (1 - baseline) * PRIOR_STRENGTH + (1 - weighted_rate) * effective_n
    probability = alpha / (alpha + beta)
    probability_low = _beta_ppf(0.10, alpha, beta)
    probability_high = _beta_ppf(0.90, alpha, beta)

    public = {
        "count": len(matches),
        "wins_21d": sum(value > 0 for value in fwd21),
        "win_rate_21d": round(weighted_rate * 100),
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
    }
    return {
        "public": public,
        "weights": weights,
        "analog_probability_up": probability * 100,
        "probability_low": probability_low * 100,
        "probability_high": probability_high * 100,
        "baseline_up_rate": baseline * 100,
    }


def _build_forecast(summary, news_score, news_count, validation, config):
    analog = summary["analog_probability_up"]
    baseline = summary["baseline_up_rate"]
    consistency = min(1.0, max(0.0, news_count) / 5) ** 0.5
    news_adjustment = max(
        -MAX_NEWS_ADJUSTMENT,
        min(MAX_NEWS_ADJUSTMENT, news_score * consistency * MAX_NEWS_ADJUSTMENT),
    )
    combined = max(1.0, min(99.0, analog + news_adjustment))
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

    return {
        "horizon_days": config["horizon_days"],
        "horizon_label": config["horizon_label"],
        "sampling": config["sampling"],
        "direction": direction,
        "analog_direction": analog_direction,
        "probability_up": round(combined),
        "analog_probability_up": round(analog),
        "baseline_up_rate": round(baseline),
        "edge_points": round(edge, 1),
        "news_adjustment_points": round(news_adjustment, 1),
        "probability_low": round(summary["probability_low"]),
        "probability_high": round(summary["probability_high"]),
        "evidence_score": max(10, min(95, round(evidence_score))),
        "range_21d": {
            "low": stats["p20_21d"],
            "typical": stats["median_21d"],
            "high": stats["p80_21d"],
        },
        "explanation": (
            f"Closest setups imply {round(analog)}% odds of finishing higher, "
            f"versus this asset's {round(baseline)}% normal historical rate. "
            f"Recent headlines adjust the displayed estimate by {news_adjustment:+.1f} points."
        ),
    }


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


def _projection(matches, config):
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
        low.append(round(_weighted_quantile(values, weights, 0.2), 3))
        median.append(round(_weighted_quantile(values, weights, 0.5), 3))
        high.append(round(_weighted_quantile(values, weights, 0.8), 3))
    return {"days": days, "low": low, "median": median, "high": high}


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
