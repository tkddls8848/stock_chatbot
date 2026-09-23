"""종목 마스터 데이터 기능 선언."""

import logging

from services.telegram_bot.core.config import STOCK_DB_FILE
from services.telegram_bot.core.workers import run_non_urgent
from services.telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from services.telegram_bot.features.instruments.handlers import cmd_stockdb
from services.telegram_bot.stocks import StockDatabase

logger = logging.getLogger(__name__)


def _install_services(app) -> None:
    stock_db = StockDatabase(cache_file=STOCK_DB_FILE)
    stock_db.load_or_build()
    app.bot_data["stock_db"] = stock_db


async def refresh_stock_db(stock_db: StockDatabase) -> None:
    try:
        await run_non_urgent(stock_db.build)
        logger.info("[StockDB] 일별 갱신 완료")
    except Exception as e:
        logger.warning("[StockDB] 일별 갱신 실패: %s", e)


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        refresh_stock_db,
        trigger="cron",
        hour=8,
        minute=30,
        args=[app.bot_data["stock_db"]],
        id="refresh_stock_db",
    )


FEATURE = FeatureSpec(
    key="instruments",
    label="종목 데이터베이스",
    commands=(
        CommandSpec("stockdb", "종목 DB 갱신", cmd_stockdb, usage="[build]"),
    ),
    menus=(
        MenuSpec("🗂 종목 DB 갱신", "nav:stockdb", 3),
    ),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=("data/instruments/stock_db.json",),
)
