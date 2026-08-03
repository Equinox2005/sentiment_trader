"""Screen market-breadth factors against the stored replay panel.

Breadth is identical for every symbol on a given date, so it cannot improve
stock selection -- within-date it is a constant and contributes nothing. It can
only inform *when* the board is worth trading. That makes this a time-series
test on 126 periods, not a cross-sectional one on 26,347 cells, and the power is
correspondingly low. Reported accordingly.

Hypotheses, fixed before looking:

    B1  The board's edge is larger when fewer names are above their 200-day
        average, extending the regime split already measured (longs beat the
        universe by 0.70 points below the SPY 200-day line and lagged it by 1.68
        above).
    B2  Wider cross-sectional dispersion means more for the matcher to find, so
        the board's edge is larger.
    B3  The board's edge is larger after the market has fallen.

Everything is computed as-of the signal date from prices already cached.
"""

from __future__ import annotations

import math
import os
import sqlite3

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PANEL = os.path.join(HERE, "replay_2016_2026.csv")
DB = os.environ.get(
    "PLAYBOOK_DATA_CACHE", os.path.join(REPO, "instance", "playbook.sqlite3")
)


def load_panel():
    panel = pd.read_csv(PANEL, low_memory=False)
    panel["as_of"] = pd.to_datetime(panel["as_of"])
    panel["side"] = panel["side"].fillna("")
    panel["w"] = panel.groupby("as_of")["fwd_return"].transform(
        lambda s: s.clip(s.quantile(0.02), s.quantile(0.98))
    )
    panel["strat"] = np.where(panel["side"] == "short", -panel["w"], panel["w"])
    universe = panel.groupby("as_of")["w"].mean().rename("uni")
    panel = panel.join(universe, on="as_of")
    panel["excess"] = np.where(
        panel["side"] == "short",
        -panel["w"] + panel["uni"],
        panel["w"] - panel["uni"],
    )
    return panel


def load_closes(symbols):
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        placeholders = ",".join("?" for _ in symbols)
        frame = pd.read_sql_query(
            f"SELECT symbol, session_date, close FROM price_bars "
            f"WHERE symbol IN ({placeholders})",
            connection,
            params=list(symbols),
        )
    finally:
        connection.close()
    frame["session_date"] = pd.to_datetime(frame["session_date"])
    return frame.pivot_table(
        index="session_date", columns="symbol", values="close"
    ).sort_index()


def breadth_features(closes, dates):
    """Trailing-only market state on each signal date."""
    sma200 = closes.rolling(200, min_periods=120).mean()
    above = (closes > sma200)
    returns21 = closes.pct_change(21, fill_method=None)
    returns5 = closes.pct_change(5, fill_method=None)

    rows = []
    for date in dates:
        window = closes.index[closes.index <= date]
        if len(window) == 0:
            continue
        session = window[-1]
        share_above = above.loc[session].mean(skipna=True)
        month = returns21.loc[session]
        rows.append(
            {
                "as_of": date,
                "pct_above_200dma": 100 * float(share_above),
                "dispersion_21d": 100 * float(month.std(skipna=True)),
                "median_21d_return": 100 * float(month.median(skipna=True)),
                "advance_share_5d": 100
                * float((returns5.loc[session] > 0).mean(skipna=True)),
            }
        )
    return pd.DataFrame(rows)


def board_periods(panel, top_k=10):
    picks = []
    eligible = panel[panel["eligible"] == 1]
    for date, group in eligible.groupby("as_of"):
        for side in ("long", "short"):
            side_group = group[group["side"] == side]
            if len(side_group):
                picks.append(side_group.nlargest(top_k, "score"))
    selected = pd.concat(picks)
    return pd.DataFrame(
        {
            "board_return": selected.groupby("as_of")["strat"].mean(),
            "board_excess": selected.groupby("as_of")["excess"].mean(),
        }
    )


def report(merged, factor, target):
    subset = merged.dropna(subset=[factor, target])
    if len(subset) < 20:
        return
    dates = sorted(subset["as_of"])
    cut = dates[len(dates) // 2]
    train = subset[subset["as_of"] < cut]
    test = subset[subset["as_of"] >= cut]
    whole = stats.spearmanr(subset[factor], subset[target])
    train_r = stats.spearmanr(train[factor], train[target])
    test_r = stats.spearmanr(test[factor], test[target])
    # Does trading only the favourable half of the factor beat trading always?
    median = subset[factor].median()
    low = subset[subset[factor] <= median][target]
    high = subset[subset[factor] > median][target]
    tstat = (
        (low.mean() - high.mean())
        / math.sqrt(low.var(ddof=1) / len(low) + high.var(ddof=1) / len(high))
        if len(low) > 2 and len(high) > 2
        else float("nan")
    )
    print(
        f"  {factor:20s} n={len(subset):3d}  rho {whole.statistic:+.3f} "
        f"(p={whole.pvalue:.3f})  train {train_r.statistic:+.3f}  "
        f"test {test_r.statistic:+.3f}  |  low-half {low.mean():+.2f}%  "
        f"high-half {high.mean():+.2f}%  t={tstat:+.2f}"
    )


def main():
    panel = load_panel()
    symbols = sorted(panel["symbol"].dropna().unique())
    closes = load_closes(symbols)
    dates = sorted(panel["as_of"].unique())
    features = breadth_features(closes, dates)
    periods = board_periods(panel).reset_index()
    merged = periods.merge(features, on="as_of", how="inner")

    print(
        f"{len(merged)} periods with breadth features "
        f"({merged['as_of'].min().date()} -> {merged['as_of'].max().date()})"
    )
    print(
        "\nBreadth is constant within a date, so it can only time the board, "
        "never pick names.\nWith ~126 periods this test is underpowered by "
        "construction; treat a lone significant\nresult as noise unless train "
        "and test agree."
    )

    for target in ("board_excess", "board_return"):
        print(f"\n{'=' * 78}\nTARGET: {target}\n{'=' * 78}")
        for factor in (
            "pct_above_200dma",
            "dispersion_21d",
            "median_21d_return",
            "advance_share_5d",
        ):
            report(merged, factor, target)


if __name__ == "__main__":
    main()
