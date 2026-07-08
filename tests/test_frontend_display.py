from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendDisplayContractTest(unittest.TestCase):
    def test_detail_panel_does_not_surface_internal_scoring_or_proxy_metrics(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        combined = app + "\n" + html

        hidden_terms = [
            "热度分",
            "最高热度",
            "可信度",
            "评分解释",
            "营销信号",
            "核心指标",
            "sales_rank_proxy",
            "listing_stability",
            "topic_match",
            "sentiment",
            "creator_reputation",
            "product_score",
        ]
        for term in hidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, combined)

    def test_detail_panel_uses_verification_sections(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for term in ["这条结果能做什么", "已抓到的数据", "待人工确认", "相关入口"]:
            with self.subTest(term=term):
                self.assertIn(term, app)

    def test_creator_list_uses_homepage_status(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

        for term in ["主页状态", "主页可打开", "待点开确认主页"]:
            with self.subTest(term=term):
                self.assertIn(term, app)

        for term in ["已抓到粉丝量", "待点开核对粉丝量"]:
            with self.subTest(term=term):
                self.assertNotIn(term, app)

    def test_product_ranking_is_not_visible(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        combined = app + "\n" + html

        for term in ['data-tab="products"', "热销 / 强营销产品", "商品入口"]:
            with self.subTest(term=term):
                self.assertNotIn(term, combined)

        self.assertIn("机会洞察", combined)

    def test_frontend_hides_source_risk_and_uses_market_badges(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        combined = app + "\n" + html + "\n" + css

        hidden_terms = [
            "低风险",
            "中风险",
            "高风险",
            "riskLabel",
            'class="risk',
            ".risk",
        ]
        for term in hidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, combined)

        for term in ["market-badge", "market-us", "market-uk", "market-ca", "market-au", "market-global"]:
            with self.subTest(term=term):
                self.assertIn(term, combined)

    def test_insight_band_uses_verification_language(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        combined = app + "\n" + html

        for term in ["本轮可验证机会", "内容需求", "达人验证", "商品机会", "数字代表出现次数"]:
            with self.subTest(term=term):
                self.assertIn(term, combined)

        self.assertNotIn("机会判断", combined)

    def test_low_value_explainer_copy_is_hidden(self):
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        combined = app + "\n" + html + "\n" + css

        for term in [
            "判断方式",
            "按证据链逐条验证",
            "来自平台公开内容页或结果页",
            "source-destination",
            "source-url-note",
            "sourceUrlNote",
            "只展示平台真实返回",
            "不会用站内搜索页",
            "目标页",
        ]:
            with self.subTest(term=term):
                self.assertNotIn(term, combined)


if __name__ == "__main__":
    unittest.main()
