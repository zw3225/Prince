from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal


SignalType = Literal["content", "creator", "product"]
RiskTier = Literal["low", "medium", "high"]
SourceType = Literal["official_api", "authorized_api", "third_party", "compliance_scrape", "sample"]
SourceUrlType = Literal["official_content", "official_profile", "official_product", "sample"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class RawSignal:
    id: str
    signal_type: SignalType
    platform: str
    title: str
    author: str
    url: str
    market: str
    source_type: SourceType
    risk_tier: RiskTier
    captured_at: str
    text: str
    tags: list[str]
    metrics: dict[str, float | int]
    price: float | None = None
    discount: float | None = None
    marketing_signals: list[str] = field(default_factory=list)
    source_url_type: SourceUrlType = "sample"
    source_url_note: str = "样例来源链接；真实接入后会使用平台返回的原始 URL。"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RankedEntity:
    id: str
    entity_type: SignalType | Literal["opportunity"]
    platform: str
    title: str
    author: str
    url: str
    market: str
    source_type: SourceType
    risk_tier: RiskTier
    captured_at: str
    score: int
    confidence: int
    growth: float
    price: float | None
    discount: float | None
    tags: list[str]
    marketing_signals: list[str]
    metrics: dict[str, float | int]
    reasons: list[str]
    related_ids: list[str] = field(default_factory=list)
    source_url_type: SourceUrlType = "sample"
    source_url_note: str = "样例来源链接；真实接入后会使用平台返回的原始 URL。"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrendSearch:
    id: str
    query: str
    markets: list[str]
    window_days: int
    refresh_cadence: Literal["daily"] = "daily"
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchResult:
    search: TrendSearch
    expanded_terms: dict[str, list[str]]
    content: list[RankedEntity]
    creators: list[RankedEntity]
    products: list[RankedEntity]
    opportunities: list[RankedEntity]
    source_health: list[dict[str, Any]]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "search": self.search.to_dict(),
            "expanded_terms": self.expanded_terms,
            "content": [item.to_dict() for item in self.content],
            "creators": [item.to_dict() for item in self.creators],
            "products": [item.to_dict() for item in self.products],
            "opportunities": [item.to_dict() for item in self.opportunities],
            "source_health": self.source_health,
            "summary": self.summary,
        }
