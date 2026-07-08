from __future__ import annotations

from datetime import datetime, timezone
from math import log10
import re

from .models import RankedEntity, RawSignal


RISK_CONFIDENCE = {"low": 94, "medium": 78, "high": 62}
SOURCE_BONUS = {"official_api": 5, "authorized_api": 2, "third_party": -4, "compliance_scrape": -8, "sample": -12}


def rank_signal(signal: RawSignal, window_days: int) -> RankedEntity:
    if signal.signal_type == "content":
        score, reasons = _content_score(signal, window_days)
    elif signal.signal_type == "creator":
        score, reasons = _creator_score(signal)
    else:
        score, reasons = _product_score(signal)

    confidence = _confidence(signal)
    growth = float(signal.metrics.get("growth_velocity") or signal.metrics.get("follower_growth") or signal.metrics.get("review_growth") or 0)
    return RankedEntity(
        id=signal.id,
        entity_type=signal.signal_type,
        platform=signal.platform,
        title=signal.title,
        author=signal.author,
        url=signal.url,
        market=signal.market,
        source_type=signal.source_type,
        risk_tier=signal.risk_tier,
        captured_at=signal.captured_at,
        score=max(1, min(100, round(score))),
        confidence=confidence,
        growth=round(growth, 2),
        price=signal.price,
        discount=signal.discount,
        tags=signal.tags,
        marketing_signals=signal.marketing_signals,
        metrics=signal.metrics,
        reasons=reasons,
        source_url_type=signal.source_url_type,
        source_url_note=signal.source_url_note,
    )


def build_opportunities(content: list[RankedEntity], creators: list[RankedEntity], products: list[RankedEntity]) -> list[RankedEntity]:
    opportunities: list[RankedEntity] = []
    candidate_products = [product for product in products if _is_opportunity_product(product)]
    ranked_products = sorted(candidate_products, key=lambda product: _product_verification_score(product), reverse=True)
    for product in ranked_products[:12]:
        related_content = _related(product, content)
        related_creators = _related(product, creators)
        cross_platform = len({item.platform for item in related_content + related_creators + [product]})
        score = _opportunity_score(product, related_content, related_creators, cross_platform)
        reasons = _opportunity_reasons(product, related_content, related_creators, cross_platform)
        if product.discount:
            reasons.append(f"公开入口解析到 {round(product.discount * 100)}% 折扣，可作为促销核对点。")
        if related_creators:
            reasons.append(f"最接近的达人匹配：{related_creators[0].platform} 上的 {related_creators[0].author}。")
        opportunities.append(
            RankedEntity(
                id=f"opp-{product.id}",
                entity_type="opportunity",
                platform=product.platform,
                title=f"机会：{product.title}",
                author=product.author,
                url=product.url,
                market=product.market,
                source_type=product.source_type,
                risk_tier=product.risk_tier,
                captured_at=product.captured_at,
                score=max(1, min(100, score)),
                confidence=min(product.confidence, 86),
                growth=max(product.growth, max((item.growth for item in related_content), default=0)),
                price=product.price,
                discount=product.discount,
                tags=product.tags,
                marketing_signals=product.marketing_signals,
                metrics={
                    "related_content": len(related_content),
                    "related_creators": len(related_creators),
                    "cross_platform_count": cross_platform,
                    "reviews": product.metrics.get("reviews", 0),
                    "rating": product.metrics.get("rating", 0),
                },
                reasons=reasons,
                related_ids=[item.id for item in related_content[:3] + related_creators[:2] + [product]],
                source_url_type=product.source_url_type,
                source_url_note=product.source_url_note,
            )
        )
    return sorted(opportunities, key=lambda item: (item.score, item.confidence, item.price or 0), reverse=True)


def _is_opportunity_product(product: RankedEntity) -> bool:
    if product.source_url_type == "sample":
        return True
    return bool(product.url and product.source_url_type == "official_product")


def _product_verification_score(product: RankedEntity) -> float:
    metrics = product.metrics or {}
    reviews = float(metrics.get("reviews", 0) or 0)
    score = 0.0
    if product.url:
        score += 30
    if product.source_url_type == "official_product":
        score += 16
    if product.price:
        score += 18
    if product.discount:
        score += min(product.discount * 60, 10)
    if _has_real_reviews(product):
        score += min(log10(max(reviews, 10)) * 7, 18)
    if _has_real_rating(product):
        score += 8
    score += {"official_api": 12, "authorized_api": 10, "compliance_scrape": 8, "third_party": 4, "sample": 2}.get(product.source_type, 0)
    return score


def _opportunity_score(
    product: RankedEntity,
    related_content: list[RankedEntity],
    related_creators: list[RankedEntity],
    cross_platform: int,
) -> int:
    score = _product_verification_score(product)
    score += min(len(related_content) * 7, 21)
    score += min(len(related_creators) * 6, 18)
    score += min(cross_platform * 5, 15)
    if any(float(item.metrics.get("views", 0) or 0) > 0 for item in related_content):
        score += 6
    if any(float(item.metrics.get("followers", 0) or 0) > 0 for item in related_creators):
        score += 6
    return max(1, min(100, round(score)))


def _opportunity_reasons(
    product: RankedEntity,
    related_content: list[RankedEntity],
    related_creators: list[RankedEntity],
    cross_platform: int,
) -> list[str]:
    reasons = []
    if product.url:
        reasons.append(f"可直达 {product.platform} 商品详情页，适合先点开验证页面状态。")
    if product.price:
        reasons.append(f"已解析到公开价格 {_price_text(product.price)}。")
    else:
        reasons.append("价格未解析到，需要点开商品详情确认。")
    if _has_real_reviews(product):
        reasons.append(f"已解析到 {_compact_number(float(product.metrics.get('reviews', 0)))} 条评价。")
    if _has_real_rating(product):
        reasons.append(f"已解析到商品评分 {round(float(product.metrics.get('rating', 0)), 1)}。")
    if related_content or related_creators:
        reasons.append(f"相关内容 / 达人入口覆盖 {cross_platform} 个平台，可顺着证据链复核需求。")
    else:
        reasons.append("暂未匹配到相关社媒或达人入口，先作为商品落地点核对。")
    return reasons


def _content_score(signal: RawSignal, window_days: int) -> tuple[float, list[str]]:
    views = float(signal.metrics.get("views", 0))
    engagement = float(signal.metrics.get("engagement_rate", 0))
    velocity = float(signal.metrics.get("growth_velocity", 0))
    sentiment = float(signal.metrics.get("sentiment", 0.5))
    topic_match = float(signal.metrics.get("topic_match", 0.5))
    cross_platform = float(signal.metrics.get("cross_platform_mentions", 1))
    freshness = _freshness(signal.captured_at, window_days)

    score = (
        min(log10(max(views, 10)) * 11, 76) * 0.26
        + min(engagement * 420, 74) * 0.22
        + min(velocity * 28, 82) * 0.2
        + freshness * 0.12
        + sentiment * 80 * 0.08
        + topic_match * 85 * 0.08
        + min(cross_platform * 10, 60) * 0.04
        + 12
    )
    reasons = [
        f"{_compact_number(views)}次播放，互动率 {round(engagement * 100, 1)}%。",
        f"内容新鲜度和平台互动会影响热度分。",
        f"主题匹配度 {round(topic_match * 100)}%，正向情绪代理值 {round(sentiment * 100)}%。",
    ]
    return score, reasons


def _creator_score(signal: RawSignal) -> tuple[float, list[str]]:
    followers = float(signal.metrics.get("followers") or signal.metrics.get("creator_reputation") or 0)
    scale_label = "粉丝" if signal.metrics.get("followers") else "声量代理值"
    avg_engagement = float(signal.metrics.get("avg_engagement_rate", 0))
    recent_hits = float(signal.metrics.get("recent_hot_posts", 0))
    follower_growth = float(signal.metrics.get("follower_growth", 0))
    brand_safety = float(signal.metrics.get("brand_safety", 0.7))
    commercial_density = float(signal.metrics.get("commercial_density", 0.4))
    topic_match = float(signal.metrics.get("topic_match", 0.5))

    score = (
        min(log10(max(followers, 10)) * 12, 78) * 0.18
        + min(avg_engagement * 430, 82) * 0.24
        + min(recent_hits * 10, 80) * 0.16
        + min(follower_growth * 80, 76) * 0.14
        + brand_safety * 75 * 0.12
        + topic_match * 82 * 0.12
        + (1 - abs(commercial_density - 0.45)) * 45 * 0.04
        + 10
    )
    reasons = [
        f"{_compact_number(followers)}{scale_label}，平均互动率 {round(avg_engagement * 100, 1)}%。",
        f"近期有 {int(recent_hits)} 条高表现内容。",
        f"品牌安全代理值 {round(brand_safety * 100)}%，主题匹配度 {round(topic_match * 100)}%。",
    ]
    return score, reasons


def _product_score(signal: RawSignal) -> tuple[float, list[str]]:
    rank_proxy = float(signal.metrics.get("sales_rank_proxy", 5000))
    reviews = float(signal.metrics.get("reviews", 0))
    review_growth = float(signal.metrics.get("review_growth", 0))
    rating = float(signal.metrics.get("rating", 4))
    social_mentions = float(signal.metrics.get("social_mentions", 0))
    cross_platform = float(signal.metrics.get("cross_platform_mentions", 1))
    stability = float(signal.metrics.get("listing_stability", 0.6))
    topic_match = float(signal.metrics.get("topic_match", 0.5))
    discount = signal.discount or 0

    rank_score = max(0, 100 - (rank_proxy / 50))
    price_fit = 72 if signal.price and 15 <= signal.price <= 80 else 54
    score = (
        rank_score * 0.18
        + min(log10(max(reviews, 10)) * 14, 72) * 0.14
        + min(review_growth * 90, 84) * 0.16
        + min((rating - 3.5) * 45, 70) * 0.1
        + min(log10(max(social_mentions, 10)) * 15, 76) * 0.14
        + min(cross_platform * 10, 70) * 0.08
        + stability * 80 * 0.08
        + min(discount * 160, 60) * 0.06
        + topic_match * 80 * 0.06
        + price_fit * 0.04
    )
    reasons = [
        f"销售排名代理值 #{int(rank_proxy)}，累计 {_compact_number(reviews)}条评价。",
        f"社媒提及 {_compact_number(social_mentions)}次，评分 {round(rating, 1)}。",
        f"Listing 稳定度 {round(stability * 100)}%，主题匹配度 {round(topic_match * 100)}%。",
    ]
    if discount:
        reasons.append(f"可见折扣 {round(discount * 100)}%，强化营销钩子。")
    return score, reasons


def _confidence(signal: RawSignal) -> int:
    base = RISK_CONFIDENCE[signal.risk_tier] + SOURCE_BONUS[signal.source_type]
    if signal.url:
        base += 2
    if signal.metrics:
        base += 2
    return max(35, min(98, base))


def _freshness(captured_at: str, window_days: int) -> float:
    try:
        captured = datetime.fromisoformat(captured_at)
    except ValueError:
        return 50
    age_hours = max(0.0, (datetime.now(timezone.utc) - captured).total_seconds() / 3600)
    window_hours = max(1, window_days * 24)
    return max(25, 100 - (age_hours / window_hours) * 75)


def _related(seed: RankedEntity, candidates: list[RankedEntity]) -> list[RankedEntity]:
    seed_tags = set(seed.tags)
    seed_tokens = _match_tokens(seed.title, seed.author, *seed.tags)
    matches = []
    for item in candidates:
        tag_match = bool(seed_tags.intersection(item.tags))
        token_overlap = len(seed_tokens.intersection(_match_tokens(item.title, item.author, *item.tags)))
        if tag_match or token_overlap >= 2:
            matches.append(item)
    return sorted(matches, key=lambda item: (_related_evidence_score(item), item.score), reverse=True)


def _related_evidence_score(item: RankedEntity) -> float:
    metrics = item.metrics or {}
    if item.entity_type == "content":
        return min(log10(max(float(metrics.get("views", 0) or 0), 10)) * 10, 70) + (12 if item.url else 0)
    if item.entity_type == "creator":
        return min(log10(max(float(metrics.get("followers", 0) or 0), 10)) * 9, 70) + (12 if item.url else 0)
    return 0


def _match_tokens(*values: str) -> set[str]:
    joined = " ".join(str(value or "").lower() for value in values)
    return {token for token in re.split(r"[^a-z0-9]+", joined) if len(token) >= 3}


def _has_real_reviews(entity: RankedEntity) -> bool:
    return float(entity.metrics.get("reviews", 0) or 0) > 0


def _has_real_rating(entity: RankedEntity) -> bool:
    rating = float(entity.metrics.get("rating", 0) or 0)
    if not rating:
        return False
    return round(rating, 1) not in {4.0, 4.1, 4.2, 4.3}


def _price_text(value: float | None) -> str:
    if value is None:
        return ""
    return f"${value:.2f}"


def _compact_number(value: float) -> str:
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(int(value))
