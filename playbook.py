"""Historical analog engine.

Fingerprints today's market setup, scans up to ten years of history for the
most similar past days, and reports what actually happened next. Everything
is deterministic and explainable: every match is a real date with a real
outcome the user can verify.
"""

import math

import pandas as pd

FEATURE_WARMUP = 260        # trading days needed before a day can be fingerprinted
FORWARD_DAYS = 21           # outcome horizon (one trading month)
RECENT_EXCLUSION = 21       # days near today that can't be their own match
MATCH_SPACING = 42          # minimum days between two selected matches
MAX_MATCHES = 20
GHOST_PATHS = 8


def build_playbook(close):
    """Return the full analog analysis for a price series.

    ``close`` is a pandas Series of daily closes indexed by date.
    """
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < FEATURE_WARMUP + FORWARD_DAYS + RECENT_EXCLUSION + 10:
        return {
            "available": False,
            "reason": (
                "This symbol does not have enough trading history yet. "
                "The playbook needs at least about 15 months of daily prices."
            ),
        }

    features = _compute_features(close)
    today = features.iloc[-1]
    if today.isna().any():
        return {
            "available": False,
            "reason": "Today's setup could not be measured from the available history.",
        }

    matches = _find_matches(close, features)
    if len(matches) < 5:
        return {
            "available": False,
            "reason": (
                "Fewer than five comparable past setups were found, "
                "which is not enough evidence to build a playbook."
            ),
        }

    stats = _outcome_stats(matches)
    verdict = _build_verdict(stats)
    plan = _build_trade_plan(float(close.iloc[-1]), stats, verdict)
    ghost_paths = _ghost_paths(close, matches)

    return {
        "available": True,
        "setup": _describe_setup(today),
        "verdict": verdict,
        "stats": stats,
        "trade_plan": plan,
        "ghost_paths": ghost_paths,
        "matches": [
            {
                "date": str(match["date"])[:10],
                "similarity": match["similarity"],
                "fwd_5d": round(match["fwd_5d"], 2),
                "fwd_10d": round(match["fwd_10d"], 2),
                "fwd_21d": round(match["fwd_21d"], 2),
            }
            for match in matches
        ],
    }


def _compute_features(close):
    returns = close.pct_change()
    sma50 = close.rolling(50).mean()
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gains / losses.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50.0)

    features = pd.DataFrame(
        {
            "r21": close.pct_change(21) * 100,
            "r5": close.pct_change(5) * 100,
            "rsi": rsi,
            "dist_sma50": (close / sma50 - 1) * 100,
            "vol21": returns.rolling(21).std() * math.sqrt(252) * 100,
            "dd": (close / close.rolling(252, min_periods=60).max() - 1) * 100,
        },
        index=close.index,
    )
    return features


def _find_matches(close, features):
    mean = features.mean()
    std = features.std().replace(0, 1.0)
    normalized = (features - mean) / std
    today = normalized.iloc[-1]

    last_candidate = len(close) - FORWARD_DAYS - 1
    first_candidate = FEATURE_WARMUP
    recent_cutoff = len(close) - RECENT_EXCLUSION

    candidates = []
    for position in range(first_candidate, min(last_candidate, recent_cutoff)):
        row = normalized.iloc[position]
        if row.isna().any():
            continue
        distance = math.sqrt(float(((row - today) ** 2).sum()))
        candidates.append((position, distance))

    if not candidates:
        return []

    candidates.sort(key=lambda item: item[1])
    median_distance = candidates[len(candidates) // 2][1] or 1.0

    selected = []
    used_positions = []
    for position, distance in candidates:
        if any(abs(position - used) < MATCH_SPACING for used in used_positions):
            continue
        entry = float(close.iloc[position])
        selected.append(
            {
                "position": position,
                "date": close.index[position],
                "distance": distance,
                "similarity": max(
                    1, min(99, round(100 * math.exp(-distance / median_distance)))
                ),
                "fwd_5d": (float(close.iloc[position + 5]) / entry - 1) * 100,
                "fwd_10d": (float(close.iloc[position + 10]) / entry - 1) * 100,
                "fwd_21d": (float(close.iloc[position + FORWARD_DAYS]) / entry - 1) * 100,
            }
        )
        used_positions.append(position)
        if len(selected) == MAX_MATCHES:
            break
    return selected


def _outcome_stats(matches):
    fwd21 = sorted(match["fwd_21d"] for match in matches)
    fwd5 = [match["fwd_5d"] for match in matches]
    count = len(fwd21)

    def percentile(values, fraction):
        if not values:
            return 0.0
        index = (len(values) - 1) * fraction
        low = math.floor(index)
        high = math.ceil(index)
        if low == high:
            return values[low]
        return values[low] + (values[high] - values[low]) * (index - low)

    wins21 = sum(value > 0 for value in fwd21)
    return {
        "count": count,
        "wins_21d": wins21,
        "win_rate_21d": round(100 * wins21 / count),
        "wins_5d": sum(value > 0 for value in fwd5),
        "win_rate_5d": round(100 * sum(value > 0 for value in fwd5) / count),
        "median_21d": round(percentile(fwd21, 0.5), 2),
        "p25_21d": round(percentile(fwd21, 0.25), 2),
        "p75_21d": round(percentile(fwd21, 0.75), 2),
        "best_21d": round(fwd21[-1], 2),
        "worst_21d": round(fwd21[0], 2),
    }


def _build_verdict(stats):
    win_rate = stats["win_rate_21d"]
    median = stats["median_21d"]

    consistency = abs(win_rate - 50) * 1.4
    evidence = min(stats["count"], MAX_MATCHES) / MAX_MATCHES * 30
    confidence = max(15, min(90, round(25 + consistency + evidence)))

    if win_rate >= 60 and median > 0.5:
        direction = "bullish"
        headline = "History leans UP"
        explanation = (
            f"In {stats['wins_21d']} of the {stats['count']} most similar past setups, "
            f"the price was higher one month later. The typical move was "
            f"{median:+.1f}%."
        )
    elif win_rate <= 40 and median < -0.5:
        direction = "bearish"
        headline = "History leans DOWN"
        explanation = (
            f"In {stats['count'] - stats['wins_21d']} of the {stats['count']} most similar "
            f"past setups, the price was lower one month later. The typical move was "
            f"{median:+.1f}%."
        )
    else:
        direction = "neutral"
        headline = "History is split"
        explanation = (
            f"Similar past setups went up {stats['wins_21d']} times and down "
            f"{stats['count'] - stats['wins_21d']} times over the following month. "
            "There is no reliable edge here right now."
        )
        confidence = min(confidence, 45)

    return {
        "direction": direction,
        "headline": headline,
        "explanation": explanation,
        "confidence": confidence,
    }


def _build_trade_plan(price, stats, verdict):
    if verdict["direction"] == "bullish":
        # Stop needs breathing room even when past setups rarely dipped.
        stop_pct = max(-15.0, min(-3.0, stats["p25_21d"]))
        target_pct = max(1.0, min(30.0, stats["p75_21d"]))
        return {
            "action": "consider_buying",
            "bias": "long",
            "entry": round(price, 4),
            "stop": round(price * (1 + stop_pct / 100), 4),
            "target": round(price * (1 + target_pct / 100), 4),
            "stop_pct": round(stop_pct, 1),
            "target_pct": round(target_pct, 1),
            "risk_reward": round(target_pct / abs(stop_pct), 1),
            "horizon_days": FORWARD_DAYS,
            "note": (
                "Entry at the current price, stop-loss where the weakest quarter of "
                "past setups bottomed, target where the strongest quarter peaked."
            ),
        }
    if verdict["direction"] == "bearish":
        return {
            "action": "avoid_or_exit",
            "bias": "flat",
            "entry": round(price, 4),
            "horizon_days": FORWARD_DAYS,
            "note": (
                "Similar setups usually lost money over the next month. If you own it, "
                "consider tightening your stop or taking profit. If you don't, waiting "
                "costs nothing."
            ),
        }
    return {
        "action": "wait",
        "bias": "flat",
        "entry": round(price, 4),
        "horizon_days": FORWARD_DAYS,
        "note": (
            "History shows no edge in either direction from this setup. "
            "The highest-probability trade is patience."
        ),
    }


def _ghost_paths(close, matches):
    paths = []
    for match in matches[:GHOST_PATHS]:
        start = match["position"]
        entry = float(close.iloc[start])
        segment = close.iloc[start : start + FORWARD_DAYS + 1]
        paths.append(
            {
                "date": str(match["date"])[:10],
                "offsets": [
                    round((float(value) / entry - 1) * 100, 3) for value in segment
                ],
            }
        )
    return paths


def _describe_setup(today):
    parts = []
    r21 = float(today["r21"])
    rsi = float(today["rsi"])
    dist = float(today["dist_sma50"])
    dd = float(today["dd"])

    if r21 >= 5:
        parts.append(f"up {r21:.0f}% over the past month")
    elif r21 <= -5:
        parts.append(f"down {abs(r21):.0f}% over the past month")
    else:
        parts.append("roughly flat over the past month")

    if rsi >= 70:
        parts.append("running hot (overbought)")
    elif rsi <= 30:
        parts.append("washed out (oversold)")

    if dist >= 8:
        parts.append("stretched well above its 50-day trend")
    elif dist <= -8:
        parts.append("sitting well below its 50-day trend")

    if dd <= -20:
        parts.append(f"still {abs(dd):.0f}% below its one-year high")

    return "Right now this asset is " + ", ".join(parts) + "."
