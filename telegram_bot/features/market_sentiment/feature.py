"""국가별 뉴스 감성 기능 선언."""

import asyncio

from telegram_bot.core.config import (
    MARKET_DIGEST_FILE,
    MARKET_DIGEST_RETENTION_DAYS,
)
from telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from telegram_bot.features.market_sentiment.handlers import cmd_market
from telegram_bot.llm import build_market_digest_analyzer
from telegram_bot.state import MarketDigestStore


def _install_services(app) -> None:
    app.bot_data["market_digest_store"] = MarketDigestStore(
        MARKET_DIGEST_FILE,
        MARKET_DIGEST_RETENTION_DAYS,
    )
    app.bot_data["market_digest_analyzer"] = build_market_digest_analyzer()
    # 다이제스트는 /market 한 번에 수십 번 호출될 수 있다. 번역 파이프라인과
    # 같은 무료 할당량을 쓰므로 동시 호출을 1로 묶어 순서를 예측 가능하게 둔다.
    app.bot_data["market_digest_semaphore"] = asyncio.Semaphore(1)


FEATURE = FeatureSpec(
    key="market_sentiment",
    label="국가별 뉴스 감성",
    requires=frozenset({"news"}),
    commands=(
        CommandSpec("market", "국가별 뉴스 감성", cmd_market, usage="[일수]"),
    ),
    menus=(MenuSpec("📊 시장", "nav:market", 0, "📊 시장", 1),),
    install_services=_install_services,
    data_files=(
        "data/market_sentiment/daily_digest.json",
    ),
)
