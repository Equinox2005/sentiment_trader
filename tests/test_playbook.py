import math
import unittest

import pandas as pd

from playbook import build_playbook


def make_series(periods=900, trend=0.12, wave_amplitude=8.0):
    index = pd.date_range("2022-01-03", periods=periods, freq="B", tz="UTC")
    values = [
        100
        + step * trend
        + wave_amplitude * math.sin(step / 17)
        + (wave_amplitude / 2) * math.sin(step / 5)
        for step in range(periods)
    ]
    return pd.Series(values, index=index)


class PlaybookTests(unittest.TestCase):
    def test_builds_full_playbook_from_long_history(self):
        result = build_playbook(make_series())

        self.assertTrue(result["available"])
        self.assertGreaterEqual(result["stats"]["count"], 5)
        self.assertIn(result["verdict"]["direction"], {"bullish", "bearish", "neutral"})
        self.assertTrue(15 <= result["verdict"]["confidence"] <= 90)
        self.assertTrue(result["setup"].startswith("Right now this asset is"))
        self.assertGreaterEqual(len(result["ghost_paths"]), 3)
        self.assertEqual(len(result["ghost_paths"][0]["offsets"]), 22)
        self.assertEqual(result["ghost_paths"][0]["offsets"][0], 0.0)

    def test_short_history_is_unavailable(self):
        result = build_playbook(make_series(periods=120))

        self.assertFalse(result["available"])
        self.assertIn("history", result["reason"])

    def test_matches_have_verifiable_outcomes(self):
        result = build_playbook(make_series())

        for match in result["matches"]:
            self.assertRegex(match["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(1 <= match["similarity"] <= 99)
            self.assertIsInstance(match["fwd_21d"], float)

    def test_matches_are_spaced_apart(self):
        result = build_playbook(make_series())

        dates = sorted(
            pd.Timestamp(match["date"]) for match in result["matches"]
        )
        for earlier, later in zip(dates, dates[1:]):
            self.assertGreaterEqual((later - earlier).days, 42)

    def test_bullish_plan_includes_stop_and_target(self):
        result = build_playbook(make_series())
        plan = result["trade_plan"]

        if plan["action"] == "consider_buying":
            self.assertLess(plan["stop"], plan["entry"])
            self.assertGreater(plan["target"], plan["entry"])
            self.assertGreater(plan["risk_reward"], 0)
        else:
            self.assertIn(plan["action"], {"wait", "avoid_or_exit"})
            self.assertIn("note", plan)

    def test_stats_percentiles_are_ordered(self):
        stats = build_playbook(make_series())["stats"]

        self.assertLessEqual(stats["worst_21d"], stats["p25_21d"])
        self.assertLessEqual(stats["p25_21d"], stats["median_21d"])
        self.assertLessEqual(stats["median_21d"], stats["p75_21d"])
        self.assertLessEqual(stats["p75_21d"], stats["best_21d"])


if __name__ == "__main__":
    unittest.main()
