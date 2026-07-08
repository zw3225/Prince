import unittest
from unittest.mock import patch

from backend.pipeline import run_trend_search


class ScoringTest(unittest.TestCase):
    def test_scores_are_bounded_and_explained(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            result = run_trend_search("Gen Z pet owners", ["US"], 30)
        all_items = result.content + result.creators + result.products + result.opportunities
        self.assertTrue(all_items)
        for item in all_items:
            self.assertGreaterEqual(item.score, 1)
            self.assertLessEqual(item.score, 100)
            self.assertGreaterEqual(item.confidence, 35)
            self.assertLessEqual(item.confidence, 98)
            self.assertTrue(item.source_url_note)
            self.assertTrue(item.reasons)

    def test_same_input_keeps_same_rank_order(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            first = run_trend_search("portable blender for gym girls", ["US", "UK"], 30)
            second = run_trend_search("portable blender for gym girls", ["US", "UK"], 30)
        self.assertEqual([item.id for item in first.products[:10]], [item.id for item in second.products[:10]])

    def test_opportunities_do_not_expose_product_score_proxy(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            result = run_trend_search("portable blender for gym girls", ["US"], 30)

        self.assertTrue(result.opportunities)
        for item in result.opportunities:
            self.assertNotIn("product_score", item.metrics)
            self.assertFalse(any("热度分" in reason for reason in item.reasons))


if __name__ == "__main__":
    unittest.main()
