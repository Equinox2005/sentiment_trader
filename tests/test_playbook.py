import math
import unittest

import pandas as pd

from playbook import (
    MATCH_SPACING,
    MAX_MATCHES,
    _align_context,
    _calendar_years,
    _compute_features,
    _first_touch,
    _match_record,
    _normalize_history,
    _prepare_matrices,
    _rank_matches,
    _rank_matches_batch,
    _sampling_config,
    build_playbook,
)


def make_history(periods=1800, trend=0.045, wave_amplitude=9.0):
    index = pd.date_range("2018-01-02", periods=periods, freq="B", tz="UTC")
    close = pd.Series(
        [
            100
            + step * trend
            + wave_amplitude * math.sin(step / 29)
            + (wave_amplitude / 2) * math.sin(step / 7)
            for step in range(periods)
        ],
        index=index,
    )
    open_price = pd.Series(
        [
            value * (1 + 0.0025 * math.sin(step / 3))
            for step, value in enumerate(close)
        ],
        index=index,
    )
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": pd.concat([open_price, close], axis=1).max(axis=1) * 1.008,
            "Low": pd.concat([open_price, close], axis=1).min(axis=1) * 0.992,
            "Close": close,
            "Volume": [
                1_000_000 + 240_000 * math.sin(step / 11)
                for step in range(periods)
            ],
        },
        index=index,
    )


def make_context(index):
    market = pd.Series(
        [400 + step * 0.06 + 12 * math.sin(step / 41) for step in range(len(index))],
        index=index,
    )
    vix = pd.Series(
        [19 + 5 * math.sin(step / 23) for step in range(len(index))],
        index=index,
    )
    return pd.DataFrame({"Market": market, "VIX": vix}, index=index)


class PlaybookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = make_history()
        cls.context = make_context(cls.history.index)
        cls.result = build_playbook(cls.history, context=cls.context)

    def test_builds_rich_playbook_from_long_history(self):
        result = self.result

        self.assertTrue(result["available"])
        self.assertGreater(result["stats"]["count"], 20)
        self.assertLessEqual(result["stats"]["count"], MAX_MATCHES)
        self.assertGreaterEqual(len(result["fingerprint"]["cards"]), 6)
        self.assertIn("Market trend", result["matching"]["features_used"])
        self.assertIn("21-day chart shape", result["matching"]["features_used"])
        self.assertIn(result["forecast"]["direction"], {"bullish", "bearish", "neutral"})
        self.assertTrue(1 <= result["forecast"]["probability_up"] <= 99)
        self.assertTrue(result["setup"].startswith("Today is a"))
        self.assertEqual(len(result["projection"]["days"]), 22)
        self.assertGreaterEqual(len(result["ghost_paths"]), 8)

    def test_walk_forward_validation_is_separate_and_explicit(self):
        validation = self.result["validation"]

        self.assertTrue(validation["available"])
        self.assertGreaterEqual(validation["sample_size"], 5)
        self.assertLessEqual(validation["accuracy_low"], validation["accuracy"])
        self.assertLessEqual(validation["accuracy"], validation["accuracy_high"])
        self.assertIn(
            validation["grade"],
            {"positive", "mixed", "weak", "limited"},
        )
        self.assertEqual(validation["selection"]["profiles_tested"], 4)

    def test_short_history_is_unavailable(self):
        result = build_playbook(make_history(periods=120))

        self.assertFalse(result["available"])
        self.assertIn("history", result["reason"])

    def test_matches_include_path_outcomes_and_scores(self):
        for match in self.result["matches"]:
            self.assertRegex(match["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(1 <= match["quality"] <= 99)
            self.assertLessEqual(match["max_drawdown"], match["max_upside"])
            self.assertIsInstance(match["fwd_21d"], float)
            self.assertNotIn("%", str(match["quality"]))

    def test_matches_are_independent_episodes(self):
        positions = sorted(
            self.history.index.get_indexer(
                [pd.Timestamp(match["date"], tz="UTC") for match in self.result["matches"]]
            )
        )
        for earlier, later in zip(positions, positions[1:]):
            self.assertGreaterEqual(later - earlier, MATCH_SPACING)

    def test_probability_is_shrunk_toward_asset_base_rate(self):
        forecast = self.result["forecast"]

        self.assertTrue(0 <= forecast["baseline_up_rate"] <= 100)
        self.assertAlmostEqual(
            forecast["edge_points"],
            forecast["analog_probability_up"] - forecast["baseline_up_rate"],
            delta=1.1,
        )
        self.assertLessEqual(
            forecast["probability_low"],
            forecast["analog_probability_up"],
        )
        self.assertLessEqual(
            forecast["analog_probability_up"],
            forecast["probability_high"],
        )

    def test_news_adjustment_is_bounded_and_visible(self):
        result = build_playbook(
            self.history,
            context=self.context,
            news_score=1.0,
            news_count=20,
        )

        self.assertEqual(result["forecast"]["news_adjustment_points"], 5.0)
        self.assertLessEqual(
            abs(
                result["forecast"]["probability_up"]
                - result["forecast"]["analog_probability_up"]
            ),
            5,
        )

    def test_rsi_handles_a_lossless_rally(self):
        index = pd.date_range("2020-01-01", periods=320, freq="B", tz="UTC")
        history = pd.DataFrame({"Close": range(100, 420)}, index=index)
        features = _compute_features(_normalize_history(history))

        self.assertEqual(features["rsi"].iloc[-1], 100.0)

    def test_all_up_history_keeps_beta_parameters_valid(self):
        index = pd.date_range("2012-01-02", periods=1800, freq="B", tz="UTC")
        history = pd.DataFrame(
            {"Close": [100 + step * 0.1 for step in range(len(index))]},
            index=index,
        )

        result = build_playbook(history)

        self.assertTrue(result["available"])
        self.assertTrue(1 <= result["forecast"]["probability_up"] <= 99)

    def test_path_excursions_use_intraday_highs_and_lows(self):
        frame = _normalize_history(make_history(periods=400))
        position = 300
        entry = frame["Close"].iloc[position]
        frame.loc[frame.index[position], "High"] = entry * 1.50
        frame.loc[frame.index[position], "Low"] = entry * 0.50
        frame.loc[frame.index[position + 1], "High"] = entry * 1.20
        frame.loc[frame.index[position + 1], "Low"] = entry * 0.80
        record = _match_record(
            frame,
            _compute_features(frame),
            position,
            distance=0.0,
            include_path=True,
        )

        self.assertAlmostEqual(record["max_upside"], 20.0)
        self.assertAlmostEqual(record["max_drawdown"], -20.0)
        self.assertEqual(
            _first_touch(record["low_path"], record["high_path"], -10, 10),
            "stop",
        )

    def test_calendar_years_do_not_overstate_crypto_history(self):
        index = pd.date_range("2016-01-01", periods=3653, freq="D", tz="UTC")

        self.assertAlmostEqual(_calendar_years(index), 10.0, delta=0.1)

    def test_context_alignment_preserves_local_session_dates(self):
        source_index = pd.DatetimeIndex(
            [
                pd.Timestamp("2026-01-05 00:00", tz="America/New_York"),
                pd.Timestamp("2026-01-06 00:00", tz="America/New_York"),
            ]
        )
        target_index = pd.DatetimeIndex(
            [pd.Timestamp("2026-01-06 00:00", tz="Asia/Tokyo")]
        )
        context = pd.DataFrame(
            {"Market": [8.0, 9.0], "VIX": [18.0, 19.0]},
            index=source_index,
        )

        aligned = _align_context(context, target_index)

        self.assertEqual(aligned["Market"].iloc[0], 8.0)

    def test_crypto_uses_calendar_month_windows(self):
        index = pd.date_range("2021-01-01", periods=1500, freq="D", tz="UTC")
        values = [
            100 + step * 0.04 + 8 * math.sin(step / 31)
            for step in range(len(index))
        ]
        history = pd.DataFrame({"Close": values}, index=index)

        config = _sampling_config(_normalize_history(history))
        result = build_playbook(history)

        self.assertEqual(config["annualizer"], 365)
        self.assertEqual(result["forecast"]["horizon_days"], 30)
        self.assertEqual(result["matching"]["shape_window_days"], 30)
        self.assertEqual(result["matching"]["independence_days"], 60)
        self.assertEqual(len(result["projection"]["days"]), 31)

    def test_vectorized_shapes_match_reference_loop(self):
        from playbook import _compute_shapes

        close = self.history["Close"].iloc[:180]
        for window in (21, 30):
            actual = _compute_shapes(close, window)
            expected = pd.DataFrame(
                float("nan"),
                index=range(len(close)),
                columns=range(window + 1),
            ).to_numpy()
            values = close.to_numpy(dtype=float)
            for end in range(window, len(values)):
                path = pd.Series(
                    [
                        math.log(value / values[end - window])
                        for value in values[end - window : end + 1]
                    ]
                )
                spread = path.std(ddof=0)
                expected[end] = (
                    ((path - path.mean()) / spread).to_numpy()
                    if spread >= 1e-9
                    else 0.0
                )
            self.assertTrue(
                pd.DataFrame(actual).fillna(0).round(12).equals(
                    pd.DataFrame(expected).fillna(0).round(12)
                )
            )

    def test_batch_ranker_matches_single_anchor_results(self):
        from playbook import _compute_shapes

        frame = _normalize_history(self.history)
        config = _sampling_config(frame)
        features = _compute_features(frame, self.context, config)
        shapes = _compute_shapes(frame["Close"], config["shape_days"])
        prepared = _prepare_matrices(frame, features, shapes)
        anchors = [1200, 1450]
        batch = _rank_matches_batch(
            frame,
            features,
            shapes,
            anchors,
            "balanced",
            include_paths=False,
            config=config,
            prepared=prepared,
        )
        for anchor in anchors:
            single = _rank_matches(
                frame,
                features,
                shapes,
                anchor,
                "balanced",
                include_paths=False,
                config=config,
            )
            self.assertEqual(
                [(item["position"], item["quality"]) for item in batch[anchor]],
                [(item["position"], item["quality"]) for item in single],
            )

    def test_trade_plan_uses_real_path_extremes_or_abstains(self):
        plan = self.result["trade_plan"]

        if plan["action"] == "consider_buying":
            self.assertLess(plan["stop"], plan["entry"])
            self.assertGreater(plan["target"], plan["entry"])
            self.assertTrue(0 <= plan["matched_path_hit_rate"] <= 100)
            self.assertIn("actual intramonth paths", plan["note"])
        else:
            self.assertIn(plan["action"], {"wait", "avoid_or_exit"})
            self.assertIn("note", plan)


if __name__ == "__main__":
    unittest.main()
