import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scorecard import ForecastObservation, calibration_summary  # noqa: E402


def observation(
    identifier,
    probability_up,
    realized_return,
    calibrated=None,
    baseline=50.0,
    status="graded",
):
    return ForecastObservation(
        id=identifier,
        symbol=f"S{identifier}",
        as_of_date="2026-01-05",
        horizon_days=21,
        horizon_date="2026-02-03",
        status=status,
        realized_return=realized_return,
        outcome_date="2026-02-03",
        entry_date="2026-01-06",
        side="long",
        tier="moderate",
        probability_up=probability_up,
        baseline_up_rate=baseline,
        calibrated_probability_up=calibrated,
        universe_id=None,
        data_vintage=None,
    )


class CalibrationSummaryTests(unittest.TestCase):
    def test_reports_unavailable_without_matured_probabilities(self):
        summary = calibration_summary([])

        self.assertFalse(summary["available"])
        self.assertIn("probability", summary["reason"])

    def test_ignores_forecasts_missing_a_probability_or_outcome(self):
        summary = calibration_summary(
            [
                observation(1, None, 4.0),
                observation(2, 70.0, None),
                observation(3, 60.0, 2.0),
            ]
        )

        self.assertEqual(summary["sample"], 1)

    def test_overconfident_forecasts_score_worse_than_the_base_rate(self):
        # Every call states 90% up; only half resolve up.
        matured = [
            observation(index, 90.0, 5.0 if index % 2 == 0 else -5.0)
            for index in range(10)
        ]

        summary = calibration_summary(matured)

        self.assertEqual(summary["realized_up_rate"], 50.0)
        self.assertGreater(summary["brier_raw"], summary["brier_base_rate"])
        self.assertLess(summary["skill_raw_points"], 0)

    def test_calibrated_value_is_scored_when_recorded(self):
        # Raw is wildly overconfident, the published value is honest, so the
        # published Brier must beat the raw one.
        matured = [
            observation(
                index,
                95.0,
                5.0 if index % 2 == 0 else -5.0,
                calibrated=50.0,
            )
            for index in range(10)
        ]

        summary = calibration_summary(matured)

        self.assertEqual(summary["calibrated_sample"], 10)
        self.assertLess(summary["brier_published"], summary["brier_raw"])

    def test_falls_back_to_raw_when_no_calibrated_value_was_recorded(self):
        matured = [
            observation(index, 60.0, 1.0 if index % 2 == 0 else -1.0)
            for index in range(6)
        ]

        summary = calibration_summary(matured)

        self.assertEqual(summary["calibrated_sample"], 0)
        self.assertEqual(summary["brier_published"], summary["brier_raw"])

    def test_buckets_expose_the_gap_between_stated_and_realized(self):
        matured = [
            observation(index, 72.0, 3.0 if index < 6 else -3.0)
            for index in range(10)
        ]

        summary = calibration_summary(matured)
        bucket = next(row for row in summary["buckets"] if row["bucket"] == "70-100")

        self.assertEqual(bucket["count"], 10)
        self.assertEqual(bucket["predicted_up"], 72.0)
        self.assertEqual(bucket["realized_up"], 60.0)
        self.assertEqual(bucket["gap_points"], -12.0)


if __name__ == "__main__":
    unittest.main()
