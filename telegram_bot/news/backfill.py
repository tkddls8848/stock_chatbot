"""차트용 일별 시장 감성 수집.

하루치 헤드라인을 모아 **한 번의 LLM 호출**로 그날의 감성을 계산한다. 기사마다
번역·감성 분석을 돌리던 방식은 헤드라인 한 줄을 보려고 프롬프트 전체를 매번 다시
보내는 낭비였고(실측: 프롬프트 912자 vs 기사 193자), 하루 20건×4시장×30일이면
34,560 Neurons로 무료 한도의 3.5배가 나와 애초에 불가능했다. 다이제스트 방식은
같은 범위가 약 1,044 Neurons다.

결과는 `MarketDigestStore`에 캐싱되고, 날이 지난 항목은 `final=True`로 굳어
다시 계산되지 않는다. 그래서 같은 기간을 다시 조회해도 값이 변하지 않는다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from shared.core.clock import today as clock_today

from shared.llm.market_digest import MarketDigestAnalyzer, MarketDigestError
from telegram_bot.news.sources import fetch_google_news_history
from telegram_bot.news.utils import filter_articles_for_jst_day
from telegram_bot.state import MarketDigestStore

logger = logging.getLogger(__name__)


def _interleave_market_days(
    markets: set[str],
    days_by_market: dict[str, list[date]] | None,
    default_days: list[date],
) -> list[tuple[str, date]]:
    """(시장, 날짜) 작업을 시장 순환 순서로 배치한다.

    시장을 하나씩 끝까지 처리하면 호출 예산이 앞 시장(정렬상 CN)에서 다 소진돼
    KR·US 구간이 비고, 차트가 다시 중화권 전용이 된다. 상한이 걸리는 자리에는
    시장 회전을 넣는다.
    """
    queues = {
        market: list(
            days_by_market.get(market, [])
            if days_by_market is not None
            else default_days
        )
        for market in sorted(markets)
    }
    ordered: list[tuple[str, date]] = []
    for index in range(max((len(days) for days in queues.values()), default=0)):
        for market, days in queues.items():
            if index < len(days):
                ordered.append((market, days[index]))
    return ordered


async def backfill_market_digests(
    store: MarketDigestStore,
    analyzer: MarketDigestAnalyzer,
    analyze_semaphore: asyncio.Semaphore,
    markets: set[str],
    queries: dict[str, str],
    days_by_market: dict[str, list[date]],
    *,
    articles_per_day: int,
    min_articles: int,
    max_calls: int,
) -> dict[str, int]:
    """부족한 (시장, 날짜)의 일별 감성을 계산해 캐시에 채운다.

    날짜별로 정확히 그 하루만 질의하므로 오늘 헤드라인이 과거 포인트로
    둔갑하지 않는다. 반환값은 시장별로 새로 계산한 날 수다.
    """
    computed = {market: 0 for market in markets}
    calls = 0
    today = clock_today()

    for market, day in _interleave_market_days(markets, days_by_market, []):
        if calls >= max_calls:
            break
        query = queries.get(market)
        if not query:
            logger.warning("[DIGEST] no history query configured for %s", market)
            continue
        try:
            articles = await asyncio.to_thread(
                fetch_google_news_history, query, day, market
            )
        except Exception as exc:
            logger.warning("[DIGEST] fetch failed for %s %s: %s", market, day, exc)
            continue
        articles = filter_articles_for_jst_day(articles, day)[:articles_per_day]
        if len(articles) < min_articles:
            # 표본이 얕은 날을 계산해 봐야 차트가 다시 출렁인다. 건너뛰고
            # 다음 호출에서 다시 시도한다.
            logger.info(
                "[DIGEST] %s %s 표본 부족(%d/%d), 건너뜀",
                market,
                day,
                len(articles),
                min_articles,
            )
            continue

        headlines = [article.title for article in articles if article.title]
        calls += 1
        try:
            async with analyze_semaphore:
                result = await asyncio.to_thread(
                    analyzer.analyze, market, day.isoformat(), headlines
                )
        except MarketDigestError as exc:
            logger.warning("[DIGEST] %s %s 분석 실패: %s", market, day, exc)
            continue
        except Exception as exc:
            logger.warning("[DIGEST] %s %s 예상치 못한 실패: %s", market, day, exc)
            continue

        await store.put(
            market,
            day,
            sentiment=result["sentiment"],
            article_count=len(headlines),
            summary=result.get("summary", ""),
            positive=result.get("positive"),
            negative=result.get("negative"),
            neutral=result.get("neutral"),
            headlines=headlines,
            # 오늘은 아직 기사가 다 모이지 않았으므로 확정하지 않는다.
            # 확정해 버리면 장중에 하루치가 몇 건에서 멈춘다.
            final=day < today,
        )
        computed[market] += 1

    logger.info(
        "[DIGEST] 백필 완료: 계산=%d일, 호출=%d회%s, 시장별=%s",
        sum(computed.values()),
        calls,
        (
            f" (요청 상한 {max_calls}회 도달, 남은 날은 다음 호출에서 이어서 채움)"
            if calls >= max_calls
            else ""
        ),
        {market: count for market, count in sorted(computed.items()) if count},
    )
    return computed
