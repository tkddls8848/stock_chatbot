"""시장 리서치 기능 선언.

정해진 시각에 한 번 돌고(`RESEARCH_SCHEDULE_*`), 텔레그램 관리 패널에서 주제를 바꾸거나
지금 돌린다. 결과는 봇 상태와 웹 산출물에 같은 한 벌로 남는다(`research/job.py`).
"""

import logging

from services.telegram_bot.core.config import (
    RESEARCH_HISTORY_LIMIT,
    RESEARCH_SCHEDULE_HOUR,
    RESEARCH_SCHEDULE_MINUTE,
    RESEARCH_STATE_FILE,
)
from services.telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from services.telegram_bot.llm import build_market_view_analyzer
from services.telegram_bot.research.handlers import cmd_research
from services.telegram_bot.research.job import run_research
from services.telegram_bot.research.news import collect_global_market_news_items
from services.telegram_bot.research.state import MarketViewManager

logger = logging.getLogger(__name__)


def _install_services(app) -> None:
    app.bot_data["market_view_manager"] = MarketViewManager(
        RESEARCH_STATE_FILE,
        history_limit=RESEARCH_HISTORY_LIMIT,
    )
    app.bot_data["market_view_analyzer"] = build_market_view_analyzer()

    registry = app.bot_data["news_registry"]

    async def collect_research_news(**kwargs):
        return await collect_global_market_news_items(registry, **kwargs)

    app.bot_data["research_news_collector"] = collect_research_news


async def _run_scheduled(app) -> None:
    # 예약 실행에는 결과를 받을 사람이 없다. 실패를 삼키는 자리를 여기 하나로 둔다.
    try:
        await run_research(app)
    except Exception:
        logger.error("[RESEARCH] 예약 실행 실패", exc_info=True)


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        _run_scheduled,
        trigger="cron",
        hour=RESEARCH_SCHEDULE_HOUR,
        minute=RESEARCH_SCHEDULE_MINUTE,
        args=[app],
        id="scheduled_research",
        max_instances=1,
        coalesce=True,
    )


FEATURE = FeatureSpec(
    key="research",
    label="시장 리서치",
    requires=frozenset({"news_summary", "watchlist", "instruments", "sector_summary"}),
    commands=(
        CommandSpec("research", "리서치 주제·지금 실행", cmd_research, usage="show|set|clear|run"),
    ),
    menus=(
        MenuSpec("🔎 리서치", "nav:research", 1, "🔎 리서치", 2),
    ),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=("data/research/market_research.json",),
)
