from __future__ import annotations

import hashlib
import re
from collections import Counter

from .connectors import DEFAULT_MARKETS, collect_signals, source_health
from .models import SearchResult, TrendSearch
from .scoring import build_opportunities, rank_signal


DEFAULT_WINDOW_DAYS = 30


def run_trend_search(query: str, markets: list[str] | None = None, window_days: int = DEFAULT_WINDOW_DAYS) -> SearchResult:
    cleaned_query = _clean_query(query)
    selected_markets = markets or DEFAULT_MARKETS
    search = TrendSearch(
        id=_search_id(cleaned_query, selected_markets, window_days),
        query=cleaned_query,
        markets=selected_markets,
        window_days=window_days,
    )
    expanded_terms = expand_query(cleaned_query)
    raw_signals = dedupe_signals(collect_signals(cleaned_query, expanded_terms, selected_markets, window_days))
    ranked = [rank_signal(signal, window_days) for signal in raw_signals]

    content = _sort([item for item in ranked if item.entity_type == "content"])
    creators = _sort([item for item in ranked if item.entity_type == "creator"])
    products = _sort([item for item in ranked if item.entity_type == "product"])
    opportunities = build_opportunities(content, creators, products)

    summary = build_summary(search, expanded_terms, content, creators, products, opportunities)
    return SearchResult(
        search=search,
        expanded_terms=expanded_terms,
        content=content,
        creators=creators,
        products=products,
        opportunities=opportunities,
        source_health=source_health(),
        summary=summary,
    )


def expand_query(query: str) -> dict[str, list[str]]:
    tokens = [token for token in re.split(r"[^a-zA-Z0-9]+", query.lower()) if len(token) > 1]
    base = " ".join(tokens[:4]) if tokens else query.lower()
    keywords = _unique(
        [
            base,
            f"{base} product",
            f"{base} amazon",
            f"{base} review",
            f"{base} viral",
            f"{base} routine",
            f"{base} gift",
            f"{base} alternative",
            f"{base} bundle",
            f"{base} problem solution",
        ]
    )
    audiences = _unique(
        [
            "Gen Z shoppers",
            "millennial parents",
            "fitness creators",
            "home office workers",
            "beauty enthusiasts",
            "pet owners",
            "college students",
            "gift buyers",
        ]
        + _audiences_from_query(tokens)
    )
    hashtags = _unique([f"#{token}" for token in tokens[:6]] + [f"#{''.join(tokens[:3])}", "#tiktokmademebuyit", "#amazonfinds", "#giftideas"])
    exclusions = ["giveaway only", "job post", "news recap", "unrelated celebrity mention"]
    return {
        "keywords": keywords,
        "hashtags": hashtags,
        "audiences": audiences,
        "exclusions": exclusions,
    }


def dedupe_signals(signals):
    seen: set[tuple[str, str, str, str]] = set()
    unique = []
    for signal in signals:
        identity = _fingerprint(signal.url) if signal.url else _fingerprint(signal.title)
        key = (signal.signal_type, signal.platform, signal.market, identity)
        if key in seen:
            continue
        seen.add(key)
        unique.append(signal)
    return unique


def build_summary(search: TrendSearch, expanded_terms, content, creators, products, opportunities):
    platforms = Counter([item.platform for item in content + creators + products])
    risk = Counter([item.risk_tier for item in content + creators + products])
    top_tags = Counter(tag for item in content[:20] + products[:20] for tag in item.tags).most_common(8)
    return {
        "search": search.to_dict(),
        "expanded_terms": expanded_terms,
        "totals": {
            "content": len(content),
            "creators": len(creators),
            "products": len(products),
            "opportunities": len(opportunities),
            "platforms": len(platforms),
        },
        "top_scores": {
            "content": content[0].score if content else 0,
            "creator": creators[0].score if creators else 0,
            "product": products[0].score if products else 0,
            "opportunity": opportunities[0].score if opportunities else 0,
        },
        "platform_mix": dict(platforms),
        "risk_mix": dict(risk),
        "top_tags": [{"tag": tag, "count": count} for tag, count in top_tags],
        "narrative": _summary_narrative(search.query, content, creators, products, opportunities),
    }


def _summary_narrative(query: str, content, creators, products, opportunities) -> list[str]:
    lines = []
    if not (content or creators or products or opportunities):
        return ["暂无可用真实链接；公开页面可能被平台拦截，请换关键词、扩大时间窗或稍后重试。"]
    if content:
        lines.append(f"已找到 {len(content)} 条可直达内容入口，优先查看 {content[0].platform} 上的“{content[0].title}”。")
    if creators:
        lines.append(f"已找到 {len(creators)} 个可直达达人主页，优先核对 {creators[0].platform} 上的 {creators[0].author}。")
    if opportunities:
        price_text = f"，首条机会已解析价格 {_format_price(opportunities[0].price)}" if opportunities[0].price else "，价格需点开确认"
        lines.append(
            f"已生成 {len(opportunities)} 个可验证机会入口，优先核对 {opportunities[0].platform} 的“{opportunities[0].title.replace('机会：', '')}”{price_text}。"
        )
    return lines


def _format_price(value: float | None) -> str:
    if value is None:
        return ""
    return f"${value:.2f}"


def _sort(items):
    return sorted(items, key=lambda item: (item.score, item.confidence, item.growth), reverse=True)


def _clean_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", query.strip())
    if not cleaned:
        return "home office wellness"
    return cleaned[:120]


def _search_id(query: str, markets: list[str], window_days: int) -> str:
    digest = hashlib.sha1(f"{query}|{','.join(markets)}|{window_days}".encode("utf-8")).hexdigest()
    return digest[:12]


def _fingerprint(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())[:80]


def _unique(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _audiences_from_query(tokens: list[str]) -> list[str]:
    audiences = []
    joined = " ".join(tokens)
    if "gym" in tokens or "fitness" in tokens:
        audiences.extend(["gym girls", "strength training beginners"])
    if "pet" in tokens or "dog" in tokens or "cat" in tokens:
        audiences.extend(["urban pet owners", "new puppy owners"])
    if "office" in tokens or "desk" in tokens:
        audiences.extend(["remote workers", "workspace upgraders"])
    if "gen" in tokens or "z" in tokens:
        audiences.extend(["Gen Z deal hunters", "campus trendsetters"])
    if not audiences and joined:
        audiences.append(f"{joined} buyers")
    return audiences
