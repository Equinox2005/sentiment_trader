import unittest

from sentiment import analyze_financial_text


class FinancialSentimentTests(unittest.TestCase):
    def test_constructive_language_scores_positive(self):
        result = analyze_financial_text(
            "Shares surge after strong earnings beat and an upbeat growth outlook"
        )

        self.assertEqual(result["label"], "Positive")
        self.assertGreater(result["score"], 0.7)
        self.assertIn("surge", result["terms"])
        self.assertIn("beat", result["terms"])

    def test_risk_language_scores_negative(self):
        result = analyze_financial_text(
            "Company warns of a sharp slowdown after profit miss and investigation"
        )

        self.assertEqual(result["label"], "Negative")
        self.assertLess(result["score"], -0.7)

    def test_negation_reverses_lexicon_term(self):
        positive = analyze_financial_text("The outlook is strong")
        negated = analyze_financial_text("The outlook is not strong")

        self.assertGreater(positive["score"], 0)
        self.assertLess(negated["score"], 0)

    def test_unknown_language_is_neutral(self):
        result = analyze_financial_text("Company schedules its annual meeting")

        self.assertEqual(result["label"], "Neutral")
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["terms"], [])


if __name__ == "__main__":
    unittest.main()
