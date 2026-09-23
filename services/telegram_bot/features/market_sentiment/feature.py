"""국가별 뉴스 감성 기능 선언.

정해진 시각에 갱신해 웹에 굽고(`refresh.py`), 텔레그램 관리 패널에서 지금 갱신할 수 있다.
"""

import asyncio
import logging

from services.telegram_bot.core.config import (
    MARKET_DIGEST_FILE,
    MARKET_DIGEST_RETENTION_DAYS,
    MARKET_SENTIMENT_SCHEDULE_HOURS,
    MARKET_SENTIMENT_SCHEDULE_MINUTE,
)
from services.telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from services.telegram_bot.features.market_sentiment.handlers import cmd_market
from services.telegram_bot.features.market_sentiment.refresh import refresh_market_sentiment
from services.telegram_bot.llm import build_market_digest_analyzer
from services.telegram_bot.state import MarketDigestStore

logger = logging.getLogger(__name__)


def _install_services(app) -> None:
    app.bot_data["market_digest_store"] = MarketDigestStore(
        MARKET_DIGEST_FILE,
        MARKET_DIGEST_RETENTION_DAYS,
    )
    app.bot_data["market_digest_analyzer"] = build_market_digest_analyzer()
    # 한 번 갱신에 다이제스트가 수십 번 호출될 수 있다. 번역 파이프라인과
    # 같은 무료 할당량을 쓰므로 동시 호출을 1로 묶어 순서를 예측 가능하게 둔다.
    app.bot_data["market_digest_semaphore"] = asyncio.Semaphore(1)


async def _run_scheduled(app) -> None:
    # 예약 실행에는 결과를 받을 사람이 없다. 실패를 삼키는 자리를 여기 하나로 둔다.
    try:
        await refresh_market_sentiment(app)
    except Exception:
        logger.error("[MARKET] 예약 갱신 실패", exc_info=True)


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        _run_scheduled,
        trigger="cron",
        hour=",".join(str(hour) for hour in MARKET_SENTIMENT_SCHEDULE_HOURS),
        minute=MARKET_SENTIMENT_SCHEDULE_MINUTE,
        args=[app],
        id="market_sentiment_refresh",
        max_instances=1,
        coalesce=True,
    )


FEATURE = FeatureSpec(
    key="market_sentiment",
    label="국가별 뉴스 감성",
    requires=frozenset({"news_summary"}),
    commands=(
        CommandSpec("market", "시장 감성 지금 갱신", cmd_market),
    ),
    menus=(MenuSpec("📊 시장 감성", "nav:market", 0, "📊 시장", 1),),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=(
        "data/market_sentiment/daily_digest.json",
    ),
)
