"""Screen short interest against the stored replay panel.

Integrating a feature into the matcher costs a full 3.2-hour replay. Screening
it against the panel costs seconds, because every cell already carries the
board's verdict and the realized outcome. Only a factor that shows incremental
signal here is worth the re-run.

Hypotheses, fixed before looking at any outcome:

    H1  For short signals, high days-to-cover predicts worse short returns.
    H2  For long signals, high days-to-cover predicts better long returns.
    H3  Rising short interest predicts worse forward returns.

Two things protect the result. FINRA publishes a settlement date roughly eight
business days later, so the join is lagged and a reading is only used once it
was actually public. And every number is reported on the training half and the
untouched half side by side.
"""

from __future__ import annotations

import math
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.join(HERE, "replay_2016_2026.csv")
SHORT_INTEREST = os.path.join(HERE, "short_interest.csv")

# FINRA publishes on the eighth business day after settlement. Fourteen calendar
# days is comfortably past that, so a reading is never used before it was public.
PUBLICATION_LAG_DAYS = 14


def load():
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

    short = pd.read_csv(SHORT_INTEREST)
    short["settlement_date"] = pd.to_datetime(short["settlement_date"])
    short["available_from"] = short["settlement_date"] + pd.Timedelta(
        days=PUBLICATION_LAG_DAYS
    )
    short = short.dropna(subset=["days_to_cover"]).sort_values("available_from")
    return panel, short


def join_as_of(panel, short):
    """Attach the most recent reading that was public on the signal date."""
    merged = []
    for symbol, cells in panel.groupby("symbol"):
        readings = short[short["symbol"] == symbol]
        if readings.empty:
            continue
        cells = cells.sort_values("as_of")
        joined = pd.merge_asof(
            cells,
            readings[
                [
                    "available_from",
                    "days_to_cover",
                    "change_percent",
                    "short_shares",
                    "avg_daily_volume",
                ]
            ],
            left_on="as_of",
            right_on="available_from",
            direction="backward",
        )
        merged.append(joined)
    return pd.concat(merged, ignore_index=True)


def clustered(frame, column="excess"):
    means = frame.groupby("as_of")[column].mean().dropna()
    if len(means) < 3:
        return float("nan"), float("nan")
    stderr = means.std(ddof=1) / math.sqrt(len(means))
    return means.mean(), (means.mean() / stderr if stderr > 0 else float("nan"))


def quintile_table(frame, factor, label):
    frame = frame.dropna(subset=[factor]).copy()
    if len(frame) < 250:
        print(f"  {label}: only {len(frame)} rows, skipping")
        return
    dates = sorted(frame["as_of"].unique())
    cut = dates[len(dates) // 2]
    print(f"\n  {label}   (n={len(frame)}, {len(dates)} periods)")
    print(
        f"    {'quintile':>10} {'TRAIN excess':>14} {'t':>6} "
        f"{'TEST excess':>13} {'t':>6} {'TEST mean':>11}"
    )
    frame["q"] = pd.qcut(
        frame[factor], 5, labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"],
        duplicates="drop",
    )
    for bucket in frame["q"].cat.categories:
        group = frame[frame["q"] == bucket]
        train = group[group["as_of"] < cut]
        test = group[group["as_of"] >= cut]
        if len(train) < 30 or len(test) < 30:
            continue
        train_mean, train_t = clustered(train)
        test_mean, test_t = clustered(test)
        print(
            f"    {bucket:>10} {train_mean:+13.2f}% {train_t:+6.2f} "
            f"{test_mean:+12.2f}% {test_t:+6.2f} {test['strat'].mean():+10.2f}%"
        )


def main():
    panel, short = load()
    joined = join_as_of(panel, short)
    covered = joined.dropna(subset=["days_to_cover"])
    print(
        f"panel cells {len(panel)}, with a public short-interest reading "
        f"{len(covered)} ({100 * len(covered) / len(panel):.1f}%)"
    )
    print(
        f"covered date range {covered['as_of'].min().date()} -> "
        f"{covered['as_of'].max().date()}"
    )

    eligible = covered[covered["eligible"] == 1]
    print(f"eligible board signals with coverage: {len(eligible)}")

    print("\n" + "=" * 78)
    print("H1 / H2 — days to cover, split by side")
    print("=" * 78)
    for side in ("short", "long"):
        quintile_table(
            eligible[eligible["side"] == side],
            "days_to_cover",
            f"{side.upper()} signals by days-to-cover",
        )

    print("\n" + "=" * 78)
    print("H3 — change in short interest since the prior reading")
    print("=" * 78)
    for side in ("short", "long"):
        quintile_table(
            eligible[eligible["side"] == side],
            "change_percent",
            f"{side.upper()} signals by short-interest change",
        )

    print("\n" + "=" * 78)
    print("Whole eligible board, ignoring side")
    print("=" * 78)
    quintile_table(eligible, "days_to_cover", "All eligible by days-to-cover")


if __name__ == "__main__":
    main()
