import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from calibration import (  # noqa: E402
    DEFAULT_SHRINK,
    calibrate_probability,
    shrink_factor,
)


class CalibrationTests(unittest.TestCase):
    def test_pulls_confident_calls_toward_the_base_rate(self):
        calibrated = calibrate_probability(80, 53)
        self.assertLess(calibrated, 80)
        self.assertGreater(calibrated, 53)

    def test_pulls_bearish_calls_toward_the_base_rate(self):
        calibrated = calibrate_probability(20, 53)
        self.assertGreater(calibrated, 20)
        self.assertLess(calibrated, 53)

    def test_leaves_a_forecast_that_equals_its_baseline_alone(self):
        self.assertAlmostEqual(calibrate_probability(53, 53), 53, places=6)

    def test_preserves_ordering(self):
        baseline = 50
        values = [
            calibrate_probability(raw, baseline) for raw in (20, 35, 50, 65, 80)
        ]
        self.assertEqual(values, sorted(values))

    def test_shrink_of_one_is_a_passthrough(self):
        self.assertAlmostEqual(
            calibrate_probability(75, 50, shrink=1.0), 75, places=6
        )

    def test_shrink_of_zero_quotes_the_baseline(self):
        self.assertAlmostEqual(
            calibrate_probability(90, 47, shrink=0.0), 47, places=6
        )

    def test_output_stays_inside_reportable_bounds(self):
        self.assertLessEqual(calibrate_probability(99.9, 99.9), 99.0)
        self.assertGreaterEqual(calibrate_probability(0.0, 0.0), 1.0)

    def test_rejects_unusable_input(self):
        self.assertIsNone(calibrate_probability(None, 50))
        self.assertIsNone(calibrate_probability(float("nan"), 50))
        self.assertIsNone(calibrate_probability("high", 50))

    def test_environment_override_is_clamped(self):
        original = os.environ.get("PLAYBOOK_PROBABILITY_SHRINK")
        try:
            os.environ["PLAYBOOK_PROBABILITY_SHRINK"] = "5"
            self.assertEqual(shrink_factor(), 1.0)
            os.environ["PLAYBOOK_PROBABILITY_SHRINK"] = "-2"
            self.assertEqual(shrink_factor(), 0.0)
            os.environ["PLAYBOOK_PROBABILITY_SHRINK"] = "not-a-number"
            self.assertEqual(shrink_factor(), DEFAULT_SHRINK)
        finally:
            os.environ.pop("PLAYBOOK_PROBABILITY_SHRINK", None)
            if original is not None:
                os.environ["PLAYBOOK_PROBABILITY_SHRINK"] = original


if __name__ == "__main__":
    unittest.main()
