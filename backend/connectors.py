from __future__ import annotations

import hashlib
import base64
import html as html_lib
import json
import os
import random
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from .models import RawSignal, RiskTier, SourceType, utc_now_iso


DEFAULT_MARKETS = ["US", "UK", "CA", "AU"]
SOCIAL_PLATFORMS = ["TikTok", "Instagram", "YouTube", "Reddit", "Pinterest"]
COMMERCE_PLATFORMS = ["Amazon", "eBay", "Etsy", "Walmart", "Target", "iHerb", "TikTok Shop"]
HTTP_TIMEOUT_SECONDS = 1.5
PUBLIC_QUERY_VARIANT_LIMIT = 4
PUBLIC_MARKET_LIMIT = 2
REAL_CONNECTOR_SECONDS = 18
DIRECT_DISCOVERY_SECONDS = 24
DIRECT_DISCOVERY_WORKERS = 14
DIRECT_DISCOVERY_OUTPUT_LIMIT = 40
SEARCH_DISCOVERY_FETCH_SECONDS = 4
PROFILE_DISCOVERY_SECONDS = 14
PROFILE_DISCOVERY_WORKERS = 4
YOUTUBE_PUBLIC_CONTENT_LIMIT = 24
YOUTUBE_PUBLIC_CREATOR_LIMIT = 8
YOUTUBE_PUBLIC_RENDERER_LIMIT = 16
YOUTUBE_PUBLIC_FOLLOWER_SECONDS = 1.2
SOCIAL_CONTENT_TARGET = 16
SOCIAL_CREATOR_TARGET = 8
SOCIAL_PLATFORM_TARGET = 3
DIRECT_OPENER = build_opener(ProxyHandler({}))
DATA_MODE_ENV = "TREND_RADAR_DATA_MODE"
INCLUDE_DEMO_ENV = "TREND_RADAR_INCLUDE_DEMO"
LAST_SOURCE_ERRORS: dict[str, str] = {}
QUERY_TRANSLATIONS = {
    "赖氨酸": "lysine supplement",
    "肌酸": "creatine supplement",
    "鱼油": "omega 3 fish oil",
    "益生菌": "probiotic supplement",
    "褪黑素": "melatonin supplement",
    "胶原蛋白": "collagen supplement",
    "叶黄素": "lutein supplement",
    "氨基丁酸": "GABA supplement",
    "γ-氨基丁酸": "GABA supplement",
    "伽马氨基丁酸": "GABA supplement",
    "蛋白粉": "protein powder",
    "乳清蛋白": "whey protein powder",
    "便携榨汁机": "portable blender",
    "筋膜枪": "massage gun",
    "健身女生": "gym girls",
    "宠物": "pet",
    "猫": "cat",
    "狗": "dog",
    "健身": "fitness",
    "户外": "outdoor",
    "露营": "camping",
    "收纳": "organizer",
}


@dataclass(frozen=True)
class ConnectorProfile:
    platform: str
    access_mode: SourceType
    risk_tier: RiskTier
    cadence: str
    coverage_note: str
    categories: tuple[str, ...]


CONNECTOR_PROFILES: list[ConnectorProfile] = [
    ConnectorProfile("TikTok", "third_party", "high", "daily", "公开趋势数据通常需要 Research API 或授权数据商支持。", ("content", "creator")),
    ConnectorProfile("Instagram", "third_party", "high", "daily", "公开内容覆盖依赖 Meta 权限或授权数据集。", ("content", "creator")),
    ConnectorProfile("YouTube", "official_api", "low", "daily", "YouTube Data API 可支持视频与频道信号检索。", ("content", "creator")),
    ConnectorProfile("Reddit", "authorized_api", "medium", "daily", "社区与帖子信号需要按 API 政策采集。", ("content", "creator")),
    ConnectorProfile("Pinterest", "official_api", "medium", "daily", "Pin 与搜索信号可通过已批准应用接入。", ("content", "creator")),
    ConnectorProfile("Amazon", "official_api", "medium", "daily", "PA-API 可支持目录发现，但销售排名深度取决于权限。", ("product",)),
    ConnectorProfile("eBay", "official_api", "low", "daily", "Browse API 可支持商品搜索、价格与卖家信号。", ("product",)),
    ConnectorProfile("Etsy", "official_api", "low", "daily", "Open API 可支持 listing 与店铺发现。", ("product",)),
    ConnectorProfile("Walmart", "compliance_scrape", "low", "daily", "无密钥模式只通过搜索索引发现公开商品详情页，不抓取站内搜索页。", ("product",)),
    ConnectorProfile("Target", "compliance_scrape", "low", "daily", "无密钥模式只通过搜索索引发现公开商品详情页，不抓取站内搜索页。", ("product",)),
    ConnectorProfile("iHerb", "compliance_scrape", "low", "daily", "无密钥模式只通过搜索索引发现公开商品详情页，不抓取站内搜索页。", ("product",)),
    ConnectorProfile("TikTok Shop", "authorized_api", "medium", "daily", "商城数据取决于合作伙伴或开放 API 的区域资格。", ("product",)),
]


def source_health() -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    mode = _data_mode()
    for profile in CONNECTOR_PROFILES:
        status, note = _connector_status(profile, mode)
        source_type = _health_source_type(profile, status)
        if LAST_SOURCE_ERRORS.get(profile.platform):
            status = "fetch_error"
            note = f"{note} 最近一次请求失败：{LAST_SOURCE_ERRORS[profile.platform]}"
        sources.append(
            {
            "platform": profile.platform,
            "source_type": source_type,
            "risk_tier": profile.risk_tier,
            "cadence": profile.cadence,
                "status": status,
                "coverage_note": note,
            }
        )
    return sources


def collect_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    mode = _data_mode()
    LAST_SOURCE_ERRORS.clear()

    if mode == "demo":
        return _sample_signals(query, expanded_terms, markets, window_days)

    signals = _real_signals(query, expanded_terms, markets, window_days)
    if os.getenv(INCLUDE_DEMO_ENV, "").lower() in {"1", "true", "yes"}:
        signals.extend(_sample_signals(query, expanded_terms, markets, window_days))
    return signals


def _real_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    signals = _direct_brand_profile_signals(query, expanded_terms, 1)
    try:
        signals.extend(_youtube_signals(query, expanded_terms, markets, window_days))
    except Exception as error:
        LAST_SOURCE_ERRORS["YouTube"] = str(error)
    jobs = [
        ("Reddit", lambda: _reddit_signals(query, expanded_terms, window_days)),
        ("Amazon", lambda: _amazon_signals(query, expanded_terms, markets, window_days)),
        ("eBay", lambda: _ebay_signals(query, expanded_terms, markets, window_days)),
        ("Etsy", lambda: _etsy_signals(query, expanded_terms, window_days)),
    ]
    signals.extend(_collect_connector_jobs(jobs, REAL_CONNECTOR_SECONDS))
    if _should_run_direct_discovery(signals):
        signals.extend(_search_engine_direct_signals(query, expanded_terms, markets, window_days, signals))
    return signals


def _collect_connector_jobs(jobs: list[tuple[str, Any]], timeout_seconds: float) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    if not jobs:
        return outputs

    deadline = time.monotonic() + timeout_seconds
    executor = ThreadPoolExecutor(max_workers=len(jobs))
    future_map = {executor.submit(job): platform for platform, job in jobs}
    try:
        for future in as_completed(future_map, timeout=max(0.1, deadline - time.monotonic())):
            platform = future_map[future]
            try:
                outputs.extend(future.result())
            except Exception as error:
                LAST_SOURCE_ERRORS[platform] = str(error)
            if time.monotonic() >= deadline:
                break
    except FuturesTimeoutError:
        pass
    finally:
        for future, platform in future_map.items():
            if not future.done():
                LAST_SOURCE_ERRORS[platform] = "本轮公开抓取超时，已先返回其他可打开结果。"
        executor.shutdown(wait=False, cancel_futures=True)
    return outputs


def _sample_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    signals: list[RawSignal] = []
    for profile in CONNECTOR_PROFILES:
        rng = _rng_for(query, profile.platform, ",".join(markets), str(window_days))
        for market in markets:
            if "content" in profile.categories:
                signals.extend(_content_signals(profile, query, expanded_terms, market, window_days, rng))
                signals.extend(_creator_signals(profile, query, expanded_terms, market, window_days, rng))
            if "product" in profile.categories:
                signals.extend(_product_signals(profile, query, expanded_terms, market, window_days, rng))
    return signals


def _youtube_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        return _youtube_public_signals(query, expanded_terms, markets, window_days)

    outputs: list[RawSignal] = []
    profile = _profile("YouTube")
    for market in markets:
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": "8",
            "order": "relevance",
            "regionCode": market,
            "relevanceLanguage": "en",
            "publishedAfter": _published_after(window_days),
            "key": api_key,
        }
        try:
            payload = _fetch_json("https://www.googleapis.com/youtube/v3/search", params)
        except ConnectorFetchError as error:
            LAST_SOURCE_ERRORS["YouTube"] = str(error)
            return outputs

        channel_ids: dict[str, str] = {}
        video_items: list[tuple[str, dict[str, Any]]] = []
        for item in payload.get("items", []):
            item_id = item.get("id") if isinstance(item.get("id"), dict) else {}
            snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
            video_id = _clean_id(item_id.get("videoId"))
            if not video_id:
                continue
            video_items.append((video_id, snippet))

        video_stats = _youtube_video_statistics(api_key, [video_id for video_id, _ in video_items])
        for video_id, snippet in video_items:
            channel_id = _clean_id(snippet.get("channelId"))
            title = _text(snippet.get("title")) or query
            channel_title = _text(snippet.get("channelTitle")) or "YouTube Channel"
            if channel_id:
                channel_ids[channel_id] = channel_title
            published_at = _text(snippet.get("publishedAt")) or utc_now_iso()
            stats = video_stats.get(video_id, {})
            views = _int(stats.get("viewCount"))
            likes = _int(stats.get("likeCount"))
            comments = _int(stats.get("commentCount"))
            outputs.append(
                RawSignal(
                    id=_stable_id("YouTube", market, "content", video_id),
                    signal_type="content",
                    platform="YouTube",
                    title=title,
                    author=channel_title,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    market=market,
                    source_type="official_api",
                    risk_tier=profile.risk_tier,
                    captured_at=_iso_or_now(published_at),
                    text=_text(snippet.get("description")) or title,
                    tags=_pick_tags(expanded_terms, _rng_for(query, "YouTube", video_id)),
                    metrics={
                        "views": views,
                        "engagement_rate": round((likes + comments) / max(views, 1), 4),
                        "comments": comments,
                        "growth_velocity": _freshness_growth(published_at, window_days),
                        "shares": 0,
                        "sentiment": 0.65,
                        "topic_match": _topic_match(query, title),
                        "cross_platform_mentions": 1,
                        "likes": likes,
                    },
                    marketing_signals=_marketing_signals_for(title, _text(snippet.get("description"))),
                    source_url_type="official_content",
                    source_url_note="YouTube 官方 API 返回 videoId，链接直达视频详情页。",
                )
            )

        outputs.extend(_youtube_creator_signals(api_key, query, expanded_terms, market, channel_ids, profile))
    return outputs


def _youtube_public_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    channels: dict[str, tuple[str, str]] = {}
    seen_video_ids: set[str] = set()
    for search_query in _public_query_variants(query, expanded_terms):
        try:
            page = _fetch_text_with_timeout(
                "https://www.youtube.com/results",
                {"search_query": search_query},
                headers=_browser_headers(),
                timeout_seconds=SEARCH_DISCOVERY_FETCH_SECONDS,
            )
        except ConnectorFetchError as error:
            LAST_SOURCE_ERRORS["YouTube"] = str(error)
            continue
        if not page:
            continue

        for renderer in _extract_youtube_renderers(page, "videoRenderer")[:YOUTUBE_PUBLIC_RENDERER_LIMIT]:
            video_id = _clean_youtube_id(renderer.get("videoId"))
            if not video_id or video_id in seen_video_ids:
                continue
            title = _youtube_text(renderer.get("title")) or query
            channel_title, channel_url = _youtube_owner(renderer)
            channel_title = channel_title or "YouTube Channel"
            if channel_url:
                channels[channel_url] = (channel_title, channel_url)
            published_text = _youtube_text(renderer.get("publishedTimeText"))
            views = _view_count(_youtube_text(renderer.get("viewCountText")))
            outputs.append(
                RawSignal(
                    id=_stable_id("YouTube", "public", "content", video_id),
                    signal_type="content",
                    platform="YouTube",
                    title=title,
                    author=channel_title,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    market="GLOBAL",
                    source_type="compliance_scrape",
                    risk_tier="medium",
                    captured_at=utc_now_iso(),
                    text=f"{title} {published_text}".strip(),
                    tags=_pick_tags(expanded_terms, _rng_for(query, "YouTubePublic", video_id)),
                    metrics={
                        "views": views,
                        "engagement_rate": 0,
                        "comments": 0,
                        "growth_velocity": _freshness_growth(utc_now_iso(), window_days),
                        "shares": 0,
                        "sentiment": 0.62,
                        "topic_match": _topic_match(query, title),
                        "cross_platform_mentions": 1,
                    },
                    marketing_signals=_marketing_signals_for(title, published_text),
                    source_url_type="official_content",
                    source_url_note="从 YouTube 公开结果页解析到 videoId，链接直达视频详情页。",
                )
            )
            seen_video_ids.add(video_id)
            if len(seen_video_ids) >= YOUTUBE_PUBLIC_CONTENT_LIMIT:
                break
        if len(seen_video_ids) >= YOUTUBE_PUBLIC_CONTENT_LIMIT and len(channels) >= YOUTUBE_PUBLIC_CREATOR_LIMIT:
            break

    channel_items = list(channels.values())[:YOUTUBE_PUBLIC_CREATOR_LIMIT]
    follower_counts = _youtube_public_channel_followers([channel_url for _, channel_url in channel_items])
    for index, (channel_title, channel_url) in enumerate(channel_items):
        followers = follower_counts.get(channel_url, 0)
        outputs.append(
            RawSignal(
                id=_stable_id("YouTube", "public", "creator", channel_url),
                signal_type="creator",
                platform="YouTube",
                title=f"{channel_title} 频道",
                author=channel_title,
                url=channel_url,
                market="GLOBAL",
                source_type="compliance_scrape",
                risk_tier="medium",
                captured_at=utc_now_iso(),
                text=f"{channel_title} 出现在 {query} 的 YouTube 公开结果中。",
                tags=_pick_tags(expanded_terms, _rng_for(query, "YouTubePublicCreator", str(index))),
                metrics={
                    "followers": followers,
                    "creator_reputation": max(1, len(outputs) - index),
                    "avg_engagement_rate": 0,
                    "recent_hot_posts": 1,
                    "follower_growth": 0,
                    "brand_safety": 0.72,
                    "commercial_density": 0.42,
                    "topic_match": _topic_match(query, channel_title),
                },
                marketing_signals=["频道主页", "公开视频", "可点击达人"],
                source_url_type="official_profile",
                source_url_note="从 YouTube 公开结果页解析到频道入口，链接直达达人主页。",
            )
        )
    return outputs


def _youtube_public_channel_followers(channel_urls: list[str]) -> dict[str, int]:
    channel_urls = [url for url in _unique_strings(channel_urls) if url]
    if not channel_urls:
        return {}

    executor = ThreadPoolExecutor(max_workers=min(len(channel_urls), YOUTUBE_PUBLIC_CREATOR_LIMIT))
    future_map = {executor.submit(_youtube_channel_followers_optional, url): url for url in channel_urls}
    followers: dict[str, int] = {}
    try:
        for future in as_completed(future_map, timeout=YOUTUBE_PUBLIC_FOLLOWER_SECONDS):
            url = future_map[future]
            try:
                count = future.result()
            except Exception:
                count = 0
            if count:
                followers[url] = count
    except FuturesTimeoutError:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return followers


def _youtube_channel_followers_optional(channel_url: str) -> int:
    try:
        page = _fetch_text_with_timeout(
            channel_url,
            {},
            headers=_browser_headers(),
            timeout_seconds=YOUTUBE_PUBLIC_FOLLOWER_SECONDS,
        )
    except ConnectorFetchError:
        return 0
    return _youtube_subscriber_count(page)


def _youtube_creator_signals(
    api_key: str,
    query: str,
    expanded_terms: dict[str, list[str]],
    market: str,
    channel_ids: dict[str, str],
    profile: ConnectorProfile,
) -> list[RawSignal]:
    if not channel_ids:
        return []
    outputs: list[RawSignal] = []
    ids = list(channel_ids.keys())[:10]
    params = {
        "part": "snippet,statistics",
        "id": ",".join(ids),
        "maxResults": "10",
        "key": api_key,
    }
    try:
        payload = _fetch_json("https://www.googleapis.com/youtube/v3/channels", params)
    except ConnectorFetchError as error:
        LAST_SOURCE_ERRORS["YouTube"] = str(error)
        return outputs

    for item in payload.get("items", []):
        channel_id = _clean_id(item.get("id"))
        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        if not channel_id:
            continue
        title = _text(snippet.get("title")) or channel_ids.get(channel_id, "YouTube Channel")
        subscribers = _int(statistics.get("subscriberCount"))
        video_count = _int(statistics.get("videoCount"))
        view_count = _int(statistics.get("viewCount"))
        outputs.append(
            RawSignal(
                id=_stable_id("YouTube", market, "creator", channel_id),
                signal_type="creator",
                platform="YouTube",
                title=f"{title} 频道",
                author=title,
                url=f"https://www.youtube.com/channel/{channel_id}",
                market=market,
                source_type="official_api",
                risk_tier=profile.risk_tier,
                captured_at=utc_now_iso(),
                text=_text(snippet.get("description")) or f"{title} 与 {query} 相关。",
                tags=_pick_tags(expanded_terms, _rng_for(query, "YouTube", channel_id)),
                metrics={
                    "followers": subscribers,
                    "avg_engagement_rate": 0,
                    "recent_hot_posts": min(video_count, 10),
                    "follower_growth": 0,
                    "brand_safety": 0.82,
                    "commercial_density": 0.4,
                    "topic_match": _topic_match(query, title),
                    "total_views": view_count,
                },
                marketing_signals=["视频内容", "频道主页", "可追踪达人"],
                source_url_type="official_profile",
                source_url_note="YouTube 官方 API 返回 channelId，链接直达频道主页。",
            )
        )
    return outputs


def _youtube_video_statistics(api_key: str, video_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not video_ids:
        return {}
    params = {
        "part": "statistics",
        "id": ",".join(video_ids[:50]),
        "maxResults": "50",
        "key": api_key,
    }
    try:
        payload = _fetch_json("https://www.googleapis.com/youtube/v3/videos", params)
    except ConnectorFetchError as error:
        LAST_SOURCE_ERRORS["YouTube"] = str(error)
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        video_id = _clean_id(item.get("id"))
        statistics = item.get("statistics") if isinstance(item.get("statistics"), dict) else {}
        if video_id:
            stats[video_id] = statistics
    return stats


def _reddit_signals(query: str, expanded_terms: dict[str, list[str]], window_days: int) -> list[RawSignal]:
    profile = _profile("Reddit")
    endpoint, headers = _reddit_endpoint_and_headers()
    payload: dict[str, Any] = {}
    for search_query in _public_query_variants(query, expanded_terms):
        params = {
            "q": search_query,
            "sort": "top",
            "t": _reddit_window(window_days),
            "limit": "12",
            "raw_json": "1",
        }
        try:
            payload = _fetch_json(endpoint, params, headers=headers)
        except ConnectorFetchError as error:
            LAST_SOURCE_ERRORS["Reddit"] = str(error)
            return []
        if (((payload.get("data") or {}).get("children")) if isinstance(payload.get("data"), dict) else []):
            break
    if not payload:
        return []

    outputs: list[RawSignal] = []
    author_posts: dict[str, list[dict[str, Any]]] = {}
    children = (((payload.get("data") or {}).get("children")) if isinstance(payload.get("data"), dict) else []) or []
    for child in children:
        data = child.get("data") if isinstance(child, dict) else {}
        if not isinstance(data, dict) or data.get("over_18"):
            continue
        post_id = _clean_id(data.get("id"))
        permalink = _text(data.get("permalink"))
        if not post_id or not permalink:
            continue
        author = _text(data.get("author")) or "Reddit user"
        title = _text(data.get("title")) or query
        created = datetime.fromtimestamp(float(data.get("created_utc") or 0), timezone.utc) if data.get("created_utc") else datetime.now(timezone.utc)
        score = _int(data.get("score"))
        comments = _int(data.get("num_comments"))
        author_posts.setdefault(author, []).append(data)
        outputs.append(
            RawSignal(
                id=_stable_id("Reddit", "content", post_id),
                signal_type="content",
                platform="Reddit",
                title=title,
                author=f"u/{author}",
                url=f"https://www.reddit.com{permalink}",
                market="GLOBAL",
                source_type="authorized_api",
                risk_tier=profile.risk_tier,
                captured_at=created.replace(microsecond=0).isoformat(),
                text=_text(data.get("selftext")) or title,
                tags=_pick_tags(expanded_terms, _rng_for(query, "Reddit", post_id)),
                metrics={
                    "views": max(score * 120, comments * 60, 1),
                    "engagement_rate": min(0.35, (comments + score) / max(score * 120, 1)),
                    "comments": comments,
                    "growth_velocity": _freshness_growth(created.isoformat(), window_days),
                    "shares": 0,
                    "sentiment": 0.62,
                    "topic_match": _topic_match(query, title),
                    "cross_platform_mentions": 1,
                    "upvotes": score,
                },
                marketing_signals=_marketing_signals_for(title, _text(data.get("selftext"))),
                source_url_type="official_content",
                source_url_note="Reddit 返回 permalink，链接直达原帖。",
            )
        )

    outputs.extend(_reddit_creator_signals(query, expanded_terms, author_posts, profile))
    return outputs


def _reddit_creator_signals(
    query: str,
    expanded_terms: dict[str, list[str]],
    author_posts: dict[str, list[dict[str, Any]]],
    profile: ConnectorProfile,
) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    for author, posts in list(author_posts.items())[:8]:
        if author in {"[deleted]", "AutoModerator"}:
            continue
        about = _reddit_user_about(author)
        karma = _int(about.get("link_karma")) + _int(about.get("comment_karma")) if about else sum(_int(post.get("score")) for post in posts)
        hot_posts = len(posts)
        title = f"u/{author}"
        outputs.append(
            RawSignal(
                id=_stable_id("Reddit", "creator", author),
                signal_type="creator",
                platform="Reddit",
                title=f"{title} 用户主页",
                author=title,
                url=f"https://www.reddit.com/user/{author}/",
                market="GLOBAL",
                source_type="authorized_api",
                risk_tier=profile.risk_tier,
                captured_at=utc_now_iso(),
                text=f"{title} 在搜索结果中出现 {hot_posts} 条相关内容。",
                tags=_pick_tags(expanded_terms, _rng_for(query, "Reddit", author)),
                metrics={
                    "creator_reputation": karma,
                    "avg_engagement_rate": 0,
                    "recent_hot_posts": hot_posts,
                    "follower_growth": 0,
                    "brand_safety": 0.72,
                    "commercial_density": 0.3,
                    "topic_match": max(_topic_match(query, _text(post.get("title"))) for post in posts),
                },
                marketing_signals=["社区讨论", "用户主页", "可追踪作者"],
                source_url_type="official_profile",
                source_url_note="Reddit 返回 author，链接直达用户主页；声量指标来自公开 karma 或帖子表现。",
            )
        )
    return outputs


def _reddit_user_about(author: str) -> dict[str, Any]:
    endpoint, headers = _reddit_endpoint_and_headers(f"/user/{author}/about")
    try:
        payload = _fetch_json(endpoint, {"raw_json": "1"}, headers=headers)
    except ConnectorFetchError:
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def _amazon_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    profile = _profile("Amazon")
    domains = {"US": "www.amazon.com", "UK": "www.amazon.co.uk", "GB": "www.amazon.co.uk", "CA": "www.amazon.ca", "AU": "www.amazon.com.au"}
    for market in markets:
        domain = domains.get(market, "www.amazon.com")
        seen: set[str] = set()
        for search_query in _public_query_variants(query, expanded_terms):
            try:
                page = _fetch_text(f"https://{domain}/s", {"k": search_query}, headers=_browser_headers())
            except ConnectorFetchError as error:
                LAST_SOURCE_ERRORS["Amazon"] = str(error)
                break
            for asin, href, parsed_title, parsed_price in _amazon_product_links(page, domain):
                if asin in seen:
                    continue
                seen.add(asin)
                url = f"https://{domain}/dp/{asin}"
                title = _product_title(parsed_title, href, search_query)
                outputs.append(
                    RawSignal(
                        id=_stable_id("Amazon", market, "public", asin),
                        signal_type="product",
                        platform="Amazon",
                        title=title,
                        author="Amazon seller",
                        url=url,
                        market=market,
                        source_type="compliance_scrape",
                        risk_tier=profile.risk_tier,
                        captured_at=utc_now_iso(),
                        text=f"{title} 出现在 Amazon 公开搜索结果中。",
                        tags=_pick_tags(expanded_terms, _rng_for(query, "AmazonPublic", asin)),
                        price=parsed_price,
                        discount=None,
                        metrics=_public_product_metrics(query, title, window_days, len(seen)),
                        marketing_signals=["真实商详", "公开页面", "可点击商品"],
                        source_url_type="official_product",
                        source_url_note="从 Amazon 公开结果页解析到 ASIN，链接直达商品详情页；未解析到时不会展示链接。",
                    )
                )
                if len(seen) >= 12:
                    break
            if len(seen) >= 12:
                break
    return outputs


def _ebay_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    token = os.getenv("EBAY_BROWSE_TOKEN", "").strip()
    if not token:
        return _ebay_public_signals(query, expanded_terms, markets, window_days)
    outputs: list[RawSignal] = []
    profile = _profile("eBay")
    marketplace_ids = {"US": "EBAY_US", "UK": "EBAY_GB", "GB": "EBAY_GB", "CA": "EBAY_CA", "AU": "EBAY_AU"}
    for market in _public_markets(markets):
        marketplace_id = marketplace_ids.get(market, "EBAY_US")
        try:
            payload = _fetch_json(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                {"q": query, "limit": "12"},
                headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": marketplace_id},
            )
        except ConnectorFetchError as error:
            LAST_SOURCE_ERRORS["eBay"] = str(error)
            return outputs
        for item in payload.get("itemSummaries", []):
            if not isinstance(item, dict):
                continue
            item_id = _clean_id(item.get("itemId"))
            url = _text(item.get("itemWebUrl"))
            if not item_id or not url:
                continue
            price = _money_value(item.get("price"))
            seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
            outputs.append(
                RawSignal(
                    id=_stable_id("eBay", market, "product", item_id),
                    signal_type="product",
                    platform="eBay",
                    title=_text(item.get("title")) or query,
                    author=_text(seller.get("username")) or "eBay seller",
                    url=url,
                    market=market,
                    source_type="official_api",
                    risk_tier=profile.risk_tier,
                    captured_at=utc_now_iso(),
                    text=_text(item.get("shortDescription")) or _text(item.get("title")) or query,
                    tags=_pick_tags(expanded_terms, _rng_for(query, "eBay", item_id)),
                    price=price,
                    discount=None,
                    metrics={
                        "sales_rank_proxy": 500,
                        "reviews": _int(seller.get("feedbackScore")),
                        "review_growth": _freshness_growth(utc_now_iso(), window_days),
                        "rating": min(5.0, _float(seller.get("feedbackPercentage")) / 20) if seller.get("feedbackPercentage") else 4.2,
                        "social_mentions": 0,
                        "cross_platform_mentions": 1,
                        "listing_stability": 0.78,
                        "topic_match": _topic_match(query, _text(item.get("title"))),
                    },
                    marketing_signals=["真实商详", "卖家反馈", "价格可见"],
                    source_url_type="official_product",
                    source_url_note="eBay Browse API 返回 itemWebUrl，链接直达商品详情页。",
                )
            )
    return outputs


def _ebay_public_signals(query: str, expanded_terms: dict[str, list[str]], markets: list[str], window_days: int) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    profile = _profile("eBay")
    domains = {"US": "www.ebay.com", "UK": "www.ebay.co.uk", "GB": "www.ebay.co.uk", "CA": "www.ebay.ca", "AU": "www.ebay.com.au"}
    for market in _public_markets(markets):
        domain = domains.get(market, "www.ebay.com")
        seen: set[str] = set()
        for search_query in _public_query_variants(query, expanded_terms):
            try:
                page = _fetch_text(f"https://{domain}/sch/i.html", {"_nkw": search_query, "_ipg": "24"}, headers=_browser_headers())
            except ConnectorFetchError as error:
                LAST_SOURCE_ERRORS["eBay"] = str(error)
                break
            for segment in re.split(r"<li[^>]+class=\"[^\"]*s-item", page):
                href = _first_match(r'href="([^"]*?/itm/[^"]+)"', segment)
                url = _clean_product_url(href, domain, "/itm/")
                if not url or url in seen:
                    continue
                seen.add(url)
                title = _product_title(_clean_html(_first_match(r's-item__title[^>]*>(.*?)</div>', segment)), url, search_query)
                if _bad_product_title(title):
                    continue
                outputs.append(
                    RawSignal(
                        id=_stable_id("eBay", market, "public", url),
                        signal_type="product",
                        platform="eBay",
                        title=title,
                        author="eBay seller",
                        url=url,
                        market=market,
                        source_type="compliance_scrape",
                        risk_tier=profile.risk_tier,
                        captured_at=utc_now_iso(),
                        text=f"{title} 出现在 eBay 公开搜索结果中。",
                        tags=_pick_tags(expanded_terms, _rng_for(query, "eBayPublic", url)),
                        price=_parse_price(_clean_html(_first_match(r's-item__price[^>]*>(.*?)</span>', segment))),
                        discount=None,
                        metrics=_public_product_metrics(query, title, window_days, len(seen)),
                        marketing_signals=["真实商详", "公开页面", "价格可见"],
                        source_url_type="official_product",
                        source_url_note="从 eBay 公开结果页解析到 /itm/ 商品链接，点击直达商详页。",
                    )
                )
                if len(seen) >= 12:
                    break
            if len(seen) >= 12:
                break
    return outputs


def _etsy_signals(query: str, expanded_terms: dict[str, list[str]], window_days: int) -> list[RawSignal]:
    api_key = os.getenv("ETSY_API_KEY", "").strip()
    if not api_key:
        return _etsy_public_signals(query, expanded_terms, window_days)
    profile = _profile("Etsy")
    try:
        payload = _fetch_json(
            "https://openapi.etsy.com/v3/application/listings/active",
            {"keywords": query, "limit": "12"},
            headers={"x-api-key": api_key},
        )
    except ConnectorFetchError as error:
        LAST_SOURCE_ERRORS["Etsy"] = str(error)
        return []
    outputs: list[RawSignal] = []
    results = payload.get("results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        listing_id = _clean_id(item.get("listing_id"))
        url = _text(item.get("url"))
        if not listing_id or not url:
            continue
        outputs.append(
            RawSignal(
                id=_stable_id("Etsy", "product", listing_id),
                signal_type="product",
                platform="Etsy",
                title=_text(item.get("title")) or query,
                author=f"Shop {item.get('shop_id')}" if item.get("shop_id") else "Etsy shop",
                url=url,
                market="GLOBAL",
                source_type="official_api",
                risk_tier=profile.risk_tier,
                captured_at=utc_now_iso(),
                text=_text(item.get("description")) or _text(item.get("title")) or query,
                tags=_pick_tags(expanded_terms, _rng_for(query, "Etsy", listing_id)),
                price=_etsy_price(item.get("price")),
                discount=None,
                metrics={
                    "sales_rank_proxy": 650,
                    "reviews": _int(item.get("num_favorers")),
                    "review_growth": _freshness_growth(utc_now_iso(), window_days),
                    "rating": 4.3,
                    "social_mentions": _int(item.get("num_favorers")),
                    "cross_platform_mentions": 1,
                    "listing_stability": 0.76,
                    "topic_match": _topic_match(query, _text(item.get("title"))),
                },
                marketing_signals=["真实商详", "收藏信号", "手作市场"],
                source_url_type="official_product",
                source_url_note="Etsy Open API 返回 listing url，链接直达商品详情页。",
            )
        )
    return outputs


def _etsy_public_signals(query: str, expanded_terms: dict[str, list[str]], window_days: int) -> list[RawSignal]:
    profile = _profile("Etsy")
    outputs: list[RawSignal] = []
    seen: set[str] = set()
    for search_query in _public_query_variants(query, expanded_terms):
        try:
            page = _fetch_text("https://www.etsy.com/search", {"q": search_query}, headers=_browser_headers())
        except ConnectorFetchError as error:
            LAST_SOURCE_ERRORS["Etsy"] = str(error)
            break
        for match in re.finditer(r'href="(https://www\.etsy\.com/listing/\d+/[^"#?]+[^"]*)"', page):
            href = match.group(1)
            url = _clean_product_url(href, "www.etsy.com", "/listing/")
            if not url or url in seen:
                continue
            seen.add(url)
            title = _product_title("", url, search_query)
            segment_start = max(page.rfind("<li", 0, match.start()), page.rfind("<div", 0, match.start()))
            segment_end = page.find("</li>", match.end())
            if segment_end == -1:
                segment_end = page.find("</div>", match.end())
            segment = page[max(0, segment_start) : segment_end if segment_end != -1 else match.end() + 1600]
            outputs.append(
                RawSignal(
                    id=_stable_id("Etsy", "public", url),
                    signal_type="product",
                    platform="Etsy",
                    title=title,
                    author="Etsy shop",
                    url=url,
                    market="GLOBAL",
                    source_type="compliance_scrape",
                    risk_tier=profile.risk_tier,
                    captured_at=utc_now_iso(),
                    text=f"{title} 出现在 Etsy 公开搜索结果中。",
                    tags=_pick_tags(expanded_terms, _rng_for(query, "EtsyPublic", url)),
                    price=_etsy_public_price(segment),
                    discount=None,
                    metrics=_public_product_metrics(query, title, window_days, len(seen)),
                    marketing_signals=["真实商详", "公开页面", "可点击商品"],
                    source_url_type="official_product",
                    source_url_note="从 Etsy 公开结果页解析到 listing 链接，点击直达商品详情页。",
                )
            )
            if len(seen) >= 12:
                break
        if len(seen) >= 12:
            break
    return outputs


def _content_signals(
    profile: ConnectorProfile,
    query: str,
    expanded_terms: dict[str, list[str]],
    market: str,
    window_days: int,
    rng: random.Random,
) -> list[RawSignal]:
    angles = ["前后对比测试", "达人日常种草", "真实测评", "爆款对比", "礼品清单", "痛点解决短视频"]
    outputs: list[RawSignal] = []
    for index in range(3):
        angle = rng.choice(angles)
        keyword = rng.choice(expanded_terms["keywords"])
        handle = f"{_slug(keyword)}_{_slug(profile.platform)}{index + 1}"
        views = rng.randint(38_000, 2_800_000)
        engagement_rate = round(rng.uniform(0.035, 0.18), 3)
        growth = round(rng.uniform(0.12, 2.6), 2)
        comments = int(views * rng.uniform(0.002, 0.022))
        captured = datetime.now(timezone.utc) - timedelta(hours=rng.randint(2, max(4, window_days * 24)))
        signal_id = _stable_id(profile.platform, market, query, "content", str(index), angle)
        outputs.append(
            RawSignal(
                id=signal_id,
                signal_type="content",
                platform=profile.platform,
                title=f"{keyword}｜{angle}",
                author=f"@{handle}",
                url="",
                market=market,
                source_type=profile.access_mode,
                risk_tier=profile.risk_tier,
                captured_at=captured.replace(microsecond=0).isoformat(),
                text=f"{query} 在 {market} 市场通过“{angle}”内容获得更高讨论度。",
                tags=_pick_tags(expanded_terms, rng),
                metrics={
                    "views": views,
                    "engagement_rate": engagement_rate,
                    "comments": comments,
                    "growth_velocity": growth,
                    "shares": int(views * rng.uniform(0.004, 0.07)),
                    "sentiment": round(rng.uniform(0.48, 0.92), 2),
                    "topic_match": round(rng.uniform(0.62, 0.98), 2),
                    "cross_platform_mentions": rng.randint(1, 6),
                },
                marketing_signals=rng.sample(["UGC 钩子", "对比卖点", "痛点表达", "季节场景", "达人优惠码", "套装提及"], 2),
                source_url_type="sample",
                source_url_note=f"样例数据不伪造 {profile.platform} 帖子链接；接入真实数据后这里会打开平台返回的原始内容详情页。",
            )
        )
    return outputs


def _creator_signals(
    profile: ConnectorProfile,
    query: str,
    expanded_terms: dict[str, list[str]],
    market: str,
    window_days: int,
    rng: random.Random,
) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    niches = expanded_terms["audiences"] + expanded_terms["keywords"]
    for index in range(2):
        niche = rng.choice(niches)
        handle = f"{_slug(niche)}lab{index + 1}"
        followers = rng.randint(18_000, 2_400_000)
        recent_hits = rng.randint(1, 8)
        avg_engagement = round(rng.uniform(0.028, 0.145), 3)
        signal_id = _stable_id(profile.platform, market, query, "creator", str(index), niche)
        outputs.append(
            RawSignal(
                id=signal_id,
                signal_type="creator",
                platform=profile.platform,
                title=f"{niche} 垂类达人",
                author=f"@{handle}",
                url="",
                market=market,
                source_type=profile.access_mode,
                risk_tier=profile.risk_tier,
                captured_at=(datetime.now(timezone.utc) - timedelta(hours=rng.randint(1, window_days * 24))).replace(microsecond=0).isoformat(),
                text=f"该达人持续发布 {query} 及相邻购买场景内容。",
                tags=_pick_tags(expanded_terms, rng),
                metrics={
                    "followers": followers,
                    "avg_engagement_rate": avg_engagement,
                    "recent_hot_posts": recent_hits,
                    "follower_growth": round(rng.uniform(0.04, 0.72), 2),
                    "brand_safety": round(rng.uniform(0.68, 0.98), 2),
                    "commercial_density": round(rng.uniform(0.18, 0.78), 2),
                    "topic_match": round(rng.uniform(0.58, 0.98), 2),
                },
                marketing_signals=rng.sample(["适合联盟分销", "UGC 友好", "演示驱动", "测评格式", "受众信任"], 2),
                source_url_type="sample",
                source_url_note=f"样例数据不伪造 {profile.platform} 达人主页；接入真实数据后这里会打开平台返回的原始达人主页。",
            )
        )
    return outputs


def _product_signals(
    profile: ConnectorProfile,
    query: str,
    expanded_terms: dict[str, list[str]],
    market: str,
    window_days: int,
    rng: random.Random,
) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    product_forms = ["入门套装", "便携款", "高配组合", "旅行装", "订阅补充装", "限量款"]
    for index in range(4):
        keyword = rng.choice(expanded_terms["keywords"])
        form = rng.choice(product_forms)
        price = round(rng.uniform(12, 129), 2)
        discount = round(rng.choice([0, rng.uniform(0.08, 0.42)]), 2)
        reviews = rng.randint(80, 18_000)
        signal_id = _stable_id(profile.platform, market, query, "product", str(index), form)
        outputs.append(
            RawSignal(
                id=signal_id,
                signal_type="product",
                platform=profile.platform,
                title=f"{keyword} {form}",
                author=f"{keyword} Co.",
                url="",
                market=market,
                source_type=profile.access_mode,
                risk_tier=profile.risk_tier,
                captured_at=(datetime.now(timezone.utc) - timedelta(hours=rng.randint(1, window_days * 24))).replace(microsecond=0).isoformat(),
                text=f"{form} 面向 {query} 场景，并带有活跃的电商陈列信号。",
                tags=_pick_tags(expanded_terms, rng),
                price=price,
                discount=discount,
                metrics={
                    "sales_rank_proxy": rng.randint(1, 5000),
                    "reviews": reviews,
                    "review_growth": round(rng.uniform(0.03, 0.82), 2),
                    "rating": round(rng.uniform(3.8, 4.9), 1),
                    "social_mentions": rng.randint(12, 3400),
                    "cross_platform_mentions": rng.randint(1, 7),
                    "listing_stability": round(rng.uniform(0.58, 0.99), 2),
                    "topic_match": round(rng.uniform(0.6, 0.98), 2),
                },
                marketing_signals=rng.sample(["优惠券", "套装", "限量库存", "达人背书", "视频化 listing", "订阅省"], 3),
                source_url_type="sample",
                source_url_note=f"样例数据不伪造 {profile.platform} 商品详情页；接入真实数据后这里会打开平台返回的原始商详页。",
            )
        )
    return outputs


def _rng_for(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:12], 16))


class ConnectorFetchError(RuntimeError):
    pass


def _data_mode() -> str:
    mode = os.getenv(DATA_MODE_ENV, "real").strip().lower()
    if mode == "demo":
        return "demo"
    return "real"


def _connector_status(profile: ConnectorProfile, mode: str) -> tuple[str, str]:
    if mode == "demo":
        return "demo_only", f"当前为样例模式。{profile.coverage_note}"
    if profile.platform == "YouTube":
        if os.getenv("YOUTUBE_API_KEY", "").strip():
            return "live_ready", "已配置 YOUTUBE_API_KEY，会使用 YouTube 官方 API 返回的视频和频道链接。"
        return "public_page_ready", "无需密钥，会解析 YouTube 公开结果页里真实出现的 videoId 和频道入口。"
    if profile.platform == "Reddit":
        if _reddit_credentials():
            return "live_ready", "已配置 Reddit OAuth，会使用授权接口返回原帖和用户主页链接。"
        return "public_json_ready", "无需密钥，会尝试 Reddit 公开 JSON；若平台拒绝会显示失败，不会生成假链接。"
    if profile.platform == "Amazon":
        return "public_page_ready", "无需密钥，会解析 Amazon 公开结果页里真实出现的 ASIN 商品链接。"
    if profile.platform == "eBay":
        if os.getenv("EBAY_BROWSE_TOKEN", "").strip():
            return "live_ready", "已配置 EBAY_BROWSE_TOKEN，会使用 Browse API 返回的商品详情页。"
        return "public_page_ready", "无需密钥，会解析 eBay 公开结果页里真实出现的 /itm/ 商品链接。"
    if profile.platform == "Etsy":
        if os.getenv("ETSY_API_KEY", "").strip():
            return "live_ready", "已配置 ETSY_API_KEY，会使用 Open API 返回的 listing 链接。"
        return "public_page_ready", "无需密钥，会解析 Etsy 公开结果页里真实出现的 listing 商品链接。"
    if profile.platform in {"TikTok", "Instagram", "Pinterest"}:
        return "search_discovery_ready", f"无需密钥；后台只从公开搜索索引发现真实帖子或主页链接，不使用站内搜索页。{profile.coverage_note}"
    if profile.platform in {"Walmart", "Target", "iHerb"}:
        return "search_discovery_ready", "无需密钥；后台只从公开搜索索引发现真实商品详情页，不使用站内搜索页。"
    return "not_connected", f"暂未接入真实接口。{profile.coverage_note}"


def _health_source_type(profile: ConnectorProfile, status: str) -> SourceType:
    if status == "public_page_ready":
        return "compliance_scrape"
    if status == "public_json_ready":
        return "authorized_api"
    if status == "search_discovery_ready":
        return "compliance_scrape"
    return profile.access_mode


def _profile(platform: str) -> ConnectorProfile:
    for profile in CONNECTOR_PROFILES:
        if profile.platform == platform:
            return profile
    raise KeyError(platform)


def _fetch_json(url: str, params: dict[str, str], headers: dict[str, str] | None = None, timeout_seconds: float | None = None) -> dict[str, Any]:
    request_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(request_url, headers=headers or {})
    return _open_json_request(request, timeout_seconds=timeout_seconds)


def _fetch_text(url: str, params: dict[str, str], headers: dict[str, str] | None = None, timeout_seconds: float | None = None) -> str:
    request_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(request_url, headers=headers or {})
    try:
        with DIRECT_OPENER.open(request, timeout=timeout_seconds or HTTP_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="ignore")
    except HTTPError as error:
        message = error.reason or error.read().decode("utf-8", errors="ignore")[:120]
        raise ConnectorFetchError(f"HTTP {error.code} {message}") from error
    except URLError as error:
        raise ConnectorFetchError(str(error.reason)) from error
    except (TimeoutError, socket.timeout) as error:
        raise ConnectorFetchError("请求超时") from error
    except ssl.SSLError as error:
        raise ConnectorFetchError(f"SSL 读取失败：{error}") from error
    except OSError as error:
        raise ConnectorFetchError(f"网络读取失败：{error}") from error


def _fetch_json_post_form(url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = urlencode(form).encode("utf-8")
    merged_headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    request = Request(url, data=body, headers=merged_headers, method="POST")
    return _open_json_request(request)


def _open_json_request(request: Request, timeout_seconds: float | None = None) -> dict[str, Any]:
    try:
        with DIRECT_OPENER.open(request, timeout=timeout_seconds or HTTP_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        message = error.reason or error.read().decode("utf-8", errors="ignore")[:120]
        raise ConnectorFetchError(f"HTTP {error.code} {message}") from error
    except URLError as error:
        raise ConnectorFetchError(str(error.reason)) from error
    except (TimeoutError, socket.timeout) as error:
        raise ConnectorFetchError("请求超时") from error
    except ssl.SSLError as error:
        raise ConnectorFetchError(f"SSL 读取失败：{error}") from error
    except OSError as error:
        raise ConnectorFetchError(f"网络读取失败：{error}") from error
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise ConnectorFetchError("返回内容不是 JSON") from error
    return payload if isinstance(payload, dict) else {}


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _published_after(window_days: int) -> str:
    captured = datetime.now(timezone.utc) - timedelta(days=max(1, window_days))
    return captured.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _reddit_window(window_days: int) -> str:
    if window_days <= 1:
        return "day"
    if window_days <= 7:
        return "week"
    if window_days <= 30:
        return "month"
    if window_days <= 365:
        return "year"
    return "all"


def _reddit_credentials() -> tuple[str, str] | None:
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


def _reddit_endpoint_and_headers(path: str = "/search") -> tuple[str, dict[str, str]]:
    user_agent = os.getenv("REDDIT_USER_AGENT", "TrendRadar/1.0 by local-dashboard")
    credentials = _reddit_credentials()
    if not credentials:
        if path == "/search":
            return "https://www.reddit.com/search.json", {"User-Agent": user_agent}
        return f"https://www.reddit.com{path}.json", {"User-Agent": user_agent}
    token = _reddit_access_token(credentials[0], credentials[1], user_agent)
    return f"https://oauth.reddit.com{path}", {"Authorization": f"Bearer {token}", "User-Agent": user_agent}


def _reddit_access_token(client_id: str, client_secret: str, user_agent: str) -> str:
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    payload = _fetch_json_post_form(
        "https://www.reddit.com/api/v1/access_token",
        {"grant_type": "client_credentials"},
        {"Authorization": f"Basic {auth}", "User-Agent": user_agent},
    )
    token = _text(payload.get("access_token"))
    if not token:
        raise ConnectorFetchError("Reddit OAuth 未返回 access_token")
    return token


def _search_engine_direct_signals(
    query: str,
    expanded_terms: dict[str, list[str]],
    markets: list[str],
    window_days: int,
    existing_signals: list[RawSignal],
) -> list[RawSignal]:
    existing_urls = {signal.url for signal in existing_signals if signal.url}
    outputs: list[RawSignal] = []
    seen = set(existing_urls)
    rank = 1
    deadline = time.monotonic() + DIRECT_DISCOVERY_SECONDS
    searches: list[tuple[int, str, str]] = []
    seen_searches: set[tuple[str, str]] = set()
    for search_query in _direct_discovery_query_variants(query, expanded_terms):
        for target_platform, search in _direct_discovery_searches(search_query, existing_signals):
            key = (target_platform, search)
            if key in seen_searches:
                continue
            searches.append((len(searches), target_platform, search))
            seen_searches.add(key)

    candidates = _direct_discovery_candidates(searches, deadline)
    for _, target_platform, direct in candidates:
        if not direct or direct in seen:
            continue
        signal = _direct_signal_from_url(direct, query, expanded_terms, window_days, markets, rank)
        if not signal or signal.platform != target_platform:
            continue
        outputs.append(signal)
        seen.add(direct)
        rank += 1
        creator = _creator_signal_from_content(signal, query, expanded_terms, window_days, rank)
        if creator and creator.url not in seen:
            outputs.append(creator)
            seen.add(creator.url)
            rank += 1
        if len(outputs) >= DIRECT_DISCOVERY_OUTPUT_LIMIT or time.monotonic() >= deadline:
            return outputs
    return outputs


def _direct_brand_profile_signals(query: str, expanded_terms: dict[str, list[str]], start_rank: int) -> list[RawSignal]:
    outputs: list[RawSignal] = []
    rank = start_rank
    tasks = [(platform, handle) for handle in _brand_handle_candidates(query) for platform in ("TikTok", "Pinterest")]
    for platform, profile in _validated_brand_profiles(tasks):
        outputs.append(
            _brand_profile_signal(
                platform=platform,
                title=profile["title"],
                author=profile["author"],
                url=profile["url"],
                followers=profile["followers"],
                note=profile["note"],
                query=query,
                expanded_terms=expanded_terms,
                rank=rank,
            )
        )
        rank += 1
    return outputs


def _validated_brand_profiles(tasks: list[tuple[str, str]]) -> list[tuple[str, dict[str, Any]]]:
    if not tasks:
        return []

    executor = ThreadPoolExecutor(max_workers=min(PROFILE_DISCOVERY_WORKERS, len(tasks)))
    future_map = {executor.submit(_validated_brand_profile, platform, handle): (index, platform) for index, (platform, handle) in enumerate(tasks)}
    profiles: list[tuple[int, str, dict[str, Any]]] = []
    try:
        for future in as_completed(future_map, timeout=PROFILE_DISCOVERY_SECONDS):
            index, platform = future_map[future]
            try:
                profile = future.result()
            except Exception:
                continue
            if profile:
                profiles.append((index, platform, profile))
    except FuturesTimeoutError:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return [(platform, profile) for _, platform, profile in sorted(profiles, key=lambda item: item[0])]


def _brand_handle_candidates(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", query.lower())
    if not tokens:
        return []
    compact = "".join(tokens)
    if len(compact) < 3 or len(compact) > 30:
        return []
    if len(tokens) > 3:
        return []
    return _unique_strings([compact])


def _validated_brand_profile(platform: str, handle: str) -> dict[str, Any] | None:
    if platform == "TikTok":
        url = f"https://www.tiktok.com/@{handle}"
        payload: dict[str, Any] = {}
        for attempt in range(3):
            try:
                payload = _fetch_json_with_timeout("https://www.tiktok.com/oembed", {"url": url}, headers=_browser_headers(), timeout_seconds=4)
                break
            except ConnectorFetchError:
                if attempt == 2:
                    return None
                time.sleep(0.2)
        author = _text(payload.get("author_name"))
        author_url = _text(payload.get("author_url"))
        if author.lower() != handle or f"/@{handle}" not in author_url.lower():
            return None
        return {
            "url": f"https://www.tiktok.com/@{author}",
            "title": f"@{author} · TikTok 主页",
            "author": f"@{author}",
            "followers": 0,
            "note": "TikTok oEmbed 已验证该创作者主页存在，入口直达达人主页；粉丝量需点开确认。",
        }

    if platform == "Pinterest":
        url = f"https://www.pinterest.com/{handle}/"
        try:
            page = _fetch_text_with_timeout(url, {}, headers=_browser_headers(), timeout_seconds=4)
        except ConnectorFetchError:
            return None
        lower = page.lower()
        if "user not found" in lower or '"httpstatus":404' in lower or "httpstatus\\\":404" in lower:
            return None
        username = _first_match(r'"username"\s*:\s*"([^"]+)"', page)
        if username.lower() != handle:
            return None
        full_name = _first_match(r'"full_name"\s*:\s*"([^"]+)"', page) or handle
        followers = max([_int(value) for value in re.findall(r'"follower_count"\s*:\s*(\d+)', page)] or [0])
        title = _clean_html(_first_match(r"<title[^>]*>(.*?)</title>", page)) or f"{full_name} · Pinterest 主页"
        return {
            "url": f"https://www.pinterest.com/{username}/",
            "title": title,
            "author": full_name,
            "followers": followers,
            "note": "Pinterest 公开页面已验证该主页存在，入口直达达人主页。",
        }
    return None


def _fetch_json_with_timeout(url: str, params: dict[str, str], headers: dict[str, str] | None, timeout_seconds: float) -> dict[str, Any]:
    try:
        return _fetch_json(url, params, headers=headers, timeout_seconds=timeout_seconds)
    except TypeError:
        return _fetch_json(url, params, headers=headers)


def _fetch_text_with_timeout(url: str, params: dict[str, str], headers: dict[str, str] | None, timeout_seconds: float) -> str:
    try:
        return _fetch_text(url, params, headers=headers, timeout_seconds=timeout_seconds)
    except TypeError:
        return _fetch_text(url, params, headers=headers)


def _brand_profile_signal(
    platform: str,
    title: str,
    author: str,
    url: str,
    followers: int,
    note: str,
    query: str,
    expanded_terms: dict[str, list[str]],
    rank: int,
) -> RawSignal:
    profile = _profile(platform)
    metrics = _direct_creator_metrics(query, title, rank)
    metrics["followers"] = followers
    metrics["recent_hot_posts"] = 0
    return RawSignal(
        id=_stable_id("BrandProfile", platform, url),
        signal_type="creator",
        platform=platform,
        title=title,
        author=author,
        url=url,
        market="GLOBAL",
        source_type="compliance_scrape",
        risk_tier=profile.risk_tier,
        captured_at=utc_now_iso(),
        text=f"{title} 是后台验证过的 {platform} 达人主页入口。",
        tags=_pick_tags(expanded_terms, _rng_for(query, "BrandProfile", platform, url)),
        metrics=metrics,
        marketing_signals=[f"可核对{platform}主页", "已验证主页入口", "需点开确认内容数据"],
        source_url_type="official_profile",
        source_url_note=note,
    )


def _creator_signal_from_content(
    signal: RawSignal,
    query: str,
    expanded_terms: dict[str, list[str]],
    window_days: int,
    rank: int,
) -> RawSignal | None:
    captured = utc_now_iso()
    if signal.platform == "YouTube":
        metadata = _youtube_video_metadata(signal.url)
        channel_url = metadata.get("channel_url", "")
        if not channel_url:
            return None
        author = metadata.get("channel_title") or signal.author or "YouTube creator"
        followers = _int(metadata.get("followers"))
        metrics = _direct_creator_metrics(query, author, rank)
        metrics["followers"] = followers
        metrics["recent_hot_posts"] = 1
        return RawSignal(
            id=_stable_id("DirectSearch", "YouTubeCreatorFromVideo", channel_url),
            signal_type="creator",
            platform="YouTube",
            title=f"{author} 频道",
            author=author,
            url=channel_url,
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier="medium",
            captured_at=captured,
            text=f"{author} 是从已抓到的 YouTube 视频详情页解析出的频道主页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectYouTubeCreatorFromVideo", channel_url)),
            metrics=metrics,
            marketing_signals=["可核对频道主页", "来自真实视频详情", "需点开确认更多数据"],
            source_url_type="official_profile",
            source_url_note="后台从已抓到的视频详情页解析频道入口，展示链接直达 YouTube 达人主页。",
        )

    if signal.platform == "TikTok":
        parsed = urlparse(signal.url)
        match = re.match(r"^/(@[A-Za-z0-9._-]+)/video/\d+$", parsed.path.rstrip("/"))
        if not match:
            return None
        handle = match.group(1)
        profile_url = f"https://www.tiktok.com/{handle}"
        metrics = _direct_creator_metrics(query, handle, rank)
        return RawSignal(
            id=_stable_id("DirectSearch", "TikTokCreatorFromVideo", profile_url),
            signal_type="creator",
            platform="TikTok",
            title=f"{handle} · TikTok 主页",
            author=handle,
            url=profile_url,
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier=_profile("TikTok").risk_tier,
            captured_at=captured,
            text=f"{handle} 是从已抓到的 TikTok 视频链接解析出的达人主页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectTikTokCreatorFromVideo", profile_url)),
            metrics=metrics,
            marketing_signals=["可核对达人主页", "来自真实视频链接", "需点开确认粉丝量"],
            source_url_type="official_profile",
            source_url_note="后台从已抓到的视频链接解析达人 handle，展示链接直达 TikTok 主页。",
        )

    return None


def _direct_discovery_candidates(searches: list[tuple[int, str, str]], deadline: float) -> list[tuple[int, str, str]]:
    if not searches:
        return []

    executor = ThreadPoolExecutor(max_workers=min(DIRECT_DISCOVERY_WORKERS, len(searches)))
    future_map = {
        executor.submit(_direct_urls_for_search, target_platform, search, deadline): (order, target_platform)
        for order, target_platform, search in searches
    }
    candidates: list[tuple[int, str, str]] = []
    try:
        for future in as_completed(future_map, timeout=max(0.1, deadline - time.monotonic())):
            order, target_platform = future_map[future]
            try:
                urls = future.result()
            except ConnectorFetchError:
                continue
            except Exception as error:
                LAST_SOURCE_ERRORS[target_platform] = f"公开搜索失败：{error}"
                continue
            for index, direct in enumerate(urls):
                candidates.append((order * 100 + index, target_platform, direct))
            if time.monotonic() >= deadline:
                break
    except FuturesTimeoutError:
        pass
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return sorted(candidates, key=lambda item: item[0])


def _direct_urls_for_search(target_platform: str, search: str, deadline: float) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for page in _search_result_pages(search, deadline):
        for raw_url in _candidate_urls_from_page(page):
            if time.monotonic() >= deadline:
                return urls
            direct = _canonical_direct_url(raw_url)
            if not direct or direct in seen:
                continue
            if _platform_from_direct_url(direct) != target_platform:
                continue
            urls.append(direct)
            seen.add(direct)
            if len(urls) >= 6:
                return urls
    return urls


def _direct_discovery_query_variants(query: str, expanded_terms: dict[str, list[str]]) -> list[str]:
    variants = _query_variants(query, expanded_terms)
    focused: list[str] = []
    for value in variants[:5]:
        focused.append(value)
        focused.append(f"{value} review")
        focused.append(f"{value} viral")
        focused.append(f"{value} creator")
        focused.append(f"{value} shorts")
    return _unique_strings(focused)[:8]


def _direct_discovery_searches(search_query: str, existing_signals: list[RawSignal]) -> list[tuple[str, str]]:
    platforms = {signal.platform for signal in existing_signals}
    social_platforms = {signal.platform for signal in existing_signals if signal.signal_type in {"content", "creator"}}
    product_platforms = {signal.platform for signal in existing_signals if signal.signal_type == "product"}
    content_count = sum(1 for signal in existing_signals if signal.signal_type == "content")
    creator_count = sum(1 for signal in existing_signals if signal.signal_type == "creator")
    product_count = sum(1 for signal in existing_signals if signal.signal_type == "product")
    social_platform_count = len(social_platforms)
    needs_content = content_count < SOCIAL_CONTENT_TARGET
    needs_creator = creator_count < SOCIAL_CREATOR_TARGET
    needs_product = product_count < 4
    needs_social_diversity = social_platform_count < SOCIAL_PLATFORM_TARGET
    needs_more_by_type = needs_content or needs_creator or needs_product or needs_social_diversity

    content_searches: list[tuple[str, str]] = [
        ("YouTube", f"site:youtube.com/watch {search_query}"),
        ("YouTube", f"site:youtube.com/shorts {search_query}"),
        ("YouTube", f"site:youtu.be {search_query}"),
        ("TikTok", f"site:tiktok.com/@ {search_query} video"),
        ("TikTok", f"site:tiktok.com/@ {search_query}"),
        ("Instagram", f"site:instagram.com/reel {search_query}"),
        ("Instagram", f"site:instagram.com/reels {search_query}"),
        ("Instagram", f"site:instagram.com/p {search_query}"),
        ("Pinterest", f"site:pinterest.com/pin {search_query}"),
        ("Reddit", f"site:reddit.com/r/ comments {search_query}"),
        ("Reddit", f"site:reddit.com {search_query} discussion"),
    ]
    creator_searches: list[tuple[str, str]] = [
        ("YouTube", f"site:youtube.com/@ {search_query}"),
        ("YouTube", f"site:youtube.com/channel {search_query}"),
        ("TikTok", f"site:tiktok.com/@ {search_query}"),
        ("Instagram", f"site:instagram.com {search_query}"),
        ("Pinterest", f"site:pinterest.com {search_query}"),
        ("Reddit", f"site:reddit.com/user {search_query}"),
    ]
    product_searches: list[tuple[str, str]] = [
        ("Amazon", f"site:amazon.com/dp {search_query}"),
        ("eBay", f"site:ebay.com/itm {search_query}"),
        ("Etsy", f"site:etsy.com/listing {search_query}"),
        ("Walmart", f"site:walmart.com/ip {search_query}"),
        ("Target", f"site:target.com/p {search_query}"),
        ("iHerb", f"site:iherb.com/pr {search_query}"),
    ]
    searches: list[tuple[str, str]] = []
    if needs_content or needs_social_diversity:
        searches.extend(content_searches)
    if needs_creator or needs_social_diversity:
        searches.extend(creator_searches)
    if needs_product:
        searches.extend(product_searches)
    if not searches:
        searches = content_searches + creator_searches + product_searches
    if needs_social_diversity:
        searches = sorted(searches, key=lambda item: (item[0] in social_platforms, item[0] in COMMERCE_PLATFORMS))

    selected: list[tuple[str, str]] = []
    seen_queries: set[str] = set()
    for platform, search in searches:
        if platform in SOCIAL_PLATFORMS and platform in social_platforms and not needs_more_by_type:
            continue
        if platform in COMMERCE_PLATFORMS and platform in product_platforms and not needs_more_by_type:
            continue
        if platform in platforms and not needs_more_by_type:
            continue
        if search not in seen_queries:
            selected.append((platform, search))
            seen_queries.add(search)
    return selected[:32]


def _search_result_pages(search: str, deadline: float | None = None) -> list[str]:
    endpoints = [
        ("https://html.duckduckgo.com/html/", {"q": search}),
        ("https://www.bing.com/search", {"format": "rss", "q": search, "count": "20"}),
        ("https://www.bing.com/search", {"q": search, "count": "20"}),
    ]
    pages: list[str] = []
    for url, params in endpoints:
        if deadline is not None and time.monotonic() >= deadline:
            return pages
        try:
            page = _fetch_text_with_timeout(url, params, headers=_browser_headers(), timeout_seconds=SEARCH_DISCOVERY_FETCH_SECONDS)
        except ConnectorFetchError:
            continue
        if page:
            pages.append(page)
    return pages


def _needs_direct_discovery(signals: list[RawSignal]) -> bool:
    content_count = sum(1 for signal in signals if signal.signal_type == "content")
    creator_count = sum(1 for signal in signals if signal.signal_type == "creator")
    product_count = sum(1 for signal in signals if signal.signal_type == "product")
    social_platform_count = len({signal.platform for signal in signals if signal.signal_type in {"content", "creator"}})
    return (
        content_count < SOCIAL_CONTENT_TARGET
        or creator_count < SOCIAL_CREATOR_TARGET
        or social_platform_count < SOCIAL_PLATFORM_TARGET
        or product_count < 4
    )


def _should_run_direct_discovery(signals: list[RawSignal]) -> bool:
    if _needs_direct_discovery(signals):
        return True
    platforms = {signal.platform for signal in signals}
    social_platforms = {signal.platform for signal in signals if signal.signal_type in {"content", "creator"}}
    product_platforms = {signal.platform for signal in signals if signal.signal_type == "product"}
    has_social_backfill = bool({"TikTok", "Instagram", "Pinterest", "Reddit"} & social_platforms)
    has_product_backfill = bool({"eBay", "Etsy", "Walmart", "Target", "iHerb"} & product_platforms)
    return len(platforms) < 4 or not has_social_backfill or not has_product_backfill


def _candidate_urls_from_page(page: str) -> list[str]:
    candidates: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', page or "", flags=re.IGNORECASE):
        candidates.append(html_lib.unescape(href))
    for link in re.findall(r"<link>(https?://[^<]+)</link>", page or "", flags=re.IGNORECASE):
        candidates.append(html_lib.unescape(link))
    for url in re.findall(r"https?://[^\s\"'<>]+", page or "", flags=re.IGNORECASE):
        candidates.append(html_lib.unescape(url))
    return candidates


def _canonical_direct_url(value: str) -> str:
    url = _unwrap_search_redirect(value)
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    if not host:
        return ""

    if host.endswith("youtu.be"):
        video_id = _clean_youtube_id(path.strip("/").split("/")[0])
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    if "youtube.com" in host:
        if path == "/watch":
            video_id = _clean_youtube_id((parse_qs(parsed.query).get("v") or [""])[0])
            return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        if path.startswith("/shorts/"):
            video_id = _clean_youtube_id(path.split("/")[2] if len(path.split("/")) > 2 else "")
            return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
        if re.match(r"^/(?:@[^/]+|channel/UC[A-Za-z0-9_-]+|c/[^/]+|user/[^/]+)$", path):
            return f"https://www.youtube.com{path}"
        return ""

    if "amazon." in host:
        match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", path, flags=re.IGNORECASE)
        if match:
            return f"https://{host}/dp/{match.group(1).upper()}"
        return ""

    if "ebay." in host:
        return _clean_product_url(url, host, "/itm/")

    if host == "www.etsy.com" or host.endswith(".etsy.com"):
        return _clean_product_url(url, "www.etsy.com", "/listing/")

    if "tiktok.com" in host:
        if re.match(r"^/@[A-Za-z0-9._-]+/video/\d+$", path):
            return f"https://www.tiktok.com{path}"
        if re.match(r"^/@[A-Za-z0-9._-]+$", path):
            return f"https://www.tiktok.com{path}"
        return ""

    if "instagram.com" in host:
        if re.match(r"^/(?:p|reel|tv)/[A-Za-z0-9_-]+$", path):
            return f"https://www.instagram.com{path}"
        if _is_instagram_profile_path(path):
            return f"https://www.instagram.com{path}"
        return ""

    if host.endswith("pinterest.com"):
        if re.match(r"^/pin/\d+$", path):
            return f"https://www.pinterest.com{path}"
        if _is_pinterest_profile_path(path):
            return f"https://www.pinterest.com{path}"
        return ""

    if "walmart." in host:
        return _clean_retail_product_url(url, "www.walmart.com", r"^/ip/(?:[^/]+/)?\d+")

    if "target." in host:
        return _clean_retail_product_url(url, "www.target.com", r"^/p/[^?#]+/-/A-\d+")

    if "iherb." in host:
        return _clean_retail_product_url(url, "www.iherb.com", r"^/pr/[^?#]+/\d+")

    if host.endswith("reddit.com"):
        if re.search(r"/r/[^/]+/comments/[A-Za-z0-9]+", path):
            return f"https://www.reddit.com{path}/"
        if re.match(r"^/user/[A-Za-z0-9_-]+$", path):
            return f"https://www.reddit.com{path}/"
    return ""


def _platform_from_direct_url(value: str) -> str:
    try:
        host = urlparse(value).netloc.lower()
    except ValueError:
        return ""
    if "youtube.com" in host or host.endswith("youtu.be"):
        return "YouTube"
    if "amazon." in host:
        return "Amazon"
    if "ebay." in host:
        return "eBay"
    if host == "www.etsy.com" or host.endswith(".etsy.com"):
        return "Etsy"
    if "tiktok.com" in host:
        return "TikTok"
    if "instagram.com" in host:
        return "Instagram"
    if host.endswith("pinterest.com"):
        return "Pinterest"
    if "walmart." in host:
        return "Walmart"
    if "target." in host:
        return "Target"
    if "iherb." in host:
        return "iHerb"
    if host.endswith("reddit.com"):
        return "Reddit"
    return ""


def _is_instagram_profile_path(path: str) -> bool:
    reserved = {
        "about",
        "accounts",
        "developer",
        "direct",
        "explore",
        "legal",
        "p",
        "reel",
        "reels",
        "stories",
        "tv",
    }
    match = re.match(r"^/([A-Za-z0-9._]{2,30})$", path)
    return bool(match and match.group(1).lower() not in reserved)


def _is_pinterest_profile_path(path: str) -> bool:
    reserved = {"about", "business", "ideas", "login", "pin", "search", "settings", "today"}
    match = re.match(r"^/([A-Za-z0-9_-]{2,40})$", path)
    return bool(match and match.group(1).lower() not in reserved)


def _clean_retail_product_url(value: str, default_domain: str, path_pattern: str) -> str:
    if not value:
        return ""
    url = html_lib.unescape(unquote(value))
    parsed = urlparse(url)
    if not parsed.netloc:
        url = urljoin(f"https://{default_domain}", url)
        parsed = urlparse(url)
    clean_path = parsed.path.rstrip("/")
    match = re.match(path_pattern, clean_path, flags=re.IGNORECASE)
    if not match:
        return ""
    return f"{parsed.scheme or 'https'}://{default_domain}{match.group(0)}"


def _unwrap_search_redirect(value: str) -> str:
    url = html_lib.unescape(unquote(value or ""))
    for _ in range(3):
        try:
            parsed = urlparse(url)
        except ValueError:
            return ""
        params = parse_qs(parsed.query)
        target = ""
        for key in ("uddg", "url"):
            if params.get(key):
                target = params[key][0]
                break
        if not target:
            match = re.search(r"https?%3A%2F%2F[^&]+", url, flags=re.IGNORECASE)
            target = unquote(match.group(0)) if match else ""
        if not target:
            break
        url = html_lib.unescape(unquote(target))
    return url


def _direct_content_metrics(query: str, title: str, window_days: int, captured: str) -> dict[str, float | int]:
    return {
        "views": 0,
        "engagement_rate": 0,
        "comments": 0,
        "likes": 0,
        "growth_velocity": _freshness_growth(captured, window_days),
        "shares": 0,
        "sentiment": 0.62,
        "topic_match": _topic_match(query, title),
        "cross_platform_mentions": 1,
    }


def _direct_creator_metrics(query: str, title: str, rank: int) -> dict[str, float | int]:
    return {
        "followers": 0,
        "creator_reputation": max(1, 40 - rank),
        "avg_engagement_rate": 0,
        "recent_hot_posts": 0,
        "follower_growth": 0,
        "brand_safety": 0.72,
        "commercial_density": 0.42,
        "topic_match": _topic_match(query, title),
    }


def _direct_social_signal(
    platform: str,
    signal_type: str,
    title: str,
    author: str,
    url: str,
    query: str,
    expanded_terms: dict[str, list[str]],
    window_days: int,
    rank: int,
    captured: str,
) -> RawSignal:
    profile = _profile(platform)
    is_creator = signal_type == "creator"
    return RawSignal(
        id=_stable_id("DirectSearch", platform, signal_type, url),
        signal_type="creator" if is_creator else "content",
        platform=platform,
        title=title,
        author=author,
        url=url,
        market="GLOBAL",
        source_type="compliance_scrape",
        risk_tier=profile.risk_tier,
        captured_at=captured,
        text=f"{title} 是后台从公开搜索索引发现的 {platform} 直达入口。",
        tags=_pick_tags(expanded_terms, _rng_for(query, "DirectSocial", platform, url)),
        metrics=_direct_creator_metrics(query, title, rank) if is_creator else _direct_content_metrics(query, title, window_days, captured),
        marketing_signals=[f"{platform} 直达入口", "公开搜索发现", "需点开确认数据"],
        source_url_type="official_profile" if is_creator else "official_content",
        source_url_note=f"后台只使用公开搜索结果发现链接，展示入口直达 {platform} {'主页' if is_creator else '内容详情'}。",
    )


def _direct_product_signal(
    platform: str,
    title: str,
    url: str,
    query: str,
    expanded_terms: dict[str, list[str]],
    window_days: int,
    markets: list[str],
    rank: int,
    captured: str,
) -> RawSignal:
    profile = _profile(platform)
    return RawSignal(
        id=_stable_id("DirectSearch", platform, url),
        signal_type="product",
        platform=platform,
        title=title,
        author=f"{platform} seller",
        url=url,
        market=_market_from_url(url, markets),
        source_type="compliance_scrape",
        risk_tier=profile.risk_tier,
        captured_at=captured,
        text=f"{title} 是后台从公开搜索索引发现的 {platform} 商品详情直达页。",
        tags=_pick_tags(expanded_terms, _rng_for(query, "DirectProduct", url)),
        price=None,
        discount=None,
        metrics=_public_product_metrics(query, title, window_days, rank),
        marketing_signals=[f"{platform} 商品详情", "公开搜索发现", "价格需点开确认"],
        source_url_type="official_product",
        source_url_note=f"后台只使用公开搜索结果发现商品详情页，展示链接直达 {platform} 商详。",
    )


def _social_author_from_path(path: str, fallback: str) -> str:
    parts = [part for part in path.strip("/").split("/") if part]
    if parts and parts[0].startswith("@"):
        return parts[0]
    if parts and parts[0] not in {"p", "reel", "tv", "pin"}:
        return parts[0]
    return fallback


def _direct_signal_from_url(
    url: str,
    query: str,
    expanded_terms: dict[str, list[str]],
    window_days: int,
    markets: list[str],
    rank: int,
) -> RawSignal | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    title = _product_title("", url, query)
    captured = utc_now_iso()

    if "youtube.com" in host and path == "/watch":
        video_id = _clean_youtube_id((parse_qs(parsed.query).get("v") or [""])[0])
        if not video_id:
            return None
        metadata = _youtube_video_metadata(url)
        title = metadata.get("title") or title
        author = metadata.get("channel_title") or "YouTube creator"
        views = _int(metadata.get("views"))
        return RawSignal(
            id=_stable_id("DirectSearch", "YouTube", video_id),
            signal_type="content",
            platform="YouTube",
            title=title,
            author=author,
            url=url,
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier="medium",
            captured_at=captured,
            text=f"{title} 是后台发现到的 YouTube 视频直达页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectYouTube", video_id)),
            metrics={
                "views": views,
                "engagement_rate": 0,
                "comments": 0,
                "growth_velocity": _freshness_growth(captured, window_days),
                "shares": 0,
                "sentiment": 0.62,
                "topic_match": _topic_match(query, title),
                "cross_platform_mentions": 1,
            },
            marketing_signals=["真实视频", "直达内容", "后台发现"],
            source_url_type="official_content",
            source_url_note="后台只使用搜索结果发现 videoId，展示链接直达 YouTube 视频详情页。",
        )

    if "youtube.com" in host:
        handle = path.strip("/") or "channel"
        followers = _youtube_channel_followers(url)
        return RawSignal(
            id=_stable_id("DirectSearch", "YouTubeCreator", path),
            signal_type="creator",
            platform="YouTube",
            title=f"{handle} 频道",
            author=handle,
            url=url,
            market="GLOBAL",
            source_type="compliance_scrape",
            risk_tier="medium",
            captured_at=captured,
            text=f"{handle} 是后台发现到的 YouTube 频道直达页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectYouTubeCreator", path)),
            metrics={
                "followers": followers,
                "creator_reputation": max(1, 40 - rank),
                "avg_engagement_rate": 0,
                "recent_hot_posts": 1,
                "follower_growth": 0,
                "brand_safety": 0.72,
                "commercial_density": 0.42,
                "topic_match": _topic_match(query, title),
            },
            marketing_signals=["真实频道", "达人主页", "后台发现"],
            source_url_type="official_profile",
            source_url_note="后台只使用搜索结果发现频道入口，展示链接直达 YouTube 达人主页。",
        )

    if "tiktok.com" in host:
        author = _social_author_from_path(path, "TikTok creator")
        if re.match(r"^/@[A-Za-z0-9._-]+/video/\d+$", path):
            return _direct_social_signal("TikTok", "content", f"{query} · TikTok 视频", author, url, query, expanded_terms, window_days, rank, captured)
        return _direct_social_signal("TikTok", "creator", f"{author} · TikTok 主页", author, url, query, expanded_terms, window_days, rank, captured)

    if "instagram.com" in host:
        if re.match(r"^/(?:p|reel|tv)/[A-Za-z0-9_-]+$", path):
            return _direct_social_signal("Instagram", "content", f"{query} · Instagram 内容", "Instagram creator", url, query, expanded_terms, window_days, rank, captured)
        author = _social_author_from_path(path, "Instagram creator")
        return _direct_social_signal("Instagram", "creator", f"{author} · Instagram 主页", author, url, query, expanded_terms, window_days, rank, captured)

    if host.endswith("pinterest.com"):
        if re.match(r"^/pin/\d+$", path):
            return _direct_social_signal("Pinterest", "content", f"{query} · Pinterest Pin", "Pinterest creator", url, query, expanded_terms, window_days, rank, captured)
        author = _social_author_from_path(path, "Pinterest creator")
        return _direct_social_signal("Pinterest", "creator", f"{author} · Pinterest 主页", author, url, query, expanded_terms, window_days, rank, captured)

    if any(marker in host for marker in ("walmart.", "target.", "iherb.")):
        platform = "Walmart" if "walmart." in host else "Target" if "target." in host else "iHerb"
        return _direct_product_signal(platform, title, url, query, expanded_terms, window_days, markets, rank, captured)

    if "amazon." in host or "ebay." in host or "etsy.com" in host:
        platform = "Amazon" if "amazon." in host else "eBay" if "ebay." in host else "Etsy"
        profile = _profile(platform)
        return RawSignal(
            id=_stable_id("DirectSearch", platform, url),
            signal_type="product",
            platform=platform,
            title=title,
            author=f"{platform} seller",
            url=url,
            market=_market_from_url(url, markets),
            source_type="compliance_scrape",
            risk_tier=profile.risk_tier,
            captured_at=captured,
            text=f"{title} 是后台发现到的 {platform} 商品详情直达页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectProduct", url)),
            price=None,
            discount=None,
            metrics=_public_product_metrics(query, title, window_days, rank),
            marketing_signals=["真实商详", "直达商品", "后台发现"],
            source_url_type="official_product",
            source_url_note=f"后台只使用搜索结果发现商品详情页，展示链接直达 {platform} 商详。",
        )

    if host.endswith("reddit.com"):
        is_user = path.startswith("/user/")
        author = path.split("/")[2] if is_user and len(path.split("/")) > 2 else "Reddit user"
        return RawSignal(
            id=_stable_id("DirectSearch", "Reddit", url),
            signal_type="creator" if is_user else "content",
            platform="Reddit",
            title=f"u/{author}" if is_user else title,
            author=f"u/{author}" if is_user else "Reddit user",
            url=url,
            market="GLOBAL",
            source_type="authorized_api",
            risk_tier=_profile("Reddit").risk_tier,
            captured_at=captured,
            text=f"{title} 是后台发现到的 Reddit 直达页。",
            tags=_pick_tags(expanded_terms, _rng_for(query, "DirectReddit", url)),
            metrics={
                "views": 0,
                "engagement_rate": 0,
                "comments": 0,
                "likes": 0,
                "growth_velocity": _freshness_growth(captured, window_days),
                "shares": 0,
                "sentiment": 0.62,
                "topic_match": _topic_match(query, title),
                "cross_platform_mentions": 1,
            },
            marketing_signals=["真实原帖" if not is_user else "真实用户", "直达链接", "后台发现"],
            source_url_type="official_profile" if is_user else "official_content",
            source_url_note="后台只使用搜索结果发现 Reddit 原帖或用户页，展示链接直达目标页。",
        )
    return None


def _query_variants(query: str, expanded_terms: dict[str, list[str]]) -> list[str]:
    variants: list[str] = []
    matched = [translation for source, translation in QUERY_TRANSLATIONS.items() if source in query]
    has_english_query = bool(re.search(r"[A-Za-z]", query))
    if has_english_query:
        variants.append(query)
    if matched:
        variants.append(" ".join(_unique_strings(matched)))
        variants.extend(matched)
    if not has_english_query:
        variants.append(query)
    for keyword in expanded_terms.get("keywords", []):
        variants.append(keyword)
    for audience in expanded_terms.get("audiences", []):
        if re.search(r"[A-Za-z]", audience) and not re.search(r"[\u4e00-\u9fff]", audience):
            variants.append(audience)
    return _unique_strings([item for item in variants if item.strip()])[:10]


def _public_query_variants(query: str, expanded_terms: dict[str, list[str]]) -> list[str]:
    return _query_variants(query, expanded_terms)[:PUBLIC_QUERY_VARIANT_LIMIT]


def _public_markets(markets: list[str]) -> list[str]:
    selected = markets or DEFAULT_MARKETS
    return selected[:PUBLIC_MARKET_LIMIT]


def _unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _text(value)
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            output.append(normalized)
    return output


def _market_from_url(url: str, markets: list[str]) -> str:
    host = urlparse(url).netloc.lower()
    if host.endswith(".co.uk"):
        return "UK"
    if host.endswith(".ca"):
        return "CA"
    if host.endswith(".com.au"):
        return "AU"
    return markets[0] if markets else "GLOBAL"


def _iso_or_now(value: str) -> str:
    if not value:
        return utc_now_iso()
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(microsecond=0).isoformat()
    except ValueError:
        return utc_now_iso()


def _freshness_growth(value: str, window_days: int) -> float:
    try:
        captured = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        captured = datetime.now(timezone.utc)
    age_hours = max(1.0, (datetime.now(timezone.utc) - captured).total_seconds() / 3600)
    window_hours = max(24, window_days * 24)
    return round(max(0.05, min(3.0, window_hours / age_hours / 12)), 2)


def _topic_match(query: str, text: str) -> float:
    query_tokens = {token for token in re.split(r"[^a-zA-Z0-9]+", query.lower()) if len(token) > 1}
    text_tokens = {token for token in re.split(r"[^a-zA-Z0-9]+", text.lower()) if len(token) > 1}
    if not query_tokens:
        return 0.6
    overlap = len(query_tokens.intersection(text_tokens)) / len(query_tokens)
    return round(max(0.42, min(0.98, 0.45 + overlap * 0.5)), 2)


def _marketing_signals_for(title: str, body: str) -> list[str]:
    text = f"{title} {body}".lower()
    signals = []
    keyword_map = [
        ("review", "真实测评"),
        ("vs", "对比卖点"),
        ("before", "前后对比"),
        ("after", "前后对比"),
        ("gift", "送礼场景"),
        ("amazon", "电商提及"),
        ("deal", "优惠信息"),
        ("hack", "痛点解决"),
        ("routine", "日常场景"),
    ]
    for keyword, label in keyword_map:
        if keyword in text and label not in signals:
            signals.append(label)
    return (signals + ["真实来源", "可点击原帖"])[:3]


def _extract_youtube_renderers(page: str, renderer_name: str) -> list[dict[str, Any]]:
    renderers: list[dict[str, Any]] = []
    marker = f'"{renderer_name}":'
    index = 0
    while True:
        marker_index = page.find(marker, index)
        if marker_index == -1:
            break
        start = page.find("{", marker_index + len(marker))
        if start == -1:
            break
        end = _json_object_end(page, start)
        if end == -1:
            index = start + 1
            continue
        try:
            payload = json.loads(page[start : end + 1])
        except json.JSONDecodeError:
            index = end + 1
            continue
        if isinstance(payload, dict):
            renderers.append(payload)
        index = end + 1
    return renderers


def _json_object_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _youtube_text(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    if not isinstance(value, dict):
        return ""
    if value.get("simpleText"):
        return _text(value.get("simpleText"))
    runs = value.get("runs")
    if isinstance(runs, list):
        return _text("".join(str(run.get("text", "")) for run in runs if isinstance(run, dict)))
    return ""


def _youtube_owner(renderer: dict[str, Any]) -> tuple[str, str]:
    for key in ("ownerText", "shortBylineText", "longBylineText"):
        owner = renderer.get(key)
        if not isinstance(owner, dict):
            continue
        runs = owner.get("runs")
        if not isinstance(runs, list) or not runs:
            continue
        first = runs[0] if isinstance(runs[0], dict) else {}
        title = _text(first.get("text"))
        endpoint = first.get("navigationEndpoint") if isinstance(first.get("navigationEndpoint"), dict) else {}
        url = _youtube_endpoint_url(endpoint)
        return title, url
    return "", ""


def _youtube_endpoint_url(endpoint: dict[str, Any]) -> str:
    browse = endpoint.get("browseEndpoint") if isinstance(endpoint.get("browseEndpoint"), dict) else {}
    browse_id = _clean_id(browse.get("browseId"))
    if browse_id.startswith("UC"):
        return f"https://www.youtube.com/channel/{browse_id}"
    command = endpoint.get("commandMetadata") if isinstance(endpoint.get("commandMetadata"), dict) else {}
    web = command.get("webCommandMetadata") if isinstance(command.get("webCommandMetadata"), dict) else {}
    path = _text(web.get("url"))
    if path and re.match(r"^/(?:@|channel/|c/|user/)", path):
        return urljoin("https://www.youtube.com", path)
    return ""


def _clean_youtube_id(value: Any) -> str:
    video_id = _clean_id(value)
    return video_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) else ""


def _youtube_video_metadata(video_url: str) -> dict[str, Any]:
    try:
        page = _fetch_text_with_timeout(video_url, {}, headers=_browser_headers(), timeout_seconds=SEARCH_DISCOVERY_FETCH_SECONDS)
    except ConnectorFetchError:
        return {}

    text = _decode_unicode_escapes(html_lib.unescape(page or ""))
    title = (
        _clean_html(_first_match(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', text))
        or _clean_html(_first_match(r"<title[^>]*>(.*?)</title>", text)).removesuffix(" - YouTube")
        or _youtube_text_field(text, "title")
    )
    channel_title = (
        _text(_first_match(r'"ownerChannelName"\s*:\s*"([^"]+)"', text))
        or _text(_first_match(r'"author"\s*:\s*"([^"]+)"', text))
        or _youtube_text_field(text, "ownerText")
    )
    owner_profile = _text(_first_match(r'"ownerProfileUrl"\s*:\s*"([^"]+)"', text))
    canonical_base = _text(_first_match(r'"canonicalBaseUrl"\s*:\s*"([^"]+)"', text))
    browse_id = _clean_id(_first_match(r'"browseId"\s*:\s*"(UC[A-Za-z0-9_-]+)"', text))
    channel_url = ""
    for candidate in (owner_profile, canonical_base):
        if candidate:
            channel_url = urljoin("https://www.youtube.com", candidate)
            break
    if not channel_url and browse_id:
        channel_url = f"https://www.youtube.com/channel/{browse_id}"

    views = _int(_first_match(r'"viewCount"\s*:\s*"(\d+)"', text))
    followers = _youtube_subscriber_count(page)
    return {
        "title": title,
        "channel_title": channel_title,
        "channel_url": channel_url,
        "views": views,
        "followers": followers,
    }


def _youtube_text_field(page: str, field: str) -> str:
    marker = f'"{field}":'
    marker_index = page.find(marker)
    if marker_index == -1:
        return ""
    start = page.find("{", marker_index + len(marker))
    if start == -1:
        return ""
    end = _json_object_end(page, start)
    if end == -1:
        return ""
    try:
        payload = json.loads(page[start : end + 1])
    except json.JSONDecodeError:
        return ""
    return _youtube_text(payload)


def _youtube_channel_followers(channel_url: str) -> int:
    if not channel_url:
        return 0
    try:
        page = _fetch_text(channel_url, {}, headers=_browser_headers())
    except ConnectorFetchError as error:
        LAST_SOURCE_ERRORS["YouTube"] = str(error)
        return 0
    return _youtube_subscriber_count(page)


def _youtube_subscriber_count(page: str) -> int:
    text = _decode_unicode_escapes(html_lib.unescape(page or ""))
    header_count = _youtube_header_subscriber_count(text)
    if header_count:
        return header_count

    for field in ("subscriberCountText", "ownerSubCountText"):
        count = _youtube_text_field_count(page, field)
        if count:
            return count

    return _subscriber_count_from_text(text)


def _youtube_header_subscriber_count(text: str) -> int:
    for match in re.finditer(r'"subtitle"\s*:', text, flags=re.IGNORECASE):
        count = _subscriber_count_from_text(text[match.start() : match.start() + 900])
        if count:
            return count
    for match in re.finditer(r'"label"\s*:\s*"[^"]*?go to channel', text, flags=re.IGNORECASE):
        count = _subscriber_count_from_text(text[max(0, match.start() - 260) : match.end() + 120])
        if count:
            return count
    return 0


def _subscriber_count_from_text(text: str) -> int:
    patterns = [
        r"([\d,.]+)\s*([kmbKMB\u4e07\u4ebf]?)\s*(?:\u4f4d)?\u8ba2\u9605\u8005",
        r"([\d,.]+)\s*([kmbKMB\u4e07\u4ebf]?)\s*subscribers?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _scaled_count(match.group(1), match.group(2))
    return 0


def _youtube_text_field_count(page: str, field: str) -> int:
    marker = f'"{field}":'
    index = 0
    while True:
        marker_index = page.find(marker, index)
        if marker_index == -1:
            return 0
        start = page.find("{", marker_index + len(marker))
        if start == -1:
            return 0
        end = _json_object_end(page, start)
        if end == -1:
            index = start + 1
            continue
        try:
            payload = json.loads(page[start : end + 1])
        except json.JSONDecodeError:
            index = end + 1
            continue
        count = _view_count(_youtube_text(payload))
        if count:
            return count
        index = end + 1


def _decode_unicode_escapes(value: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match.group(1), 16)), value or "")


def _view_count(value: str) -> int:
    compact = value.lower().replace(",", "")
    match = re.search(r"([\d.]+)\s*([kmb\u4e07\u4ebf]?)", compact)
    if not match:
        return 0
    return _scaled_count(match.group(1), match.group(2))


def _scaled_count(number_value: str, suffix: str) -> int:
    number = _float(number_value)
    multiplier = {
        "k": 1_000,
        "m": 1_000_000,
        "b": 1_000_000_000,
        "\u4e07": 10_000,
        "\u4ebf": 100_000_000,
    }.get((suffix or "").lower(), 1)
    return int(number * multiplier)


def _amazon_product_links(page: str, domain: str) -> list[tuple[str, str, str, float | None]]:
    links: list[tuple[str, str, str, float | None]] = []
    seen: set[str] = set()
    for segment in re.split(r'(?=<div[^>]+data-component-type=["\']s-search-result["\'])', page or "", flags=re.IGNORECASE):
        link_match = re.search(r'href=["\']([^"\']*(?:/dp/|/gp/product/)([A-Z0-9]{10})[^"\']*)["\']', segment, flags=re.IGNORECASE)
        asin = _clean_id(_first_match(r'data-asin=["\']([A-Z0-9]{10})["\']', segment)) or (link_match.group(2).upper() if link_match else "")
        if not asin or asin in seen:
            continue
        raw_url = link_match.group(1) if link_match else f"/dp/{asin}"
        title = (
            _clean_html(_first_match(r'<h2[^>]*>.*?<span[^>]*>(.*?)</span>.*?</h2>', segment))
            or _clean_html(_first_match(r'<span[^>]*class=["\'][^"\']*a-size-base-plus[^"\']*["\'][^>]*>(.*?)</span>', segment))
            or _clean_html(_first_match(r'<span[^>]*class=["\'][^"\']*a-size-medium[^"\']*["\'][^>]*>(.*?)</span>', segment))
            or _text(html_lib.unescape(_first_match(r'aria-label=["\']([^"\']+)["\']', segment)))
        )
        links.append((asin, urljoin(f"https://{domain}", html_lib.unescape(raw_url)), title, _amazon_price(segment)))
        seen.add(asin)

    for match in re.finditer(r'<a[^>]+href=["\']([^"\']*(?:/dp/|/gp/product/)([A-Z0-9]{10})[^"\']*)["\'][^>]*>(.*?)</a>', page or "", flags=re.IGNORECASE | re.DOTALL):
        asin = match.group(2).upper()
        if asin in seen:
            continue
        raw_url = html_lib.unescape(match.group(1))
        title = _clean_html(match.group(3))
        links.append((asin, urljoin(f"https://{domain}", raw_url), title, None))
        seen.add(asin)
    return links


def _clean_product_url(value: str, domain: str, required_path: str) -> str:
    if not value:
        return ""
    url = html_lib.unescape(unquote(value))
    parsed = urlparse(url)
    if parsed.netloc and "rover.ebay" in parsed.netloc:
        target = parse_qs(parsed.query).get("mpre", [""])[0]
        if target:
            url = unquote(target)
            parsed = urlparse(url)
    if not parsed.netloc:
        url = urljoin(f"https://{domain}", url)
        parsed = urlparse(url)
    if required_path not in parsed.path:
        return ""
    clean_path = parsed.path.rstrip("/")
    return f"{parsed.scheme or 'https'}://{parsed.netloc}{clean_path}"


def _clean_html(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value or "")
    return _text(html_lib.unescape(without_tags))


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text or "", flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _title_from_url(value: str) -> str:
    path = urlparse(value).path
    parts = [
        part
        for part in path.split("/")
        if part
        and not part.isdigit()
        and not re.fullmatch(r"[A-Z0-9]{10}", part, flags=re.IGNORECASE)
        and not part.lower().startswith(("ref", "qid", "sprefix", "keywords"))
        and part.lower() not in {"itm", "listing", "dp", "gp", "product", "watch"}
    ]
    if not parts:
        return ""
    title = re.sub(r"[-_]+", " ", parts[-1])
    return _text(title).title()


def _product_title(parsed_title: str, url: str, fallback_query: str) -> str:
    for candidate in (parsed_title, _title_from_url(url), fallback_query):
        cleaned = _text(html_lib.unescape(candidate))
        if cleaned and not _bad_product_title(cleaned):
            return cleaned
    return "商品详情"


def _bad_product_title(value: str) -> bool:
    normalized = _text(value)
    if not normalized:
        return True
    lower = normalized.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lower)
    if len(compact) < 3:
        return True
    if lower in {"shop on ebay", "new listing", "sponsored", "search results"}:
        return True
    if "http://" in lower or "https://" in lower or "www." in lower:
        return True
    if re.fullmatch(r"[a-z0-9]{8,16}", compact) and re.search(r"\d", compact):
        return True
    if re.search(r"\bref\s*[=_-]?\s*(sr|sxin|nb_sb|nav|sspa)", lower):
        return True
    if re.fullmatch(r"(ref|sr|s|dp|gp|product|asin|itm|listing|search|qid|sprefix|keywords)[a-z0-9\s=_-]*", lower):
        return True
    return False


def _amazon_price(segment: str) -> float | None:
    whole = _clean_html(
        _first_match(r'<span[^>]*class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>(.*?)</span>', segment)
    )
    fraction = _clean_html(
        _first_match(r'<span[^>]*class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>(.*?)</span>', segment)
    )
    if whole:
        return _parse_price(f"{whole}.{fraction or '00'}")
    return _price_from_html(segment)


def _etsy_public_price(segment: str) -> float | None:
    return _price_from_html(segment)


def _price_from_html(segment: str) -> float | None:
    candidates = [
        _clean_html(_first_match(r'<span[^>]*class=["\'][^"\']*a-offscreen[^"\']*["\'][^>]*>(.*?)</span>', segment)),
        _clean_html(_first_match(r'<span[^>]*class=["\'][^"\']*currency-value[^"\']*["\'][^>]*>(.*?)</span>', segment)),
        _clean_html(_first_match(r'<p[^>]*class=["\'][^"\']*price[^"\']*["\'][^>]*>(.*?)</p>', segment)),
        _first_match(r'((?:US\s*)?[$€£]\s*\d[\d,.]*)', segment),
    ]
    for candidate in candidates:
        price = _parse_price(candidate)
        if price:
            return price
    return None


def _parse_price(value: str) -> float | None:
    cleaned = _text(html_lib.unescape(value or ""))
    match = re.search(r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?|\d+,\d{1,2})", cleaned)
    if not match:
        return None
    amount = match.group(1)
    if "," in amount and "." not in amount and re.search(r",\d{1,2}$", amount):
        amount = amount.replace(",", ".")
    else:
        amount = amount.replace(",", "")
    parsed = _float(amount)
    return round(parsed, 2) if parsed else None


def _public_product_metrics(query: str, title: str, window_days: int, rank: int) -> dict[str, float | int]:
    return {
        "sales_rank_proxy": max(1, rank * 120),
        "reviews": 0,
        "review_growth": _freshness_growth(utc_now_iso(), window_days),
        "rating": 4.1,
        "social_mentions": 0,
        "cross_platform_mentions": 1,
        "listing_stability": 0.68,
        "topic_match": _topic_match(query, title),
    }


def _clean_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_:\-]", "", str(value or ""))[:160]


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _int(value: Any) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _money_value(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    amount = _float(value.get("value"))
    return round(amount, 2) if amount else None


def _etsy_price(value: Any) -> float | None:
    if isinstance(value, dict):
        amount = _float(value.get("amount"))
        divisor = _float(value.get("divisor")) or 100
        return round(amount / divisor, 2) if amount else None
    amount = _float(value)
    return round(amount, 2) if amount else None


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", value.lower())
    return slug[:18] or "trend"


def _pick_tags(expanded_terms: dict[str, list[str]], rng: random.Random) -> list[str]:
    pool = expanded_terms["keywords"] + expanded_terms["hashtags"] + expanded_terms["audiences"]
    return rng.sample(pool, min(4, len(pool)))
