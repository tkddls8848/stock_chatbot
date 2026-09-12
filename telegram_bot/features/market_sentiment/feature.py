"""국가별 뉴스 감성 기능 선언."""

import asyncio

from shared.core.clock import JST
from shared.core.config import (
    MARKET_ANOMALY_COLLECTION_ENABLED,
    MARKET_ANOMALY_ENABLED,
    MARKET_ANOMALY_FILE,
    MARKET_ANOMALY_JOB_MINUTE,
    MARKET_ANOMALY_RETENTION_DAYS,
    MARKET_DIGEST_FILE,
    MARKET_DIGEST_RETENTION_DAYS,
)
from telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from telegram_bot.features.market_sentiment.handlers import cmd_anomaly, cmd_market
from telegram_bot.features.market_sentiment.overnight import capture_completed_overnight_windows
from shared.llm import build_market_digest_analyzer, build_overnight_tone_analyzer
from telegram_bot.state import MarketDigestStore, OvernightToneStore


def _install_market_digest_services(app) -> None:
    app.bot_data["market_digest_store"] = MarketDigestStore(
        MARKET_DIGEST_FILE,
        MARKET_DIGEST_RETENTION_DAYS,
    )
    app.bot_data["market_digest_analyzer"] = build_market_digest_analyzer()
    # 다이제스트는 /market 한 번에 수십 번 호출될 수 있다. 번역 파이프라인과
    # 같은 무료 할당량을 쓰므로 동시 호출을 1로 묶어 순서를 예측 가능하게 둔다.
    # 이름은 "market_digest"지만 아노말리(overnight.py의 capture_window)도
    # 같은 Cloudflare 무료 할당량을 쓰는 오버나이트 톤 분석에서 이 락을 빌린다.
    app.bot_data["market_digest_semaphore"] = asyncio.Semaphore(1)


def _install_market_anomaly_services(app) -> None:
    if not (MARKET_ANOMALY_ENABLED or MARKET_ANOMALY_COLLECTION_ENABLED):
        return
    app.bot_data["overnight_tone_store"] = OvernightToneStore(
        MARKET_ANOMALY_FILE,
        MARKET_ANOMALY_RETENTION_DAYS,
    )
    app.bot_data["overnight_tone_analyzer"] = build_overnight_tone_analyzer()


def _install_services(app) -> None:
    _install_market_digest_services(app)
    _install_market_anomaly_services(app)


def _install_market_anomaly_jobs(scheduler, app) -> None:
    if not MARKET_ANOMALY_COLLECTION_ENABLED:
        return
    # 고정 개장 시각 cron은 DST·반휴장을 다시 손으로 구현하게 된다. 30분마다
    # 깨워 거래소 캘린더가 "이미 끝난 창"만 돌려주게 한다.
    scheduler.add_job(
        capture_completed_overnight_windows,
        trigger="cron",
        minute=MARKET_ANOMALY_JOB_MINUTE,
        timezone=JST,
        args=[app],
        id="market_anomaly_capture",
        max_instances=1,
        coalesce=True,
    )


def _install_jobs(scheduler, app) -> None:
    _install_market_anomaly_jobs(scheduler, app)


FEATURE = FeatureSpec(
    key="market_sentiment",
    label="국가별 뉴스 감성",
    requires=frozenset({"news"}),
    commands=(
        CommandSpec("market", "국가별 뉴스 감성", cmd_market, usage="[일수]"),
        CommandSpec("anomaly", "시장 서술 이상(파일럿)", cmd_anomaly, usage="[일수]"),
    ),
    # 감성과 이상 화면을 한 시장 메뉴 아래에 둔다.
    menus=(MenuSpec("📊 시장", "nav:market", 0, "📊 시장", 1),),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=(
        "data/market_sentiment/daily_digest.json",
        "data/market_sentiment/overnight_tone.json",
    ),
)
