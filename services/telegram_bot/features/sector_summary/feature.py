"""시세·자금흐름·섹터 요약 컨텍스트 기능 선언."""

from services.telegram_bot.core.config import (
    SECTOR_SUMMARY_CACHE_TTL_MINUTES,
    SECTOR_SUMMARY_FAILURE_COOLDOWN_MINUTES,
    SECTOR_SUMMARY_SECTOR_TOP_N,
)
from services.telegram_bot.features.base import FeatureSpec
from services.telegram_bot.stocks import QuoteService


def _install_services(app) -> None:
    app.bot_data["quote_service"] = QuoteService(
        cache_ttl_minutes=SECTOR_SUMMARY_CACHE_TTL_MINUTES,
        sector_top_n=SECTOR_SUMMARY_SECTOR_TOP_N,
        failure_cooldown_minutes=SECTOR_SUMMARY_FAILURE_COOLDOWN_MINUTES,
    )

FEATURE = FeatureSpec(
    key="sector_summary",
    label="섹터·시세 요약 데이터",
    requires=frozenset({"instruments"}),
    install_services=_install_services,
)
