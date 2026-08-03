import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from performance import build_live_performance  # noqa: E402
from scorecard import ForecastObservation, build_scorecard  # noqa: E402


def observation(identifier, symbol, realized_return, eligible, status="graded"):
    return ForecastObservation(
        id=identifier,
        symbol=symbol,
        as_of_date="2026-01-05",
        horizon_days=21,
        horizon_date="2026-02-03",
        status=status,
        realized_return=realized_return,
        outcome_date="2026-02-03",
        entry_date="2026-01-06",
        side="long",
        tier="moderate",
        probability_up=62.0,
        baseline_up_rate=52.0,
        universe_id=None,
        data_vintage=None,
        eligible=eligible,
    )


def bars(symbol_prices):
    """(session, open, close) triples, matching the shape the store yields."""
    return {
        symbol: [
            (date, open_price, close) for date, open_price, close in rows
        ]
        for symbol, rows in symbol_prices.items()
    }


class ScorecardEligibilityTests(unittest.TestCase):
    def test_rejected_names_are_excluded_from_the_headline(self):
        observations = [
            observation(1, "GOOD", 4.0, eligible=True),
            observation(2, "ALSO", 2.0, eligible=True),
            observation(3, "PENNY", 90.0, eligible=False),
        ]

        report = build_scorecard(
            observations, as_of_date="2026-03-01", minimum_sample=1
        )

        self.assertEqual(report["counts"]["scored_matured"], 2)
        self.assertEqual(report["counts"]["board_rejected"], 1)
        # The 90% outlier would dominate a three-name mean if it leaked in.
        self.assertEqual(report["headline"]["metrics"]["mean_signal_return"], 3.0)

    def test_unrecorded_eligibility_is_kept_and_counted(self):
        observations = [
            observation(1, "OLD", 4.0, eligible=None),
            observation(2, "NEW", 2.0, eligible=True),
        ]

        report = build_scorecard(
            observations, as_of_date="2026-03-01", minimum_sample=1
        )

        self.assertEqual(report["counts"]["scored_matured"], 2)
        self.assertEqual(report["counts"]["board_rejected"], 0)
        self.assertEqual(report["counts"]["eligibility_unrecorded"], 1)


class LivePerformanceEligibilityTests(unittest.TestCase):
    def test_rejected_names_never_reach_the_running_average(self):
        observations = [
            observation(1, "GOOD", None, eligible=True, status="pending"),
            observation(2, "PENNY", None, eligible=False, status="pending"),
        ]
        prices = bars(
            {
                "GOOD": [
                    ("2026-01-05", 10.0, 10.0),
                    ("2026-01-06", 10.0, 11.0),
                ],
                "PENNY": [
                    ("2026-01-05", 0.10, 0.10),
                    ("2026-01-06", 0.10, 0.50),
                ],
            }
        )

        report = build_live_performance(
            observations, prices, evaluation_date="2026-01-06"
        )

        self.assertEqual(report["counts"]["board_rejected"], 1)
        self.assertEqual(report["counts"]["total"], 1)
        symbols = {
            row["symbol"]
            for board in report["leaderboards"]
            for row in board.get("rows", [])
        }
        self.assertNotIn("PENNY", symbols)


if __name__ == "__main__":
    unittest.main()
