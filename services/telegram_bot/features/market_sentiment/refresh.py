"""국가별 뉴스 감성의 예약 갱신.

텔레그램 명령(`/market`)은 없앴다(2026-09-24). 정해진 시각에 빠진 날의 다이제스트를
보충하고 차트를 그려 웹 산출물(`data/webpub/market.json`·`market_chart.png`)로 굽는다.
결과는 `MarketDigestStore`와 웹 산출물에 같은 한 벌로 남는다.

`MarketDigestStore`의 확정된 날(`final=True`)은 다시 계산하지 않으므로 같은 기간을
다시 돌려도 지난 날의 값은 변하지 않는다. 비용은 오늘 치와 아직 빠진 날뿐이다.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import Application

from services.telegram_bot.core.config import (
    MARKET_CHART_BACKFILL_DAYS_PER_REQUEST,
    MARKET_CHART_LOOKBACK_DAYS,
    MARKET_CHART_MARKETS,
    MARKET_CHART_MIN_ARTICLES,
    MARKET_CHART_MIN_DAYS,
    MARKET_DIGEST_ARTICLES_PER_DAY,
    MARKET_DIGEST_MAX_CALLS_PER_REQUEST,
    MARKET_DIGEST_MIN_ARTICLES,
    NEWS_MARKET_BACKFILL_QUERIES,
)
from services.telegram_bot.core.workers import burst_job, run_non_urgent
from services.telegram_bot.features.market_sentiment.chart import render_market_chart
from services.telegram_bot.news import backfill_market_digests
from services.telegram_bot.state import market_history_gaps

logger = logging.getLogger(__name__)


def _spread_backfill_days(days: list, limit: int) -> list:
    """기간의 앞·중간·끝을 보존하면서 한 번의 보충 연산량을 제한한다."""
    if len(days) <= limit:
        return days
    if limit <= 1:
        return [days[-1]]
    indexes = {
        round(index * (len(days) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [days[index] for index in sorted(indexes)]


def _gaps(markets: dict, days: int) -> dict:
    return market_history_gaps(
        markets,
        set(MARKET_CHART_MARKETS),
        MARKET_CHART_MIN_ARTICLES,
        min(days, MARKET_CHART_MIN_DAYS),
        MARKET_DIGEST_MIN_ARTICLES,
    )


@burst_job("시장 감성")
async def refresh_market_sentiment(
    app: Application, days: int = MARKET_CHART_LOOKBACK_DAYS
) -> dict[str, Any] | None:
    """빠진 날을 보충하고 차트를 웹에 굽는다. 그릴 시장이 둘 미만이면 None."""
    bot_data = app.bot_data
    store = bot_data["market_digest_store"]
    required = set(MARKET_CHART_MARKETS)

    missing = await store.missing_digest_days(required, days)
    backfill_days = {
        market: _spread_backfill_days(market_days, MARKET_CHART_BACKFILL_DAYS_PER_REQUEST)
        for market, market_days in missing.items()
    }
    backfill_markets = {market for market, market_days in backfill_days.items() if market_days}
    if backfill_markets:
        await backfill_market_digests(
            store,
            bot_data["market_digest_analyzer"],
            # 번역과 같은 Cloudflare 무료 할당량을 쓰므로 세마포어로 묶는다.
            bot_data["market_digest_semaphore"],
            backfill_markets,
            NEWS_MARKET_BACKFILL_QUERIES,
            backfill_days,
            articles_per_day=MARKET_DIGEST_ARTICLES_PER_DAY,
            min_articles=MARKET_DIGEST_MIN_ARTICLES,
            max_calls=MARKET_DIGEST_MAX_CALLS_PER_REQUEST,
        )

    markets = await store.series(required, days)
    gaps = _gaps(markets, days)
    ready = {market: stats for market, stats in markets.items() if market not in gaps}
    if len(ready) < 2:
        # 불완전한 선이나 단일 점 차트는 만들지 않는다. 직전 산출물이 웹에 남는다.
        logger.warning(
            "[MARKET] 차트를 그릴 시계열이 부족하다: %s",
            ", ".join(f"{market}: {reason}" for market, reason in sorted(gaps.items())),
        )
        return None

    image = await run_non_urgent(render_market_chart, ready, days)
    from services.web.export import publish_market

    await run_non_urgent(publish_market, image.getvalue(), ready, days)
    logger.info("[MARKET] 시장 감성 갱신: %s", ", ".join(sorted(ready)))
    return ready
