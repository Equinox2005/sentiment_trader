"""Blind historical simulation of the Playbook board.

For every (symbol, as-of session) cell the engine is handed a price history and
market context physically truncated at that session, runs the identical audited
analog matcher the live board uses, and produces a verdict. The realized
forward outcome is read afterwards from the untruncated series using the exact
convention the production grader settles on: buy the OPEN of the session after
the signal, measure to the CLOSE of the 21st session after the signal.

Nothing after the as-of session is visible to the forecast.
"""

from __future__ import annotations

import csv
import os
import random
import sqlite3
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

import market_data as md  # noqa: E402
from scanner import rank_analysis  # noqa: E402

DB = os.environ.get(
    "PLAYBOOK_DATA_CACHE",
    os.path.join(REPO_ROOT, "instance", "playbook.sqlite3"),
)
CONTEXT_CSV = os.path.join(HERE, "market_context.csv")
OUT_CSV = os.path.join(HERE, "replay_2016_2026.csv")

HORIZON = 21
STEP = 21                 # non-overlapping holding windows
START_DATE = "2016-01-01"
MIN_PRIOR_BARS = 1000     # ~4 years of candidate history before the engine runs
N_SYMBOLS = int(os.environ.get("BT_SYMBOLS", "180"))
WORKERS = int(os.environ.get("BT_WORKERS", "14"))
SEED = 20260802

FIELDS = [
    "symbol", "as_of", "prior_bars",
    "available", "unavailable_reason",
    "eligible", "side", "tier", "signal", "score",
    "direction", "analog_direction", "probability_up", "edge_points",
    "evidence", "agreement", "grade", "brier_skill",
    "expected_move", "adverse_move", "reward_risk", "typical", "band_low", "band_high",
    "plan_action", "stop_pct", "target_pct",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "fwd_return", "fwd_close_to_close", "min_low_pct", "max_high_pct",
    "spy_fwd_return", "runtime_s",
]


def read_prices(symbol, connection):
    rows = connection.execute(
        "SELECT session_date, open, high, low, close, volume FROM price_bars "
        "WHERE symbol = ? ORDER BY session_date",
        (symbol,),
    ).fetchall()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(
        rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]
    )
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.set_index("Date").sort_index()


def load_context():
    context = pd.read_csv(CONTEXT_CSV, index_col=0, parse_dates=True)
    return context.sort_index()


def build_date_grid(connection):
    spy = read_prices("SPY", connection)
    sessions = [d for d in spy.index if d >= pd.Timestamp(START_DATE)]
    # Only sessions whose full 21-session forward window exists in SPY.
    usable = sessions[: max(0, len(sessions) - HORIZON)]
    grid = usable[::STEP]

    # BT_CONSECUTIVE=anchor:length,... expands each anchor into consecutive
    # sessions. The default grid is 21 sessions apart, so it cannot say how much
    # the board changes from one day to the next; this can.
    runs = os.environ.get("BT_CONSECUTIVE", "").strip()
    if runs:
        positions = {d: i for i, d in enumerate(usable)}
        chosen = []
        for chunk in runs.split(","):
            anchor, _, length = chunk.partition(":")
            start = pd.Timestamp(anchor.strip())
            index = next(
                (i for d, i in positions.items() if d >= start), None
            )
            if index is None:
                continue
            span = int(length or 5)
            chosen.extend(usable[index : index + span])
        return sorted(set(chosen))

    # The default grid is one of 21 possible phases. BT_RANDOM_DATES samples
    # sessions that are deliberately *not* on it, which is the only way to tell
    # whether a result is a property of the strategy or of the chosen phase.
    sample = int(os.environ.get("BT_RANDOM_DATES", "0"))
    if sample <= 0:
        return grid

    on_grid = set(grid)
    # Keep a horizon's clearance from the ends so every draw has a full window.
    candidates = [d for d in usable[HORIZON:] if d not in on_grid]
    rng = random.Random(SEED + 7)
    chosen = rng.sample(candidates, min(sample, len(candidates)))
    return sorted(chosen)


def choose_universe(connection):
    rows = connection.execute(
        "SELECT symbol, COUNT(*) n, MAX(session_date) b FROM price_bars "
        "GROUP BY symbol HAVING n >= 2500 AND b >= '2026-07-25'"
    ).fetchall()
    symbols = sorted(
        r[0] for r in rows
        if r[0].isalpha() and len(r[0]) <= 5 and r[0] not in {"SPY"}
    )
    rng = random.Random(SEED)
    rng.shuffle(symbols)
    return sorted(symbols[:N_SYMBOLS])


def spy_forward_returns(connection, dates):
    spy = read_prices("SPY", connection)
    index = list(spy.index)
    position_of = {d: i for i, d in enumerate(index)}
    opens = spy["Open"].to_numpy(float)
    closes = spy["Close"].to_numpy(float)
    out = {}
    for date in dates:
        position = position_of.get(date)
        if position is None or position + HORIZON >= len(index):
            continue
        out[date] = (closes[position + HORIZON] / opens[position + 1] - 1) * 100
    return out


def run_symbol(symbol, dates, spy_returns):
    """Run every as-of date for one symbol; returns a list of result rows."""
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        history = read_prices(symbol, connection)
    finally:
        connection.close()
    if history.empty:
        return []

    context = load_context()
    index = list(history.index)
    position_of = {d: i for i, d in enumerate(index)}
    opens = history["Open"].to_numpy(float)
    highs = history["High"].to_numpy(float)
    lows = history["Low"].to_numpy(float)
    closes = history["Close"].to_numpy(float)

    results = []
    for date in dates:
        position = position_of.get(date)
        if position is None:
            continue                      # symbol did not trade that session
        if position < MIN_PRIOR_BARS:
            continue                      # not enough candidate history yet
        if position + HORIZON >= len(index):
            continue                      # outcome not yet realized
        entry_position = position + 1
        exit_position = position + HORIZON
        entry_price = float(opens[entry_position])
        exit_price = float(closes[exit_position])
        if not (entry_price > 0 and exit_price > 0):
            continue

        row = {field: "" for field in FIELDS}
        row["symbol"] = symbol
        row["as_of"] = date.date().isoformat()
        row["prior_bars"] = position + 1
        row["entry_date"] = index[entry_position].date().isoformat()
        row["entry_price"] = round(entry_price, 6)
        row["exit_date"] = index[exit_position].date().isoformat()
        row["exit_price"] = round(exit_price, 6)
        row["fwd_return"] = round((exit_price / entry_price - 1) * 100, 4)
        row["fwd_close_to_close"] = round(
            (exit_price / float(closes[position]) - 1) * 100, 4
        )
        window_low = float(lows[entry_position:exit_position + 1].min())
        window_high = float(highs[entry_position:exit_position + 1].max())
        row["min_low_pct"] = round((window_low / entry_price - 1) * 100, 4)
        row["max_high_pct"] = round((window_high / entry_price - 1) * 100, 4)
        row["spy_fwd_return"] = round(spy_returns.get(date, float("nan")), 4)

        started = time.perf_counter()
        try:
            truncated = history.iloc[: position + 1]
            truncated_context = context[context.index <= date]
            analysis = md._build_analysis(
                symbol=symbol,
                history=truncated,
                profile={},
                news=[],
                warnings=[],
                context=truncated_context,
                include_validation=True,
                snapshot_id=f"bt:{symbol}:{row['as_of']}",
            )
        except Exception as exc:                      # noqa: BLE001
            row["available"] = "error"
            row["unavailable_reason"] = f"{type(exc).__name__}: {exc}"[:200]
            row["runtime_s"] = round(time.perf_counter() - started, 2)
            results.append(row)
            continue

        play = analysis.get("playbook", {})
        row["available"] = int(bool(play.get("available")))
        if not play.get("available"):
            row["unavailable_reason"] = str(play.get("reason", ""))[:160]
            row["runtime_s"] = round(time.perf_counter() - started, 2)
            results.append(row)
            continue

        forecast = play["forecast"]
        validation = play.get("validation", {})
        agreement = forecast.get("agreement", {})
        band = forecast["range_21d"]
        plan = play.get("trade_plan", {})
        ranking = rank_analysis(analysis)

        row.update({
            "eligible": int(bool(ranking.get("eligible"))),
            "side": ranking.get("side") or "",
            "tier": ranking.get("tier") or "",
            "signal": ranking.get("signal") or "",
            "score": ranking.get("opportunity_score"),
            "direction": forecast.get("direction"),
            "analog_direction": forecast.get("analog_direction"),
            "probability_up": forecast.get("probability_up"),
            "edge_points": forecast.get("edge_points"),
            "evidence": forecast.get("evidence_score"),
            "agreement": agreement.get("score"),
            "grade": validation.get("grade") or "",
            "brier_skill": validation.get("brier_skill"),
            "expected_move": ranking.get("expected_move"),
            "adverse_move": ranking.get("adverse_move"),
            "reward_risk": ranking.get("reward_risk"),
            "typical": band.get("typical"),
            "band_low": band.get("low"),
            "band_high": band.get("high"),
            "plan_action": plan.get("action", ""),
            "stop_pct": plan.get("stop_pct", ""),
            "target_pct": plan.get("target_pct", ""),
            "runtime_s": round(time.perf_counter() - started, 2),
        })
        results.append(row)
    return results


def worker(payload):
    symbol, dates, spy_returns = payload
    try:
        return symbol, run_symbol(symbol, dates, spy_returns), None
    except Exception:                                  # noqa: BLE001
        return symbol, [], traceback.format_exc()[-600:]


def main():
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dates = build_date_grid(connection)
    universe = choose_universe(connection)
    spy_returns = spy_forward_returns(connection, dates)
    connection.close()

    print(
        f"universe={len(universe)} dates={len(dates)} "
        f"({dates[0].date()} -> {dates[-1].date()}) "
        f"cells={len(universe) * len(dates)}",
        flush=True,
    )

    # Resume: symbols already written stay written. Remaining work is shuffled so
    # a partial result is a random sample of the universe rather than an
    # alphabetical slice of it.
    done = set()
    if os.path.exists(OUT_CSV) and os.path.getsize(OUT_CSV) > 0:
        with open(OUT_CSV, newline="", encoding="utf-8") as handle:
            done = {row["symbol"] for row in csv.DictReader(handle)}
    pending = [symbol for symbol in universe if symbol not in done]
    random.Random(SEED + 1).shuffle(pending)
    print(f"resuming: {len(done)} symbols already complete, {len(pending)} pending",
          flush=True)

    payloads = [(symbol, dates, spy_returns) for symbol in pending]
    written = 0
    completed = 0
    started = time.time()
    fresh = not done
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if fresh:
            writer.writeheader()
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(worker, payload) for payload in payloads]
            for future in as_completed(futures):
                symbol, rows, error = future.result()
                completed += 1
                if error:
                    print(f"FAILED {symbol}: {error}", flush=True)
                for row in rows:
                    writer.writerow(row)
                written += len(rows)
                handle.flush()
                elapsed = time.time() - started
                rate = completed / max(elapsed, 1e-9)
                remaining = (len(payloads) - completed) / max(rate, 1e-9)
                print(
                    f"[{completed}/{len(payloads)}] {symbol} rows={len(rows)} "
                    f"total={written} elapsed={elapsed/60:.1f}m "
                    f"eta={remaining/60:.1f}m",
                    flush=True,
                )
    print(f"DONE rows={written} elapsed={(time.time()-started)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
