"""Aggregate the blind backtest into an honest performance report."""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

SCRATCH = os.path.dirname(os.path.abspath(__file__))
ROWS = os.path.join(SCRATCH, "replay_2016_2026.csv")

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 40)

SIGNAL_ORDER = [
    "STRONG BUY", "BUY", "WEAK BUY",
    "(no signal)",
    "WEAK SHORT", "SHORT", "STRONG SHORT",
]


def load():
    frame = pd.read_csv(ROWS)
    frame["as_of"] = pd.to_datetime(frame["as_of"])
    frame["signal"] = frame["signal"].fillna("").replace("", "(no signal)")
    frame["side"] = frame["side"].fillna("")
    frame["tier"] = frame["tier"].fillna("")
    frame["strategy_return"] = np.where(
        frame["side"] == "short", -frame["fwd_return"], frame["fwd_return"]
    )
    # Same-date, same-universe equal-weight comparator: cancels the survivorship
    # and market-beta inflation shared by every cell sampled on that date.
    universe = frame.groupby("as_of")["fwd_return"].mean().rename("universe_return")
    frame = frame.join(universe, on="as_of")
    frame["excess_vs_universe"] = np.where(
        frame["side"] == "short",
        -frame["fwd_return"] + frame["universe_return"],
        frame["fwd_return"] - frame["universe_return"],
    )
    frame["excess_vs_spy"] = np.where(
        frame["side"] == "short",
        -frame["fwd_return"] + frame["spy_fwd_return"],
        frame["fwd_return"] - frame["spy_fwd_return"],
    )
    return frame


def clustered_t(frame, column, group="as_of"):
    """t-statistic on date-level means: overlapping cross-sectional
    correlation within a date cannot inflate significance."""
    means = frame.groupby(group)[column].mean().dropna()
    if len(means) < 3:
        return float("nan"), float("nan"), len(means)
    mean = means.mean()
    stderr = means.std(ddof=1) / math.sqrt(len(means))
    return mean, (mean / stderr if stderr > 0 else float("nan")), len(means)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def describe_cohort(frame, label):
    if frame.empty:
        return {"cohort": label, "n": 0}
    wins = (frame["strategy_return"] > 0).mean() * 100
    mean_excess, t_excess, periods = clustered_t(frame, "excess_vs_universe")
    mean_raw, t_raw, _ = clustered_t(frame, "strategy_return")
    return {
        "cohort": label,
        "n": len(frame),
        "dates": frame["as_of"].nunique(),
        "mean_%": round(frame["strategy_return"].mean(), 2),
        "median_%": round(frame["strategy_return"].median(), 2),
        "win_%": round(wins, 1),
        "vs_univ_%": round(mean_excess, 2),
        "t_univ": round(t_excess, 2),
        "vs_spy_%": round(frame["excess_vs_spy"].mean(), 2),
        "periods": periods,
    }


def compound(period_returns):
    curve = (1 + period_returns / 100).cumprod()
    total = (curve.iloc[-1] - 1) * 100
    years = len(period_returns) * 21 / 252
    cagr = ((curve.iloc[-1]) ** (1 / years) - 1) * 100 if years > 0 else float("nan")
    peak = curve.cummax()
    drawdown = ((curve / peak) - 1).min() * 100
    return curve, total, cagr, drawdown


def board_simulation(frame, top_k, sides=("long", "short")):
    """Each period, take the top-K eligible names per side by conviction
    score, equal weight, hold 21 sessions. Windows never overlap."""
    picks = []
    eligible = frame[(frame["eligible"] == 1) & frame["side"].isin(sides)]
    for date, group in eligible.groupby("as_of"):
        for side in sides:
            side_group = group[group["side"] == side]
            if side_group.empty:
                continue
            chosen = side_group.nlargest(top_k, "score")
            picks.append(chosen.assign(period=date))
    if not picks:
        return pd.DataFrame(), pd.Series(dtype=float), pd.Series(dtype=float)
    selected = pd.concat(picks)
    period_returns = selected.groupby("period")["strategy_return"].mean()
    # Excess must be signed per leg: a short outperforms when the name lags the
    # universe, so its comparator is the negated universe return, not the raw one.
    period_excess = selected.groupby("period")["excess_vs_universe"].mean()
    return selected, period_returns, period_excess


def main():
    frame = load()
    section("1 · COVERAGE")
    print(f"cells run             : {len(frame):,}")
    print(f"symbols               : {frame['symbol'].nunique()}")
    print(f"as-of sessions        : {frame['as_of'].nunique()} "
          f"({frame['as_of'].min().date()} -> {frame['as_of'].max().date()})")
    print(f"forecast produced     : {(frame['available'] == '1').sum() if frame['available'].dtype == object else (frame['available'] == 1).sum():,}")
    errors = frame[frame["available"].astype(str) == "error"]
    print(f"engine errors         : {len(errors):,}")
    unavailable = frame[frame["available"].astype(str) == "0"]
    print(f"declined (no forecast): {len(unavailable):,}")
    if not unavailable.empty:
        print(unavailable["unavailable_reason"].str.slice(0, 70).value_counts().head(5))
    print(f"eligible board signals: {(frame['eligible'] == 1).sum():,} "
          f"({(frame['eligible'] == 1).mean() * 100:.1f}% of cells)")
    print(f"total compute         : {frame['runtime_s'].sum() / 3600:.2f} CPU-hours")

    section("2 · RETURN BY VERDICT  (21 sessions, next-open -> horizon-close, gross)")
    rows = []
    for label in SIGNAL_ORDER:
        subset = frame[frame["signal"] == label]
        if label == "(no signal)":
            subset = subset.assign(strategy_return=subset["fwd_return"],
                                   excess_vs_universe=subset["fwd_return"] - subset["universe_return"],
                                   excess_vs_spy=subset["fwd_return"] - subset["spy_fwd_return"])
        if len(subset):
            rows.append(describe_cohort(subset, label))
    print(pd.DataFrame(rows).to_string(index=False))

    section("3 · RETURN BY SIDE AND TIER")
    rows = []
    for side in ("long", "short"):
        for tier in ("strong", "moderate", "speculative"):
            subset = frame[(frame["side"] == side) & (frame["tier"] == tier)
                           & (frame["eligible"] == 1)]
            if len(subset):
                rows.append(describe_cohort(subset, f"{side}/{tier} (eligible)"))
        subset = frame[(frame["side"] == side) & (frame["eligible"] == 1)]
        if len(subset):
            rows.append(describe_cohort(subset, f"{side}/ALL eligible"))
    print(pd.DataFrame(rows).to_string(index=False))

    section("4 · BENCHMARKS OVER THE IDENTICAL 126 WINDOWS")
    universe_periods = frame.groupby("as_of")["fwd_return"].mean()
    spy_periods = frame.groupby("as_of")["spy_fwd_return"].mean()
    for name, series in (("equal-weight sampled universe", universe_periods),
                         ("SPY", spy_periods)):
        curve, total, cagr, drawdown = compound(series.dropna())
        print(f"{name:32s} per-period {series.mean():+.2f}%  "
              f"total {total:+.1f}%  CAGR {cagr:+.1f}%  maxDD {drawdown:.1f}%")

    section("5 · BOARD SIMULATION  (top-K by conviction score, equal weight)")
    results = {}
    for top_k in (3, 5, 10, 20):
        for sides, label in ((("long",), "long only"),
                             (("short",), "short only"),
                             (("long", "short"), "long+short")):
            selected, period_returns, excess = board_simulation(frame, top_k, sides)
            if period_returns.empty:
                continue
            curve, total, cagr, drawdown = compound(period_returns)
            mean, tstat, periods = (period_returns.mean(),
                                    period_returns.mean() / (period_returns.std(ddof=1) / math.sqrt(len(period_returns))),
                                    len(period_returns))
            ex_t = excess.mean() / (excess.std(ddof=1) / math.sqrt(len(excess)))
            results[(top_k, label)] = (curve, period_returns)
            print(f"top{top_k:>3} {label:11s} n={len(selected):5d} periods={periods:3d} "
                  f"per-period {mean:+6.2f}% (t={tstat:5.2f})  "
                  f"vs univ {excess.mean():+6.2f}% (t={ex_t:5.2f})  "
                  f"total {total:+8.1f}%  CAGR {cagr:+6.1f}%  maxDD {drawdown:6.1f}%  "
                  f"win {100 * (period_returns > 0).mean():.0f}%")

    section("6 · CALIBRATION: PREDICTED VS REALIZED")
    # The engine's probability and typical move describe a close-to-close path,
    # so calibration is judged close-to-close. Only the tradable P&L above uses
    # the next-open entry.
    available = frame[frame["available"].astype(str) == "1"].copy()
    available["prob_bucket"] = pd.cut(
        available["probability_up"],
        [0, 45, 50, 55, 60, 65, 70, 100],
        labels=["<45", "45-50", "50-55", "55-60", "60-65", "65-70", ">70"],
    )
    calibration = available.groupby("prob_bucket", observed=True).agg(
        n=("fwd_close_to_close", "size"),
        predicted_up=("probability_up", "mean"),
        realized_up=("fwd_close_to_close", lambda s: (s > 0).mean() * 100),
        mean_return=("fwd_close_to_close", "mean"),
    ).round(2)
    calibration["gap_pp"] = (
        calibration["realized_up"] - calibration["predicted_up"]
    ).round(2)
    print(calibration.to_string())

    probability = available["probability_up"] / 100
    outcome = (available["fwd_close_to_close"] > 0).astype(float)
    brier = ((probability - outcome) ** 2).mean()
    base = outcome.mean()
    brier_base = ((base - outcome) ** 2).mean()
    print(f"\nout-of-sample Brier {brier:.4f} vs base-rate {brier_base:.4f} "
          f"-> skill {100 * (1 - brier / brier_base):+.1f}%   "
          f"(realized up-rate {base * 100:.1f}%)")

    eligible = frame[frame["eligible"] == 1].copy()
    if not eligible.empty:
        eligible["realized_c2c"] = np.where(
            eligible["side"] == "short",
            -eligible["fwd_close_to_close"],
            eligible["fwd_close_to_close"],
        )
        print("\npredicted typical move vs realized, eligible signals (close-to-close):")
        print(f"  median predicted move : {eligible['expected_move'].median():+.2f}%")
        print(f"  median realized move  : {eligible['realized_c2c'].median():+.2f}%")
        print(f"  mean predicted move   : {eligible['expected_move'].mean():+.2f}%")
        print(f"  mean realized move    : {eligible['realized_c2c'].mean():+.2f}%")
        print(f"  realized/predicted    : "
              f"{eligible['realized_c2c'].mean() / eligible['expected_move'].mean():.2f}x")
        print("\n  by predicted-move decile:")
        eligible["move_decile"] = pd.qcut(
            eligible["expected_move"], 5, labels=["Q1 low", "Q2", "Q3", "Q4", "Q5 high"],
            duplicates="drop",
        )
        print(eligible.groupby("move_decile", observed=True).agg(
            n=("realized_c2c", "size"),
            predicted=("expected_move", "mean"),
            realized=("realized_c2c", "mean"),
            excess_vs_univ=("excess_vs_universe", "mean"),
        ).round(2).to_string())

    section("7 · YEAR BY YEAR  (top-10 long+short board)")
    _selected, period_returns, _excess = board_simulation(frame, 10, ("long", "short"))
    if not period_returns.empty:
        yearly = pd.DataFrame({
            "board": period_returns,
            "universe": universe_periods.reindex(period_returns.index),
            "spy": spy_periods.reindex(period_returns.index),
        })
        yearly["year"] = yearly.index.year
        table = yearly.groupby("year").agg(
            periods=("board", "size"),
            board_mean=("board", "mean"),
            universe_mean=("universe", "mean"),
            spy_mean=("spy", "mean"),
            board_total=("board", lambda s: ((1 + s / 100).prod() - 1) * 100),
            universe_total=("universe", lambda s: ((1 + s / 100).prod() - 1) * 100),
        ).round(2)
        print(table.to_string())

    section("8 · LONG SIGNALS WITH THE PUBLISHED STOP / TARGET PLAN")
    plan = frame[(frame["eligible"] == 1) & (frame["side"] == "long")
                 & (frame["plan_action"] == "consider_buying")].copy()
    if not plan.empty:
        plan["stop_pct"] = pd.to_numeric(plan["stop_pct"], errors="coerce")
        plan["target_pct"] = pd.to_numeric(plan["target_pct"], errors="coerce")
        hit_stop = plan["min_low_pct"] <= plan["stop_pct"]
        hit_target = plan["max_high_pct"] >= plan["target_pct"]
        # Conservative tie-break: if both levels traded, assume the stop first.
        plan["managed_return"] = np.where(
            hit_stop, plan["stop_pct"],
            np.where(hit_target, plan["target_pct"], plan["fwd_return"]),
        )
        print(f"n={len(plan)}  stop hit {hit_stop.mean()*100:.0f}%  "
              f"target hit {hit_target.mean()*100:.0f}%")
        print(f"  buy-and-hold to horizon : {plan['fwd_return'].mean():+.2f}%")
        print(f"  stop/target managed     : {plan['managed_return'].mean():+.2f}%")
        print(f"  same-date universe      : {plan['universe_return'].mean():+.2f}%")

    section("9 · ROBUSTNESS")
    board_selected, period_returns, _excess = board_simulation(frame, 10, ("long", "short"))
    if not board_selected.empty:
        print("top-10 board, dropping the single best and worst period:")
        trimmed = period_returns.sort_values().iloc[1:-1]
        print(f"  full    mean {period_returns.mean():+.2f}%  n={len(period_returns)}")
        print(f"  trimmed mean {trimmed.mean():+.2f}%  n={len(trimmed)}")
        print("\nconcentration — share of board picks by symbol (top 8):")
        print((board_selected["symbol"].value_counts(normalize=True).head(8) * 100).round(1).to_string())
        halves = np.array_split(period_returns.sort_index(), 2)
        for name, half in zip(("first half", "second half"), halves):
            print(f"  {name}: {half.mean():+.2f}% per period over {len(half)} periods "
                  f"({half.index.min().date()} -> {half.index.max().date()})")

    section("10 · REWARD/RISK FLOOR ARTIFACT  (scanner.py:511)")
    # When the whole 20-80 band sits on one side of zero, adverse_move is 0 and
    # reward_risk becomes expected_move / 0.5 -- an automatic pass on the
    # MIN_REWARD_RISK gate. Does that group actually behave better?
    eligible_all = frame[frame["eligible"] == 1].copy()
    if not eligible_all.empty:
        eligible_all["floored"] = eligible_all["adverse_move"] <= 0.0
        rows = []
        for value, group in eligible_all.groupby("floored"):
            mean_excess, t_excess, periods = clustered_t(group, "excess_vs_universe")
            rows.append({
                "band": "one-sided (risk floored to 0.5)" if value else "crosses zero",
                "n": len(group),
                "share_%": round(100 * len(group) / len(eligible_all), 1),
                "median_reward_risk": round(group["reward_risk"].median(), 2),
                "mean_%": round(group["strategy_return"].mean(), 2),
                "vs_univ_%": round(mean_excess, 2),
                "t": round(t_excess, 2),
                "realized_adverse_%": round(group["min_low_pct"].mean(), 2),
            })
        print(pd.DataFrame(rows).to_string(index=False))
        print("\n'realized_adverse_%' is the mean worst intraperiod drawdown that")
        print("actually occurred, so a floored risk estimate can be checked against it.")

    section("11 · BY SHARE PRICE AT THE SIGNAL  (size / liquidity proxy)")
    eligible_price = frame[frame["eligible"] == 1].copy()
    if not eligible_price.empty:
        eligible_price["price_bucket"] = pd.cut(
            eligible_price["entry_price"],
            [0, 5, 15, 50, 1e9],
            labels=["<$5", "$5-15", "$15-50", ">$50"],
        )
        rows = []
        for value, group in eligible_price.groupby("price_bucket", observed=True):
            mean_excess, t_excess, periods = clustered_t(group, "excess_vs_universe")
            rows.append({
                "price": value, "n": len(group),
                "mean_%": round(group["strategy_return"].mean(), 2),
                "median_%": round(group["strategy_return"].median(), 2),
                "vs_univ_%": round(mean_excess, 2), "t": round(t_excess, 2),
                "win_%": round((group["strategy_return"] > 0).mean() * 100, 1),
                "stdev_%": round(group["strategy_return"].std(), 1),
            })
        print(pd.DataFrame(rows).to_string(index=False))

    section("12 · COSTS AND BOOTSTRAP CONFIDENCE")
    for top_k in (5, 10):
        for sides, label in ((("long",), "long only"), (("long", "short"), "long+short")):
            _sel, period_returns, excess = board_simulation(frame, top_k, sides)
            if period_returns.empty:
                continue
            print(f"\ntop{top_k} {label}: every position is closed and replaced each "
                  f"period, so one round trip per name per 21 sessions.")
            for cost_bps in (0, 10, 25, 50):
                net = period_returns - cost_bps / 100
                curve, total, cagr, drawdown = compound(net)
                print(f"   round-trip {cost_bps:>2} bps -> per-period {net.mean():+.2f}%  "
                      f"CAGR {cagr:+6.1f}%  total {total:+8.1f}%")
            rng = np.random.default_rng(7)
            values = period_returns.to_numpy()
            # Stationary block bootstrap: 4-period blocks keep any regime
            # persistence intact instead of assuming independent periods.
            block, draws = 4, 4000
            samples = np.empty(draws)
            blocks_needed = int(np.ceil(len(values) / block))
            for i in range(draws):
                starts = rng.integers(0, len(values) - block + 1, blocks_needed)
                resampled = np.concatenate([values[s:s + block] for s in starts])
                samples[i] = resampled[: len(values)].mean()
            low, high = np.percentile(samples, [2.5, 97.5])
            print(f"   block-bootstrap 95% CI on per-period return: "
                  f"[{low:+.2f}%, {high:+.2f}%]   "
                  f"P(mean <= 0) = {100 * (samples <= 0).mean():.1f}%")
            ex_low, ex_high = np.percentile(
                [excess.sample(len(excess), replace=True, random_state=i).mean()
                 for i in range(1500)], [2.5, 97.5])
            print(f"   excess vs same-date universe 95% CI: "
                  f"[{ex_low:+.2f}%, {ex_high:+.2f}%]")

    section("13 · REGIME BREAKDOWN  (state known at the signal date only)")
    context = pd.read_csv(
        os.path.join(SCRATCH, "market_context.csv"), index_col=0, parse_dates=True
    ).sort_index()
    context["sma200"] = context["Market"].rolling(200).mean()
    context["uptrend"] = context["Market"] > context["sma200"]
    context["vix_pct"] = context["VIX"].rolling(252, min_periods=60).rank(pct=True) * 100
    regime = context.reindex(sorted(frame["as_of"].unique()), method="ffill")
    labels = pd.DataFrame({
        "trend": np.where(regime["uptrend"], "SPY > 200dma", "SPY < 200dma"),
        "vol": pd.cut(regime["vix_pct"], [0, 33, 67, 100],
                      labels=["calm VIX", "mid VIX", "stressed VIX"]),
    }, index=regime.index)
    tagged = frame.join(labels, on="as_of")

    for column in ("trend", "vol"):
        rows = []
        for value, group in tagged.groupby(column, observed=True):
            eligible_group = group[group["eligible"] == 1]
            for side in ("long", "short"):
                side_group = eligible_group[eligible_group["side"] == side]
                if len(side_group) < 20:
                    continue
                mean_excess, t_excess, periods = clustered_t(
                    side_group, "excess_vs_universe"
                )
                rows.append({
                    "regime": f"{value} / {side}",
                    "n": len(side_group),
                    "periods": periods,
                    "mean_%": round(side_group["strategy_return"].mean(), 2),
                    "vs_univ_%": round(mean_excess, 2),
                    "t": round(t_excess, 2),
                    "win_%": round((side_group["strategy_return"] > 0).mean() * 100, 1),
                })
        if rows:
            print(pd.DataFrame(rows).to_string(index=False))
            print()


if __name__ == "__main__":
    main()
