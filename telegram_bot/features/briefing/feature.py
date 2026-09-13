"""모닝·마감 브리핑 기능 선언."""

import logging

from telegram_bot.briefing import (
    TradeCalendar,
    cmd_briefing,
    send_evening_briefing,
    send_morning_briefing,
)
from telegram_bot.core.config import (
    BRIEFING_EVENING_ENABLED,
    BRIEFING_EVENING_HOUR,
    BRIEFING_EVENING_MINUTE,
    BRIEFING_MORNING_ENABLED,
    BRIEFING_MORNING_HOUR,
    BRIEFING_MORNING_MINUTE,
)
from telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from telegram_bot.llm import build_briefing_writer

logger = logging.getLogger(__name__)


def _install_services(app) -> None:
    app.bot_data["trade_calendar"] = TradeCalendar()
    app.bot_data["briefing_writer"] = build_briefing_writer()


async def _run_scheduled(action, app, label: str) -> None:
    """예약 실행 전용 경계.

    브리핑 전송 실패는 `/briefing`에서는 명령 실패로 보여야 하지만, 예약
    실행에는 결과를 받을 사람이 없다. 그래서 실패를 삼키는 자리를 함수 안이
    아니라 여기 하나로 둔다 — 수동 실행은 이 경계를 지나지 않는다.
    """
    try:
        await action(app)
    except Exception:
        logger.error("[BRIEFING] %s 예약 실행 실패", label, exc_info=True)


def _install_jobs(scheduler, app) -> None:
    if BRIEFING_MORNING_ENABLED:
        scheduler.add_job(
            _run_scheduled,
            trigger="cron",
            hour=BRIEFING_MORNING_HOUR,
            minute=BRIEFING_MORNING_MINUTE,
            args=[send_morning_briefing, app, "모닝 브리핑"],
            id="morning_briefing",
            max_instances=1,
            coalesce=True,
        )
    if BRIEFING_EVENING_ENABLED:
        scheduler.add_job(
            _run_scheduled,
            trigger="cron",
            hour=BRIEFING_EVENING_HOUR,
            minute=BRIEFING_EVENING_MINUTE,
            args=[send_evening_briefing, app, "마감 브리핑"],
            id="evening_briefing",
            max_instances=1,
            coalesce=True,
        )


FEATURE = FeatureSpec(
    key="briefing",
    label="브리핑",
    requires=frozenset({"news", "watchlist", "research", "quant"}),
    commands=(
        CommandSpec(
            "briefing",
            "브리핑 생성",
            cmd_briefing,
            usage="[morning|intraday|evening]",
        ),
    ),
    menus=(
        MenuSpec("📰 브리핑", "nav:briefing", 1, "📰 브리핑", 2),
    ),
    install_services=_install_services,
    install_jobs=_install_jobs,
)
