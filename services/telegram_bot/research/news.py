"""리서치(시황 분석)용 뉴스 수집기.

전역 속보에서 분석 입력용 뉴스 아이템을 모은다. 뉴스 파이프라인과 동일한
NewsSourceRegistry를 공유해 소스 페일오버를 그대로 따른다.

수집은 시장 균형을 맞춘다. 소스 우선순위대로 상한까지 채우면 첫 소스
(중화권)가 전부 가져가 미국·한국 뉴스가 분석 입력에 들어가지 못하므로,
기사를 시장별로 모은 뒤 RESEARCH_NEWS_MARKETS 순서로 라운드로빈 선택한다.
분석 모델이 다국어를 직접 읽으므로 별도 번역 호출은 하지 않는다.
"""

import asyncio
import logging
from typing import Any

from services.telegram_bot.core.config import (
    NEWS_LIVE_MAX_AGE_HOURS,
    NEWS_SOURCE_FETCH_TIMEOUT_SECONDS,
    RESEARCH_NEWS_CONTENT_MAX_CHARS,
    RESEARCH_NEWS_GLOBAL_LIMIT,
    RESEARCH_NEWS_MARKETS,
    RESEARCH_NEWS_MAX_ITEMS,
)
from services.telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from services.telegram_bot.news.utils import filter_recent_articles

logger = logging.getLogger(__name__)

_OTHER_MARKET = "OTHER"


async def _fetch_source(func, *args):
    return await asyncio.wait_for(
        asyncio.to_thread(func, *args),
        timeout=NEWS_SOURCE_FETCH_TIMEOUT_SECONDS,
    )


def _make_news_item(
    source: str,
    title: str,
    content: str,
    published_at: str,
    url: str = "",
    market: str = "",
) -> dict[str, Any]:
    # 시나처럼 제목 없이 본문만 오는 소스가 있다. 번역을 켜면 모델이 제목을
    # 만들어 주지만, 원문을 그대로 쓸 때는 본문 앞부분을 제목으로 삼는다.
    title = title or content[:60]
    return {
        "source": source,
        "market": market,
        "title": title[:240],
        "content": content[:RESEARCH_NEWS_CONTENT_MAX_CHARS],
        "published_at": published_at,
        "url": url,
    }


def _article_market(article, spec: SourceSpec) -> str:
    """기사 시장 태그. 혼합 소스(gnews)는 기사별 extra가 권위 있는 값이다."""
    extra = getattr(article, "extra", None) or {}
    return str(extra.get("market") or spec.market or "").upper() or _OTHER_MARKET


def select_balanced_articles(
    buckets: dict[str, list[tuple[SourceSpec, Any]]],
    max_items: int,
    markets: tuple[str, ...] | list[str] = RESEARCH_NEWS_MARKETS,
) -> list[tuple[SourceSpec, Any]]:
    """시장 버킷을 라운드로빈으로 훑어 max_items개를 고른다.

    markets에 적힌 순서를 먼저 돌고, 목록에 없는 시장은 그 뒤에 이어 붙인다.
    비어 있는 시장은 건너뛰므로, 한 시장만 수집돼도 상한까지 채운다.
    """
    ordered_markets = [market for market in dict.fromkeys(markets) if buckets.get(market)]
    ordered_markets += [
        market
        for market in buckets
        if market not in ordered_markets and buckets.get(market)
    ]

    selected: list[tuple[SourceSpec, Any]] = []
    cursors = {market: 0 for market in ordered_markets}
    while len(selected) < max_items:
        progressed = False
        for market in ordered_markets:
            if len(selected) >= max_items:
                break
            cursor = cursors[market]
            queue = buckets[market]
            if cursor >= len(queue):
                continue
            selected.append(queue[cursor])
            cursors[market] = cursor + 1
            progressed = True
        if not progressed:
            break
    return selected


async def _collect_articles_by_market(
    registry: NewsSourceRegistry,
) -> dict[str, list[tuple[SourceSpec, Any]]]:
    """활성 소스를 동시에 조회해 시장별 기사 버킷을 만든다(실패는 건너뜀)."""
    specs = registry.active_specs()
    if not specs:
        return {}

    results = await asyncio.gather(
        *(_fetch_source(spec.fetch) for spec in specs),
        return_exceptions=True,
    )

    buckets: dict[str, list[tuple[SourceSpec, Any]]] = {}
    for spec, result in zip(specs, results):
        if isinstance(result, TimeoutError):
            registry.record_failure(spec.key, "timeout")
            logger.error(
                "[RESEARCH] %s news collection timed out: %.1f seconds",
                spec.key,
                NEWS_SOURCE_FETCH_TIMEOUT_SECONDS,
            )
            continue
        if isinstance(result, BaseException):
            registry.record_failure(spec.key, str(result))
            logger.error("[RESEARCH] %s news collection failed: %s", spec.key, result)
            continue

        registry.record_success(spec.key)
        articles = filter_recent_articles(result, NEWS_LIVE_MAX_AGE_HOURS)
        for article in articles[:RESEARCH_NEWS_GLOBAL_LIMIT]:
            buckets.setdefault(_article_market(article, spec), []).append((spec, article))
    return buckets


async def collect_global_market_news_items(
    registry: NewsSourceRegistry | None = None,
    max_items: int = RESEARCH_NEWS_MAX_ITEMS,
    markets: tuple[str, ...] | list[str] = RESEARCH_NEWS_MARKETS,
) -> list[dict[str, Any]]:
    """레지스트리에서 시장 균형을 맞춘 원문 분석 입력을 만든다."""
    if registry is None:
        logger.warning("[RESEARCH] news registry가 없어 전역 뉴스 수집을 건너뜁니다.")
        return []

    buckets = await _collect_articles_by_market(registry)
    if not buckets:
        return []

    selected = select_balanced_articles(buckets, max_items, markets)
    logger.info(
        "[RESEARCH] 뉴스 후보 시장 분포: %s → 선택 %d건",
        {market: len(rows) for market, rows in buckets.items()},
        len(selected),
    )

    news_items: list[dict[str, Any]] = []
    for spec, article in selected:
        market = _article_market(article, spec)
        news_items.append(
            _make_news_item(
                spec.label,
                article.title,
                article.content,
                article.published_at,
                article.url,
                market=market,
            )
        )

    return news_items[:max_items]
