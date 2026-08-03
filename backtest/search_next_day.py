"""Broad search for next-day predictors across the cached price history.

Search a wide feature space hard enough and something always looks significant.
That is guaranteed by arithmetic, not by the market, so this is built so the
answer is trustworthy either way:

  * Features are ranked on the training half only; the second half is untouched
    until a winner has been picked.
  * Skill is the daily cross-sectional information coefficient -- rank the
    feature across symbols each day, rank the forward return, correlate. The
    t-statistic comes from the time series of daily ICs, so a single lucky day
    cannot carry a feature.
  * The expected best |t| under pure noise is reported alongside the observed
    one. With N features that threshold is roughly sqrt(2 ln N), and a winner
    that does not clear it has found nothing.
  * The target is the tradable open-to-close move. Close-to-close is reported
    beside it, because the difference is the overnight gap, which no strategy
    holding from the next open can actually capture.

Statistical significance is not the bar. With hundreds of thousands of
observations a 2 basis point effect clears p<0.001 and still loses money after
costs, so the economic size is reported in basis points next to every result.
"""

from __future__ import annotations

import math
import os
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DB = os.environ.get(
    "PLAYBOOK_DATA_CACHE", os.path.join(REPO, "instance", "playbook.sqlite3")
)
SYMBOL_LIMIT = int(os.environ.get("SEARCH_SYMBOLS", "800"))
START = "2016-01-01"


def load_wide():
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        symbols = [
            row[0]
            for row in connection.execute(
                "SELECT symbol, COUNT(*) n FROM price_bars "
                "GROUP BY symbol HAVING n >= 2000 AND MAX(session_date) >= '2026-07-25' "
                "ORDER BY n DESC LIMIT ?",
                (SYMBOL_LIMIT,),
            )
        ]
        placeholders = ",".join("?" for _ in symbols)
        frame = pd.read_sql_query(
            "SELECT symbol, session_date, open, high, low, close, volume "
            f"FROM price_bars WHERE symbol IN ({placeholders}) "
            "AND session_date >= ?",
            connection,
            params=[*symbols, START],
        )
    finally:
        connection.close()
    frame["session_date"] = pd.to_datetime(frame["session_date"])
    fields = {}
    for name in ("open", "high", "low", "close", "volume"):
        fields[name] = frame.pivot_table(
            index="session_date", columns="symbol", values=name
        ).sort_index()
    return fields


def build_features(fields):
    close = fields["close"]
    open_ = fields["open"]
    high = fields["high"]
    low = fields["low"]
    volume = fields["volume"]

    features = {}
    for window in (1, 2, 3, 5, 10, 21, 63, 126):
        features[f"mom_{window}"] = close.pct_change(window, fill_method=None)
        features[f"reversal_{window}"] = -close.pct_change(window, fill_method=None)

    delta = close.diff()
    for period in (5, 14):
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        features[f"rsi_{period}"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    returns = close.pct_change(fill_method=None)
    for window in (5, 21, 63):
        features[f"vol_{window}"] = returns.rolling(window).std()
        features[f"sma_dist_{window}"] = close / close.rolling(window).mean() - 1
    features["sma_dist_200"] = close / close.rolling(200).mean() - 1
    features["vol_ratio"] = (
        returns.rolling(5).std() / returns.rolling(21).std() - 1
    )

    features["gap"] = open_ / close.shift() - 1
    span = (high - low).replace(0, np.nan)
    features["intraday_position"] = (close - low) / span
    features["intraday_move"] = close / open_ - 1
    features["true_range"] = span / close
    features["drawdown_252"] = close / close.rolling(252).max() - 1
    features["high_21"] = close / close.rolling(21).max() - 1
    features["low_21"] = close / close.rolling(21).min() - 1

    average_volume = volume.rolling(20).mean().replace(0, np.nan)
    features["volume_ratio"] = volume.rolling(5).mean() / average_volume - 1
    features["dollar_volume"] = np.log1p(close * average_volume)
    features["price_level"] = np.log(close.replace(0, np.nan))

    # Market context, broadcast to every symbol so it can interact with rank.
    universe = close.pct_change(fill_method=None).mean(axis=1)
    breadth = (close > close.rolling(200).mean()).mean(axis=1)
    for name, series in (
        ("mkt_1d", universe),
        ("mkt_5d", universe.rolling(5).mean()),
        ("mkt_21d", universe.rolling(21).mean()),
        ("breadth", breadth),
    ):
        features[f"beta_x_{name}"] = features["mom_21"].mul(series, axis=0)

    return features


def daily_ic(feature, forward):
    """Cross-sectional Spearman per day, vectorised."""
    valid = feature.notna() & forward.notna()
    feature = feature.where(valid)
    forward = forward.where(valid)
    counts = valid.sum(axis=1)
    usable = counts >= 30
    ranked_feature = feature.rank(axis=1)
    ranked_forward = forward.rank(axis=1)
    centred_feature = ranked_feature.sub(ranked_feature.mean(axis=1), axis=0)
    centred_forward = ranked_forward.sub(ranked_forward.mean(axis=1), axis=0)
    numerator = (centred_feature * centred_forward).sum(axis=1)
    denominator = np.sqrt(
        (centred_feature**2).sum(axis=1) * (centred_forward**2).sum(axis=1)
    )
    ic = (numerator / denominator.replace(0, np.nan))[usable]
    return ic.dropna()


def summarise(ic):
    if len(ic) < 30:
        return None
    stderr = ic.std(ddof=1) / math.sqrt(len(ic))
    return {
        "days": len(ic),
        "ic": ic.mean(),
        "t": ic.mean() / stderr if stderr > 0 else float("nan"),
    }


def main():
    fields = load_wide()
    close = fields["close"]
    open_ = fields["open"]
    print(
        f"{close.shape[1]} symbols, {close.shape[0]} sessions "
        f"({close.index.min().date()} -> {close.index.max().date()})"
    )

    # Tradable: signal on today's close, buy tomorrow's open, sell tomorrow's
    # close. Close-to-close includes the overnight gap, which cannot be traded
    # from the next open and inflates any apparent edge.
    tradable = (close / open_ - 1).shift(-1)
    close_to_close = close.pct_change(fill_method=None).shift(-1)

    features = build_features(fields)
    print(f"{len(features)} candidate features\n")

    dates = close.index
    cut = dates[len(dates) // 2]

    rows = []
    for name, frame in features.items():
        frame = frame.replace([np.inf, -np.inf], np.nan)
        ic_all = daily_ic(frame, tradable)
        train = summarise(ic_all[ic_all.index < cut])
        test = summarise(ic_all[ic_all.index >= cut])
        gap_ic = daily_ic(frame, close_to_close)
        gap = summarise(gap_ic)
        if not (train and test and gap):
            continue
        rows.append(
            {
                "feature": name,
                "train_ic": train["ic"],
                "train_t": train["t"],
                "test_ic": test["ic"],
                "test_t": test["t"],
                "c2c_ic": gap["ic"],
                "days": train["days"] + test["days"],
            }
        )

    table = pd.DataFrame(rows).sort_values("train_t", key=abs, ascending=False)
    count = len(table)
    noise_threshold = math.sqrt(2 * math.log(count)) if count > 1 else float("nan")

    print(f"Ranked on the TRAINING half only. {count} features tested.")
    print(
        f"Expected best |t| from pure noise with {count} features: "
        f"{noise_threshold:.2f}. A winner below that has found nothing.\n"
    )
    print(
        f"{'feature':22s} {'train IC':>9} {'train t':>8} "
        f"{'TEST IC':>9} {'TEST t':>8} {'c2c IC':>8}"
    )
    for _, row in table.head(15).iterrows():
        print(
            f"{row['feature']:22s} {row['train_ic']:+9.4f} {row['train_t']:+8.2f} "
            f"{row['test_ic']:+9.4f} {row['test_t']:+8.2f} {row['c2c_ic']:+8.4f}"
        )

    best = table.iloc[0]
    print(
        f"\nBest in training: {best['feature']} at t={best['train_t']:+.2f} "
        f"(noise threshold {noise_threshold:.2f})"
    )
    print(
        f"  holds out of sample at t={best['test_t']:+.2f}, "
        f"IC {best['test_ic']:+.4f}"
    )
    survivors = table[
        (table["train_t"].abs() > noise_threshold)
        & (np.sign(table["train_ic"]) == np.sign(table["test_ic"]))
        & (table["test_t"].abs() > 2)
    ]
    print(f"\nFeatures clearing noise AND replicating with the same sign: {len(survivors)}")
    if len(survivors):
        print(survivors[["feature", "train_ic", "test_ic", "test_t"]].to_string(index=False))
        print(
            "\nEconomic size: an IC of 0.01 on a long/short decile spread is "
            "worth single-digit\nbasis points a day. One round trip costs 10-50. "
            "Check that before believing it."
        )


if __name__ == "__main__":
    main()
