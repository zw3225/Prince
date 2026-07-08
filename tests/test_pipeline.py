import ssl
import unittest
from unittest.mock import patch

from backend.connectors import (
    ConnectorFetchError,
    _amazon_signals,
    _canonical_direct_url,
    _direct_discovery_query_variants,
    _direct_discovery_searches,
    _fetch_text,
    _search_engine_direct_signals,
    _query_variants,
    _youtube_public_signals,
    _youtube_video_metadata,
    _youtube_subscriber_count,
    collect_signals,
)
from backend.models import RawSignal
from backend.pipeline import dedupe_signals, expand_query, run_trend_search


class PipelineTest(unittest.TestCase):
    def test_query_expansion_contains_core_groups(self):
        expanded = expand_query("portable blender for gym girls")
        self.assertIn("keywords", expanded)
        self.assertIn("hashtags", expanded)
        self.assertIn("audiences", expanded)
        self.assertTrue(any("portable" in keyword for keyword in expanded["keywords"]))
        self.assertIn("gym girls", expanded["audiences"])

    def test_search_outputs_ranked_collections(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            result = run_trend_search("home office wellness", ["US", "UK"], 30)
        self.assertGreater(len(result.content), 0)
        self.assertGreater(len(result.creators), 0)
        self.assertGreater(len(result.products), 0)
        self.assertGreater(len(result.opportunities), 0)
        self.assertGreaterEqual(result.content[0].score, result.content[-1].score)

    def test_summary_narrative_hides_internal_scoring(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            result = run_trend_search("home office wellness", ["US", "UK"], 30)

        narrative = "\n".join(result.summary["narrative"])
        self.assertNotIn("热度分", narrative)
        self.assertNotIn("可信度", narrative)
        self.assertIn("可直达", narrative)

    def test_sample_connector_does_not_fake_external_detail_urls(self):
        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "demo"}, clear=False):
            result = run_trend_search("portable blender for gym girls", ["US"], 30)
        items = result.content + result.creators + result.products + result.opportunities
        self.assertGreater(len(items), 0)
        self.assertTrue(all(item.source_url_type == "sample" for item in items))
        self.assertTrue(all(item.url == "" for item in items))

    def test_real_youtube_connector_uses_returned_detail_urls(self):
        def fake_fetch(url, params, headers=None):
            if url.endswith("/search"):
                return {
                    "items": [
                        {
                            "id": {"videoId": "abc123XYZ"},
                            "snippet": {
                                "channelId": "UCabc123",
                                "channelTitle": "Blend Lab",
                                "title": "Portable blender review",
                                "description": "A real review of a gym blender.",
                                "publishedAt": "2026-06-01T00:00:00Z",
                            },
                        }
                    ]
                }
            if url.endswith("/videos"):
                return {"items": [{"id": "abc123XYZ", "statistics": {"viewCount": "120000", "likeCount": "6400", "commentCount": "380"}}]}
            if url.endswith("/channels"):
                return {
                    "items": [
                        {
                            "id": "UCabc123",
                            "snippet": {"title": "Blend Lab", "description": "Blender tests."},
                            "statistics": {"subscriberCount": "54000", "videoCount": "80", "viewCount": "9000000"},
                        }
                    ]
                }
            if "reddit.com" in url:
                return {"data": {"children": []}}
            return {}

        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "real", "YOUTUBE_API_KEY": "test-key"}, clear=False):
            with patch("backend.connectors._fetch_json", side_effect=fake_fetch):
                with patch("backend.connectors._fetch_text", return_value=""):
                    result = run_trend_search("portable blender", ["US"], 30)

        self.assertEqual(result.content[0].url, "https://www.youtube.com/watch?v=abc123XYZ")
        self.assertEqual(result.content[0].source_url_type, "official_content")
        self.assertEqual(result.creators[0].url, "https://www.youtube.com/channel/UCabc123")
        self.assertEqual(result.creators[0].source_url_type, "official_profile")

    def test_real_mode_without_credentials_does_not_fallback_to_sample(self):
        with patch.dict(
            "os.environ",
            {
                "TREND_RADAR_DATA_MODE": "real",
                "YOUTUBE_API_KEY": "",
                "REDDIT_CLIENT_ID": "",
                "REDDIT_CLIENT_SECRET": "",
                "EBAY_BROWSE_TOKEN": "",
                "ETSY_API_KEY": "",
            },
            clear=False,
        ):
            with patch("backend.connectors._reddit_signals", return_value=[]):
                with patch("backend.connectors._fetch_text", return_value=""):
                    expanded = expand_query("portable blender")
                    signals = collect_signals("portable blender", expanded, ["US"], 30)
        self.assertEqual(signals, [])

    def test_default_mode_does_not_fallback_to_sample(self):
        with patch.dict(
            "os.environ",
            {
                "YOUTUBE_API_KEY": "",
                "REDDIT_CLIENT_ID": "",
                "REDDIT_CLIENT_SECRET": "",
                "EBAY_BROWSE_TOKEN": "",
                "ETSY_API_KEY": "",
            },
            clear=True,
        ):
            with patch("backend.connectors._reddit_signals", return_value=[]):
                with patch("backend.connectors._fetch_text", return_value=""):
                    expanded = expand_query("portable blender")
                    signals = collect_signals("portable blender", expanded, ["US"], 30)
        self.assertEqual(signals, [])

    def test_keyless_public_pages_emit_only_direct_detail_links(self):
        youtube_page = """
        {"videoRenderer":{"videoId":"abc123XYZ09","title":{"runs":[{"text":"Portable blender real review"}]},"ownerText":{"runs":[{"text":"Blend Lab","navigationEndpoint":{"browseEndpoint":{"browseId":"UCblendlab"},"commandMetadata":{"webCommandMetadata":{"url":"/@blendlab"}}}}]},"viewCountText":{"simpleText":"12K views"},"publishedTimeText":{"simpleText":"2 days ago"}}}
        """
        youtube_channel_page = """
        {"subscriberCountText":{"simpleText":"34.5万位订阅者"}}
        """
        ebay_page = """
        <li class="s-item">
          <a class="s-item__link" href="https://www.ebay.com/itm/256123456789?hash=itemabc">item</a>
          <div class="s-item__title"><span>Portable Blender USB Smoothie Maker</span></div>
          <span class="s-item__price">$29.99</span>
        </li>
        """
        etsy_page = """
        <li>
          <a href="https://www.etsy.com/listing/123456789/portable-blender-cup">Portable blender cup</a>
          <p class="wt-text-title-01">$18.50</p>
        </li>
        """
        amazon_page = """
        <div data-component-type="s-search-result" data-asin="B0ABCDEF12">
          <a href="/dp/B0ABCDEF12/ref=sxin">Portable blender</a>
          <span class="a-price"><span class="a-offscreen">$49.99</span></span>
        </div>
        """

        def fake_fetch_text(url, params=None, headers=None):
            if "youtube.com/channel/UCblendlab" in url:
                return youtube_channel_page
            if "youtube.com" in url:
                return youtube_page
            if "ebay" in url:
                return ebay_page
            if "etsy" in url:
                return etsy_page
            if "amazon" in url:
                return amazon_page
            return ""

        with patch.dict(
            "os.environ",
            {
                "TREND_RADAR_DATA_MODE": "real",
                "YOUTUBE_API_KEY": "",
                "REDDIT_CLIENT_ID": "",
                "REDDIT_CLIENT_SECRET": "",
                "EBAY_BROWSE_TOKEN": "",
                "ETSY_API_KEY": "",
            },
            clear=False,
        ):
            with patch("backend.connectors._reddit_signals", return_value=[]):
                with patch("backend.connectors._fetch_text", side_effect=fake_fetch_text):
                    result = run_trend_search("portable blender", ["US"], 30)

        urls = [item.url for item in result.content + result.creators + result.products]
        self.assertIn("https://www.youtube.com/watch?v=abc123XYZ09", urls)
        self.assertIn("https://www.youtube.com/channel/UCblendlab", urls)
        self.assertIn("https://www.amazon.com/dp/B0ABCDEF12", urls)
        self.assertIn("https://www.ebay.com/itm/256123456789", urls)
        self.assertIn("https://www.etsy.com/listing/123456789/portable-blender-cup", urls)
        self.assertFalse(any("/results?search_query=" in url or "/sch/i.html" in url or "/search?q=" in url for url in urls))
        creator = next(item for item in result.creators if item.url == "https://www.youtube.com/channel/UCblendlab")
        self.assertEqual(creator.metrics["followers"], 345000)
        prices = {item.platform: item.price for item in result.products}
        self.assertEqual(prices["Amazon"], 49.99)
        self.assertEqual(prices["eBay"], 29.99)
        self.assertEqual(prices["Etsy"], 18.5)

    def test_youtube_channel_followers_prefer_channel_header(self):
        page = """
        {"subscriberCountText":{"simpleText":"128K subscribers"},"title":{"simpleText":"Related channel"}}
        {"subtitle":{"content":"@MyproteinOfficial • 345K subscribers"}}
        """

        self.assertEqual(_youtube_subscriber_count(page), 345000)

    def test_search_discovery_keeps_only_direct_destination_links(self):
        discovery_page = """
        <a href="/html/?q=portable+blender">platform search</a>
        <a href="/l/?uddg=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc123XYZ09">video</a>
        <a href="/l/?uddg=https%3A%2F%2Fwww.amazon.com%2FPortable-Blender%2Fdp%2FB0ABCDEF12%3Ftag%3Dabc">product</a>
        <a href="https://www.ebay.com/itm/256123456789?hash=itemabc">item</a>
        <a href="https://www.etsy.com/listing/123456789/portable-blender-cup?click_key=x">listing</a>
        <a href="https://www.reddit.com/r/BuyItForLife/comments/abc123/portable_blender_review/">post</a>
        <a href="https://www.tiktok.com/@blendlab/video/7351234567890123456">tiktok video</a>
        <a href="https://www.instagram.com/reel/Cxyz123ABC/">instagram reel</a>
        <a href="https://www.pinterest.com/pin/123456789012345678/">pin</a>
        <a href="https://www.walmart.com/ip/Portable-Blender/123456789">walmart item</a>
        <a href="https://www.target.com/p/portable-blender/-/A-12345678">target item</a>
        <a href="https://www.iherb.com/pr/myprotein-impact-whey/12345">iherb item</a>
        """

        def fake_fetch_text(url, params=None, headers=None):
            if "duckduckgo" in url or "bing" in url:
                return discovery_page
            return ""

        with patch.dict("os.environ", {"TREND_RADAR_DATA_MODE": "real"}, clear=True):
            with patch("backend.connectors._fetch_json", return_value={"data": {"children": []}}):
                with patch("backend.connectors._fetch_text", side_effect=fake_fetch_text):
                    result = run_trend_search("便携榨汁机", ["US"], 30)

        urls = [item.url for item in result.content + result.creators + result.products]
        self.assertIn("https://www.youtube.com/watch?v=abc123XYZ09", urls)
        self.assertIn("https://www.amazon.com/dp/B0ABCDEF12", urls)
        self.assertIn("https://www.ebay.com/itm/256123456789", urls)
        self.assertIn("https://www.etsy.com/listing/123456789/portable-blender-cup", urls)
        self.assertIn("https://www.reddit.com/r/BuyItForLife/comments/abc123/portable_blender_review/", urls)
        self.assertIn("https://www.tiktok.com/@blendlab/video/7351234567890123456", urls)
        self.assertIn("https://www.instagram.com/reel/Cxyz123ABC", urls)
        self.assertIn("https://www.pinterest.com/pin/123456789012345678", urls)
        self.assertIn("https://www.walmart.com/ip/Portable-Blender/123456789", urls)
        self.assertIn("https://www.target.com/p/portable-blender/-/A-12345678", urls)
        self.assertIn("https://www.iherb.com/pr/myprotein-impact-whey/12345", urls)
        self.assertFalse(any("/html/?q=" in url or "/results?search_query=" in url or "/search?q=" in url for url in urls))

    def test_search_discovery_ignores_invalid_url_candidates(self):
        self.assertEqual(_canonical_direct_url("https://www.weiyun.com）：提供更大存储空间"), "")

    def test_public_fetch_ssl_read_error_becomes_source_failure(self):
        with patch("backend.connectors.DIRECT_OPENER.open", side_effect=ssl.SSLWantReadError("try again")):
            with self.assertRaises(ConnectorFetchError):
                _fetch_text("https://www.bing.com/search", {"q": "lysine"})

    def test_search_discovery_accepts_youtube_shorts_as_content_detail(self):
        self.assertEqual(
            _canonical_direct_url("https://www.youtube.com/shorts/abc123XYZ09?feature=share"),
            "https://www.youtube.com/watch?v=abc123XYZ09",
        )

    def test_direct_discovery_prioritizes_social_when_products_already_exist(self):
        products = [
            RawSignal(
                id=f"p-{index}",
                signal_type="product",
                platform="Amazon",
                title=f"Protein powder {index}",
                author="Amazon seller",
                url=f"https://www.amazon.com/dp/B0ABCDEF1{index}",
                market="US",
                source_type="compliance_scrape",
                risk_tier="medium",
                captured_at="2026-07-06T00:00:00+00:00",
                text="Protein powder product",
                tags=["protein powder"],
                metrics={"reviews": 0},
                source_url_type="official_product",
            )
            for index in range(4)
        ]

        searches = _direct_discovery_searches("protein powder", products)
        platforms = [platform for platform, _ in searches]

        self.assertIn("YouTube", platforms)
        self.assertIn("TikTok", platforms)
        self.assertNotIn("Amazon", platforms)
        self.assertTrue(all("site:" in search for _, search in searches))

    def test_direct_discovery_keeps_searching_other_social_platforms(self):
        existing = [
            RawSignal(
                id="yt-content",
                signal_type="content",
                platform="YouTube",
                title="Protein powder review",
                author="YouTube creator",
                url="https://www.youtube.com/watch?v=abc123XYZ09",
                market="GLOBAL",
                source_type="compliance_scrape",
                risk_tier="medium",
                captured_at="2026-07-06T00:00:00+00:00",
                text="Protein powder review",
                tags=["protein powder"],
                metrics={"views": 1000},
                source_url_type="official_content",
            )
        ]

        searches = _direct_discovery_searches("protein powder", existing)
        platforms = [platform for platform, _ in searches]

        self.assertIn("TikTok", platforms)
        self.assertIn("Instagram", platforms)
        self.assertIn("Pinterest", platforms)

    def test_dedupe_keeps_same_title_when_urls_are_different(self):
        first = RawSignal(
            id="tiktok-1",
            signal_type="content",
            platform="TikTok",
            title="protein powder · TikTok 视频",
            author="@creator",
            url="https://www.tiktok.com/@creator/video/7351234567890123456",
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier="high",
            captured_at="2026-07-06T00:00:00+00:00",
            text="TikTok video",
            tags=["protein powder"],
            metrics={"views": 0},
            source_url_type="official_content",
        )
        second = RawSignal(
            id="tiktok-2",
            signal_type="content",
            platform="TikTok",
            title="protein powder · TikTok 视频",
            author="@creator",
            url="https://www.tiktok.com/@creator/video/7351234567890123457",
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier="high",
            captured_at="2026-07-06T00:00:00+00:00",
            text="TikTok video",
            tags=["protein powder"],
            metrics={"views": 0},
            source_url_type="official_content",
        )

        self.assertEqual(len(dedupe_signals([first, second])), 2)

    def test_direct_discovery_query_variants_include_social_intent_terms(self):
        variants = _direct_discovery_query_variants("protein powder", expand_query("protein powder"))

        self.assertIn("protein powder", variants)
        self.assertIn("protein powder review", variants)
        self.assertIn("protein powder viral", variants)

    def test_direct_youtube_content_derives_creator_homepage(self):
        with patch(
            "backend.connectors._direct_discovery_candidates",
            return_value=[(0, "YouTube", "https://www.youtube.com/watch?v=abc123XYZ09")],
        ), patch(
            "backend.connectors._youtube_video_metadata",
            return_value={
                "title": "Lysine supplement review",
                "channel_title": "Supplement Lab",
                "channel_url": "https://www.youtube.com/@SupplementLab",
                "views": 12000,
                "followers": 345000,
            },
        ):
            signals = _search_engine_direct_signals("赖氨酸", expand_query("赖氨酸"), ["US"], 30, [])

        self.assertEqual(signals[0].url, "https://www.youtube.com/watch?v=abc123XYZ09")
        self.assertEqual(signals[0].title, "Lysine supplement review")
        self.assertEqual(signals[1].signal_type, "creator")
        self.assertEqual(signals[1].url, "https://www.youtube.com/@SupplementLab")
        self.assertEqual(signals[1].metrics["followers"], 345000)

    def test_youtube_video_metadata_extracts_channel_entry(self):
        page = """
        <html>
          <head><meta property="og:title" content="Best Lysine Supplement Review"></head>
          <script>
            {"ownerChannelName":"Supplement Lab","ownerProfileUrl":"/@SupplementLab",
             "viewCount":"12000","subtitle":{"content":"@SupplementLab • 345K subscribers"}}
          </script>
        </html>
        """

        with patch("backend.connectors._fetch_text_with_timeout", return_value=page):
            metadata = _youtube_video_metadata("https://www.youtube.com/watch?v=abc123XYZ09")

        self.assertEqual(metadata["title"], "Best Lysine Supplement Review")
        self.assertEqual(metadata["channel_title"], "Supplement Lab")
        self.assertEqual(metadata["channel_url"], "https://www.youtube.com/@SupplementLab")
        self.assertEqual(metadata["views"], 12000)
        self.assertEqual(metadata["followers"], 345000)

    def test_youtube_public_signals_do_not_block_on_channel_followers(self):
        page = """
        {"videoRenderer":{"videoId":"abc123XYZ09",
          "title":{"runs":[{"text":"Lysine supplement review"}]},
          "ownerText":{"runs":[{"text":"Supplement Lab","navigationEndpoint":{
            "commandMetadata":{"webCommandMetadata":{"url":"/@SupplementLab"}}
          }}]},
          "viewCountText":{"simpleText":"12,345 views"}}}
        """

        with patch("backend.connectors._fetch_text_with_timeout", return_value=page), patch(
            "backend.connectors._youtube_channel_followers", side_effect=AssertionError("should not fetch channel page")
        ):
            signals = _youtube_public_signals("赖氨酸", expand_query("赖氨酸"), ["US"], 30)

        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_type, "content")
        self.assertEqual(signals[0].metrics["views"], 12345)
        self.assertEqual(signals[1].signal_type, "creator")
        self.assertEqual(signals[1].url, "https://www.youtube.com/@SupplementLab")
        self.assertEqual(signals[1].metrics["followers"], 0)

    def test_chinese_query_prefers_overseas_search_terms(self):
        expanded = expand_query("便携榨汁机 健身女生")
        variants = _query_variants("便携榨汁机 健身女生", expanded)

        self.assertGreaterEqual(len(variants), 2)
        self.assertIn("portable blender", variants[0])
        self.assertLess(variants.index("便携榨汁机 健身女生"), len(variants))
        self.assertGreater(variants.index("便携榨汁机 健身女生"), 0)

    def test_chinese_supplement_query_uses_overseas_term(self):
        expanded = expand_query("氨基丁酸")
        variants = _query_variants("氨基丁酸", expanded)

        self.assertEqual(variants[0], "GABA supplement")
        self.assertIn("氨基丁酸", variants)

    def test_arbitrary_chinese_query_still_searches_real_sources(self):
        expanded = expand_query("桌面暖风机")
        variants = _query_variants("桌面暖风机", expanded)

        self.assertEqual(variants[0], "桌面暖风机")
        self.assertIn("桌面暖风机 product", variants)
        self.assertIn("桌面暖风机 amazon", variants)
        self.assertIn("桌面暖风机 review", variants)

    def test_amazon_public_connector_ignores_bad_ref_titles(self):
        amazon_page = """
        <div data-component-type="s-search-result" data-asin="B0ABCDEF12">
          <a href="/Portable-Blender-Smoothies/dp/B0ABCDEF12/ref=sr_1_3">Ref=Sr 1 3</a>
        </div>
        """

        with patch("backend.connectors._fetch_text", return_value=amazon_page):
            signals = _amazon_signals("便携榨汁机", expand_query("便携榨汁机"), ["US"], 30)

        self.assertEqual(signals[0].url, "https://www.amazon.com/dp/B0ABCDEF12")
        self.assertEqual(signals[0].title, "Portable Blender Smoothies")
        self.assertNotIn("Ref=Sr", signals[0].title)


if __name__ == "__main__":
    unittest.main()
