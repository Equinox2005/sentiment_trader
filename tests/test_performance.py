import os
import tempfile
import unittest

import pandas as pd

from performance import (
    AWAITING_ENTRY,
    MATURED,
    OPEN,
    SUSPECT,
    UNPRICED,
    LivePerformanceService,
    build_live_performance,
    latest_market_session,
    mark_position,
)
from scorecard import ForecastObservation
from storage import PlaybookStore

SESSIONS = [
    "2025-01-02",
    "2025-01-03",
    "2025-01-06",
    "2025-01-07",
    "2025-01-08",
]


def observation(
    identifier=1,
    *,
    symbol="AAA",
    side="long",
    as_of_date="2025-01-02",
    horizon_days=3,
    status="pending",
    realized_return=None,
    entry_date=None,
    outcome_date=None,
):
    return ForecastObservation(
        id=identifier,
        symbol=symbol,
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        horizon_date="2025-01-07",
        status=status,
        realized_return=realized_return,
        outcome_date=outcome_date,
        entry_date=entry_date,
        side=side,
        tier="moderate",
        probability_up=60,
        baseline_up_rate=50,
        universe_id=1,
        data_vintage="vintage",
    )


def leaderboard(report, key):
    return next(
        group for group in report["leaderboards"] if group["key"] == key
    )


def symbols(report, key):
    return [row["symbol"] for row in leaderboard(report, key)["rows"]]


def bars(closes, opens=None, sessions=None):
    sessions = sessions or SESSIONS[: len(closes)]
    opens = opens or closes
    return [
        (session, open_price, close_price)
        for session, open_price, close_price in zip(sessions, opens, closes)
    ]


class MarkPositionTests(unittest.TestCase):
    def test_entry_uses_the_session_after_the_signal_not_the_signal_close(self):
        # Signal close 100, next open 110: a grader that entered at the signal
        # close would book a gain the forecast could never have traded.
        series = bars([100.0, 120.0, 130.0], opens=[95.0, 110.0, 125.0])

        position = mark_position(observation(), series, "2025-01-06")

        self.assertEqual(position.entry_date, "2025-01-03")
        self.assertEqual(position.entry_price, 110.0)
        self.assertEqual(position.mark_date, "2025-01-06")
        self.assertEqual(position.mark_price, 130.0)
        self.assertAlmostEqual(position.price_return, 18.1818, places=3)

    def test_short_position_gains_when_the_price_falls(self):
        series = bars([100.0, 100.0, 80.0], opens=[100.0, 100.0, 80.0])

        position = mark_position(
            observation(side="short"),
            series,
            "2025-01-06",
        )

        self.assertAlmostEqual(position.price_return, -20.0)
        self.assertAlmostEqual(position.signed_return, 20.0)

    def test_position_is_open_until_the_horizon_session_arrives(self):
        series = bars([100.0, 100.0, 105.0])

        position = mark_position(observation(), series, "2025-01-06")

        self.assertEqual(position.state, OPEN)
        self.assertEqual(position.sessions_elapsed, 2)

    def test_position_matures_and_stops_at_the_horizon_session(self):
        series = bars([100.0, 100.0, 105.0, 110.0, 999.0])

        position = mark_position(observation(), series, "2025-01-08")

        self.assertEqual(position.state, MATURED)
        self.assertEqual(position.mark_date, "2025-01-07")
        self.assertEqual(position.sessions_elapsed, 3)

    def test_forecast_without_a_traded_session_yet_awaits_entry(self):
        series = bars([100.0])

        position = mark_position(observation(), series, "2025-01-02")

        self.assertEqual(position.state, AWAITING_ENTRY)
        self.assertIsNone(position.signed_return)

    def test_missing_signal_session_leaves_the_position_unpriced(self):
        series = bars(
            [100.0, 110.0],
            sessions=["2025-01-06", "2025-01-07"],
        )

        position = mark_position(observation(), series, "2025-01-07")

        self.assertEqual(position.state, UNPRICED)

    def test_reverse_split_sized_jump_is_withheld_from_the_average(self):
        # A 1-for-10 reverse split arrives as a +900% overnight move. Trusting
        # it would move the average across a thousand forecasts more than the
        # signal does.
        series = bars([1.0, 1.0, 10.0, 10.2], opens=[1.0, 1.0, 10.0, 10.1])

        position = mark_position(observation(), series, "2025-01-07")

        self.assertEqual(position.state, SUSPECT)
        self.assertIsNone(position.signed_return)
        self.assertIsNotNone(position.price_return)

    def test_graded_forecast_reports_the_stored_outcome_not_a_recomputation(self):
        # The scorecard and this page must never quote two different numbers
        # for the same settled forecast.
        position = mark_position(
            observation(
                status="graded",
                realized_return=7.5,
                entry_date="2025-01-03",
                outcome_date="2025-01-07",
            ),
            bars([100.0, 100.0, 105.0, 110.0]),
            "2025-01-08",
        )

        self.assertEqual(position.state, MATURED)
        self.assertAlmostEqual(position.signed_return, 7.5)

    def test_graded_forecast_survives_pruned_price_history(self):
        position = mark_position(
            observation(
                status="graded",
                realized_return=-3.25,
                side="short",
                entry_date="2025-01-03",
                outcome_date="2025-01-07",
            ),
            [],
            "2025-01-08",
        )

        self.assertEqual(position.state, MATURED)
        self.assertAlmostEqual(position.signed_return, 3.25)


class MarketSessionTests(unittest.TestCase):
    def test_thinly_covered_session_does_not_advance_the_board(self):
        # One crypto ticker trading on a Sunday must not mark every equity flat
        # against a session the equity market never had.
        book = {
            f"EQ{index}": bars([10.0, 11.0], sessions=["2025-01-02", "2025-01-03"])
            for index in range(20)
        }
        book["BTC-USD"] = bars(
            [10.0, 11.0, 12.0],
            sessions=["2025-01-02", "2025-01-03", "2025-01-04"],
        )

        self.assertEqual(latest_market_session(book), "2025-01-03")

    def test_evaluation_never_looks_past_the_requested_date(self):
        book = {"AAA": bars([10.0, 11.0, 12.0])}

        self.assertEqual(
            latest_market_session(book, on_or_before="2025-01-03"),
            "2025-01-03",
        )


class LivePerformanceReportTests(unittest.TestCase):
    def test_average_is_the_mean_direction_adjusted_move(self):
        book = {
            "AAA": bars([100.0, 100.0, 110.0]),
            "BBB": bars([100.0, 100.0, 90.0]),
        }
        observations = [
            observation(1, symbol="AAA", side="long"),
            observation(2, symbol="BBB", side="short"),
        ]

        report = build_live_performance(
            observations,
            book,
            evaluation_date="2025-01-06",
        )
        metrics = report["headline"]["metrics"]

        self.assertTrue(report["headline"]["available"])
        self.assertEqual(metrics["sample_size"], 2)
        self.assertAlmostEqual(metrics["average_return"], 10.0)
        self.assertAlmostEqual(metrics["positive_share"], 100.0)

    def test_neutral_forecasts_are_counted_but_never_signed(self):
        book = {"AAA": bars([100.0, 100.0, 110.0])}
        observations = [observation(1, symbol="AAA", side=None)]

        report = build_live_performance(
            observations,
            book,
            evaluation_date="2025-01-06",
        )

        self.assertEqual(report["counts"]["neutral"], 1)
        self.assertEqual(report["counts"]["scored"], 0)
        self.assertFalse(report["headline"]["available"])

    def test_leaderboards_rank_each_side_separately(self):
        book = {
            "LWIN": bars([100.0, 100.0, 130.0]),
            "LMID": bars([100.0, 100.0, 105.0]),
            "LOSS": bars([100.0, 100.0, 70.0]),
            "SWIN": bars([100.0, 100.0, 60.0]),
            "SBAD": bars([100.0, 100.0, 140.0]),
        }
        observations = [
            observation(1, symbol="LWIN", side="long"),
            observation(2, symbol="LMID", side="long"),
            observation(3, symbol="LOSS", side="long"),
            observation(4, symbol="SWIN", side="short"),
            observation(5, symbol="SBAD", side="short"),
        ]

        report = build_live_performance(
            observations,
            book,
            evaluation_date="2025-01-06",
        )

        self.assertEqual(
            symbols(report, "long_winners"),
            ["LWIN", "LMID"],
        )
        self.assertEqual(symbols(report, "short_winners"), ["SWIN"])
        self.assertEqual(symbols(report, "long_losers"), ["LOSS"])
        self.assertEqual(symbols(report, "short_losers"), ["SBAD"])

    def test_top_list_is_never_padded_with_losing_names(self):
        # A "top returns" table that fills to a fixed length with losses would
        # report a winner that is not one.
        book = {
            "WIN": bars([100.0, 100.0, 130.0]),
            "BAD1": bars([100.0, 100.0, 90.0]),
            "BAD2": bars([100.0, 100.0, 80.0]),
        }
        observations = [
            observation(1, symbol="WIN"),
            observation(2, symbol="BAD1"),
            observation(3, symbol="BAD2"),
        ]

        report = build_live_performance(
            observations,
            book,
            evaluation_date="2025-01-06",
            leaderboard_size=50,
        )

        self.assertEqual(symbols(report, "long_winners"), ["WIN"])
        self.assertEqual(leaderboard(report, "long_winners")["total"], 1)

    def test_leaderboard_caps_rows_but_reports_the_full_count(self):
        book = {
            f"S{index:03d}": bars([100.0, 100.0, 100.0 + index])
            for index in range(1, 61)
        }
        observations = [
            observation(index, symbol=f"S{index:03d}")
            for index in range(1, 61)
        ]

        report = build_live_performance(
            observations,
            book,
            evaluation_date="2025-01-06",
            leaderboard_size=50,
        )
        group = leaderboard(report, "long_winners")

        self.assertEqual(group["total"], 60)
        self.assertEqual(len(group["rows"]), 50)
        self.assertEqual(group["rows"][0]["symbol"], "S060")

    def test_empty_side_leaderboard_is_present_and_empty(self):
        book = {"AAA": bars([100.0, 100.0, 110.0])}

        report = build_live_performance(
            [observation(1, symbol="AAA", side="long")],
            book,
            evaluation_date="2025-01-06",
        )

        self.assertEqual(leaderboard(report, "short_winners")["rows"], [])
        self.assertEqual(leaderboard(report, "short_winners")["total"], 0)

    def test_report_is_empty_but_valid_before_any_entry_exists(self):
        book = {"AAA": bars([100.0])}

        report = build_live_performance(
            [observation(1, symbol="AAA")],
            book,
            evaluation_date="2025-01-02",
        )

        self.assertFalse(report["headline"]["available"])
        self.assertEqual(report["counts"]["awaiting_entry"], 1)
        self.assertIn("next session", report["headline"]["reason"])

    def test_benchmark_holds_every_symbol_over_the_identical_window(self):
        book = {
            "AAA": bars([100.0, 100.0, 120.0]),
            "BBB": bars([100.0, 100.0, 100.0]),
            "CCC": bars([100.0, 100.0, 100.0]),
        }

        report = build_live_performance(
            [observation(1, symbol="AAA")],
            book,
            evaluation_date="2025-01-06",
        )
        metrics = report["headline"]["metrics"]

        # AAA +20, BBB 0, CCC 0 -> equal-weight benchmark of 6.67.
        self.assertAlmostEqual(metrics["average_benchmark_return"], 6.6667, places=3)
        self.assertAlmostEqual(metrics["average_excess_return"], 13.3333, places=3)

    def test_progress_reports_sessions_elapsed_against_the_horizon(self):
        book = {"AAA": bars([100.0, 100.0, 105.0])}

        report = build_live_performance(
            [observation(1, symbol="AAA", horizon_days=3)],
            book,
            evaluation_date="2025-01-06",
        )

        self.assertEqual(report["progress"]["sessions_elapsed"], 2)
        self.assertEqual(report["progress"]["sessions_total"], 3)
        self.assertAlmostEqual(report["progress"]["percent_complete"], 66.6667, places=3)


class LivePerformanceServiceTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        self.store = PlaybookStore(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            candidate = f"{self.path}{suffix}"
            if os.path.exists(candidate):
                os.unlink(candidate)

    def _save_prices(self, symbol, closes, opens):
        frame = pd.DataFrame(
            {
                "Open": opens,
                "High": closes,
                "Low": opens,
                "Close": closes,
                "Volume": [1_000] * len(closes),
            },
            index=pd.to_datetime(SESSIONS[: len(closes)]),
        )
        self.store.save_prices(symbol, frame)

    def test_service_marks_the_ledger_from_stored_prices(self):
        self._save_prices("AAA", [100.0, 100.0, 115.0], [100.0, 100.0, 110.0])
        self.store.save_forecast(
            symbol="AAA",
            as_of_date="2025-01-02",
            horizon_days=3,
            horizon_date="2025-01-07",
            payload={
                "entry_price": None,
                "entry_reference": {"session_offset": 1, "price_field": "Open"},
                "signal_close": 100.0,
                "probability_up": 62,
                "baseline_up_rate": 50,
                "direction": "bullish",
                "side": "long",
                "tier": "strong",
                "edge_points": 12,
                "range": {"low": -5, "typical": 3, "high": 9},
                "evidence_score": 70,
                "validation_grade": "positive",
                "horizon_label": "3 trading days",
                "snapshot_id": "snapshot",
                "exchange_timezone": "America/New_York",
            },
        )

        report = LivePerformanceService(self.store).current(
            evaluation_date="2025-01-06"
        )
        metrics = report["headline"]["metrics"]

        self.assertEqual(report["counts"]["scored"], 1)
        self.assertAlmostEqual(metrics["average_return"], 15.0)
        self.assertEqual(symbols(report, "long_winners"), ["AAA"])

    def test_repeated_calls_reuse_the_cached_report(self):
        self._save_prices("AAA", [100.0, 100.0, 115.0], [100.0, 100.0, 110.0])
        service = LivePerformanceService(self.store)

        first = service.current(evaluation_date="2025-01-06")
        second = service.current(evaluation_date="2025-01-06")

        self.assertIs(first, second)

    def test_new_prices_invalidate_the_cached_report(self):
        self._save_prices("AAA", [100.0, 100.0], [100.0, 100.0])
        service = LivePerformanceService(self.store)
        first = service.current(evaluation_date="2025-01-06")

        self._save_prices("AAA", [100.0, 100.0, 115.0], [100.0, 100.0, 110.0])
        second = service.current(evaluation_date="2025-01-06")

        self.assertIsNot(first, second)

    def test_empty_ledger_produces_a_valid_report(self):
        report = LivePerformanceService(self.store).current(
            evaluation_date="2025-01-06"
        )

        self.assertEqual(report["counts"]["total"], 0)
        self.assertFalse(report["headline"]["available"])


class SessionBarTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(handle)
        os.unlink(self.path)
        self.store = PlaybookStore(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            candidate = f"{self.path}{suffix}"
            if os.path.exists(candidate):
                os.unlink(candidate)

    def test_session_bars_returns_sorted_windows_per_symbol(self):
        frame = pd.DataFrame(
            {
                "Open": [1.0, 2.0, 3.0],
                "High": [1.0, 2.0, 3.0],
                "Low": [1.0, 2.0, 3.0],
                "Close": [1.5, 2.5, 3.5],
                "Volume": [10, 10, 10],
            },
            index=pd.to_datetime(SESSIONS[:3]),
        )
        self.store.save_prices("AAA", frame)

        book = self.store.session_bars(["AAA", "MISSING"], "2025-01-03")

        self.assertEqual(list(book), ["AAA"])
        self.assertEqual(
            book["AAA"],
            [("2025-01-03", 2.0, 2.5), ("2025-01-06", 3.0, 3.5)],
        )

    def test_price_fingerprint_changes_when_prices_are_written(self):
        before = self.store.price_ledger_fingerprint()
        frame = pd.DataFrame(
            {
                "Open": [1.0],
                "High": [1.0],
                "Low": [1.0],
                "Close": [1.0],
                "Volume": [10],
            },
            index=pd.to_datetime(["2025-01-02"]),
        )
        self.store.save_prices("AAA", frame)

        self.assertNotEqual(before, self.store.price_ledger_fingerprint())


if __name__ == "__main__":
    unittest.main()
