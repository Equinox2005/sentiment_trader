import inspect
import math
import tempfile
import unittest

import pandas as pd

import scorecard
from scorecard import (
    ForecastObservation,
    ScorecardService,
    build_scorecard,
    direction_adjusted_return,
)
from storage import PlaybookStore


def observation(
    identifier,
    *,
    side="long",
    realized_return=1.0,
    status="graded",
    as_of_date="2025-01-02",
    horizon_date="2025-02-03",
    outcome_date="2025-02-03",
    tier="moderate",
    horizon_days=21,
    probability_up=60,
    baseline_up_rate=55,
):
    return ForecastObservation(
        id=identifier,
        symbol=f"S{identifier}",
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        horizon_date=horizon_date,
        status=status,
        realized_return=realized_return,
        outcome_date=outcome_date,
        entry_date="2025-01-03",
        side=side,
        tier=tier,
        probability_up=probability_up,
        baseline_up_rate=baseline_up_rate,
        universe_id=1,
        data_vintage=f"vintage-{identifier}",
    )


class ScorecardMetricTests(unittest.TestCase):
    def test_short_stock_decline_is_positive_signal_return(self):
        self.assertEqual(direction_adjusted_return("short", -12.0), 12.0)

    def test_headline_is_suppressed_below_thirty_matured_forecasts(self):
        observations = [observation(index) for index in range(29)]

        report = build_scorecard(
            observations,
            benchmark_returns={item.id: 0.5 for item in observations},
            as_of_date="2025-03-01",
        )

        self.assertEqual(report["counts"]["matured"], 29)
        self.assertFalse(report["headline"]["available"])
        self.assertIsNone(report["headline"]["metrics"])

    def test_hit_rate_uses_each_forecasts_base_rate_boundary(self):
        below_half_but_above_base_rate = observation(
            1,
            probability_up=49,
            baseline_up_rate=45,
            realized_return=3,
        )

        report = build_scorecard(
            [below_half_but_above_base_rate],
            benchmark_returns={1: 1.0},
            as_of_date="2025-03-01",
            minimum_sample=1,
        )

        self.assertEqual(report["headline"]["metrics"]["hit_rate"], 100.0)

    def test_mixed_long_short_fixture_matches_hand_calculation(self):
        observations = [
            observation(1, side="long", realized_return=10, tier="strong"),
            observation(
                2,
                side="short",
                realized_return=-5,
                probability_up=40,
                baseline_up_rate=55,
            ),
            observation(3, side="long", realized_return=-2),
            observation(
                4,
                side="short",
                realized_return=4,
                probability_up=40,
                baseline_up_rate=55,
            ),
        ]

        report = build_scorecard(
            observations,
            benchmark_returns={1: 8, 2: -3, 3: 1, 4: 2},
            as_of_date="2025-03-01",
            minimum_sample=1,
            bootstrap_samples=200,
        )
        metrics = report["headline"]["metrics"]

        self.assertAlmostEqual(metrics["mean_signal_return"], 2.25)
        self.assertAlmostEqual(metrics["median_signal_return"], 1.5)
        self.assertAlmostEqual(
            metrics["standard_deviation"],
            math.sqrt(124.75 / 3),
            places=4,
        )
        self.assertAlmostEqual(metrics["mean_benchmark_return"], 2.5)
        self.assertAlmostEqual(metrics["mean_excess_return"], -0.25)
        self.assertEqual(metrics["hit_rate"], 50.0)
        self.assertEqual(metrics["sample_size"], 4)
        self.assertIsNotNone(metrics["bootstrap_interval"])
        self.assertEqual(
            set(report["breakdowns"]),
            {"side", "tier", "horizon", "cohort"},
        )

    def test_pending_matured_and_expired_ungraded_are_separate(self):
        observations = [
            observation(1),
            observation(
                2,
                status="pending",
                realized_return=None,
                horizon_date="2025-04-01",
                outcome_date=None,
            ),
            observation(
                3,
                status="pending",
                realized_return=None,
                horizon_date="2025-02-01",
                outcome_date=None,
            ),
        ]

        report = build_scorecard(observations, as_of_date="2025-03-01")

        self.assertEqual(
            report["counts"],
            {
                "total": 3,
                "pending": 1,
                "matured": 1,
                "expired_ungraded": 1,
                "scored_matured": 1,
            },
        )

    def test_due_today_forecast_is_pending_until_grading_can_complete(self):
        due_today = observation(
            1,
            status="pending",
            realized_return=None,
            horizon_date="2025-03-01",
            outcome_date=None,
        )

        report = build_scorecard([due_today], as_of_date="2025-03-01")

        self.assertEqual(report["counts"]["pending"], 1)
        self.assertEqual(report["counts"]["expired_ungraded"], 0)

    def test_benchmark_comparison_uses_only_paired_signal_forecasts(self):
        observations = [
            observation(1, realized_return=10),
            observation(2, realized_return=-10),
        ]

        report = build_scorecard(
            observations,
            benchmark_returns={
                1: {
                    "return": 8,
                    "constituent_count": 80,
                    "universe_count": 100,
                }
            },
            as_of_date="2025-03-01",
            minimum_sample=1,
        )
        metrics = report["headline"]["metrics"]

        self.assertEqual(metrics["mean_signal_return"], 0.0)
        self.assertEqual(metrics["mean_paired_signal_return"], 10.0)
        self.assertEqual(metrics["mean_benchmark_return"], 8.0)
        self.assertEqual(metrics["mean_excess_return"], 2.0)
        self.assertEqual(metrics["benchmark_sample_size"], 1)
        self.assertEqual(metrics["benchmark_coverage_median"], 80.0)

    def test_delayed_snapshot_reconstruction_excludes_future_ledger_state(self):
        class Store:
            @staticmethod
            def list_all_forecasts():
                return [
                    {
                        "id": 1,
                        "symbol": "OLD",
                        "as_of_date": "2025-01-02",
                        "horizon_days": 21,
                        "horizon_date": "2025-02-03",
                        "status": "graded",
                        "realized_return": 5.0,
                        "outcome_date": "2025-02-03",
                        "entry_date": "2025-01-03",
                        "side": "long",
                        "tier": "moderate",
                        "probability_up": 60,
                        "baseline_up_rate": 55,
                        "universe_id": None,
                        "data_vintage": "old",
                        "created_at": "2025-01-02T22:00:00+00:00",
                        "graded_at": "2025-02-04T22:00:00+00:00",
                    },
                    {
                        "id": 2,
                        "symbol": "FUTURE",
                        "as_of_date": "2025-02-04",
                        "horizon_days": 21,
                        "horizon_date": "2025-03-05",
                        "status": "pending",
                        "realized_return": None,
                        "outcome_date": None,
                        "entry_date": None,
                        "side": "long",
                        "tier": "moderate",
                        "probability_up": 60,
                        "baseline_up_rate": 55,
                        "universe_id": None,
                        "data_vintage": "future",
                        "created_at": "2025-02-04T22:00:00+00:00",
                        "graded_at": None,
                    },
                ]

        report = ScorecardService(Store()).current(
            as_of_date="2025-01-31",
            as_of_timestamp="2025-02-01T00:00:00+00:00",
        )

        self.assertEqual(report["counts"]["total"], 1)
        self.assertEqual(report["counts"]["matured"], 0)
        self.assertEqual(report["counts"]["pending"], 1)

    def test_scorecard_source_has_no_fixed_probability_threshold(self):
        source = inspect.getsource(scorecard)
        self.assertNotIn("50", source)
        self.assertNotRegex(source, r"probability[^\n]{0,80}(?:==|>=|>)\s*50")


class ScorecardStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = PlaybookStore(f"{self.temp.name}\\playbook.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_equal_weight_benchmark_uses_exact_entry_and_outcome_dates(self):
        universe_id = self.store.save_scan_universe(
            [
                {"symbol": "AAA", "name": "A"},
                {"symbol": "BBB", "name": "B"},
            ],
            "fixture",
        )
        dates = pd.to_datetime(
            ["2025-01-02", "2025-01-03", "2025-01-10", "2025-01-13"],
            utc=True,
        )
        aaa = pd.DataFrame(
            {
                "Open": [10, 100, 999, 20],
                "High": [11, 101, 111, 21],
                "Low": [9, 99, 109, 19],
                "Close": [10, 101, 110, 20],
                "Volume": 1_000_000,
            },
            index=dates,
        )
        bbb = pd.DataFrame(
            {
                "Open": [20, 200, 999, 40],
                "High": [21, 201, 181, 41],
                "Low": [19, 199, 179, 39],
                "Close": [20, 201, 180, 40],
                "Volume": 1_000_000,
            },
            index=dates,
        )
        self.store.save_prices("AAA", aaa, full_refresh=True)
        self.store.save_prices("BBB", bbb, full_refresh=True)

        benchmark = self.store.equal_weight_benchmark_return(
            universe_id,
            "2025-01-03",
            "2025-01-10",
        )

        self.assertAlmostEqual(benchmark["return"], 0.0)
        self.assertEqual(benchmark["constituent_count"], 2)
        self.assertEqual(benchmark["universe_count"], 2)

    def test_scorecard_snapshot_is_append_only_and_never_overwritten(self):
        first = self.store.append_scorecard_snapshot(
            scan_run_id=7,
            session_date="2025-03-01",
            report={"headline": {"available": False}, "counts": {"matured": 0}},
            model_version="model-a",
            git_commit="commit-a",
            config_hash="config-a",
            data_vintage="vintage-a",
            universe_id=3,
        )
        second = self.store.append_scorecard_snapshot(
            scan_run_id=7,
            session_date="2025-03-01",
            report={"headline": {"available": True}, "counts": {"matured": 99}},
            model_version="model-b",
            git_commit="commit-b",
            config_hash="config-b",
            data_vintage="vintage-b",
            universe_id=4,
        )
        snapshots = self.store.list_scorecard_snapshots()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["git_commit"], "commit-a")
        self.assertFalse(snapshots[0]["report"]["headline"]["available"])


if __name__ == "__main__":
    unittest.main()
