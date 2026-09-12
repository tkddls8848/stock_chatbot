"""시세·자금흐름·섹터 정량 컨텍스트 기능 선언."""

from shared.core.config import (
    QUANT_CACHE_TTL_MINUTES,
    QUANT_FAILURE_COOLDOWN_MINUTES,
    QUANT_SECTOR_TOP_N,
)
from telegram_bot.features.base import FeatureSpec
from telegram_bot.stocks import QuoteService


def _install_services(app) -> None:
    app.bot_data["quote_service"] = QuoteService(
        cache_ttl_minutes=QUANT_CACHE_TTL_MINUTES,
        sector_top_n=QUANT_SECTOR_TOP_N,
        failure_cooldown_minutes=QUANT_FAILURE_COOLDOWN_MINUTES,
    )

FEATURE = FeatureSpec(
    key="quant",
    label="정량 시장 데이터",
    requires=frozenset({"instruments"}),
    install_services=_install_services,
)
