"""명령·메뉴 없이 매일 잔량과 초과 요금을 확인한다."""

from services.telegram_bot.features.base import FeatureSpec
from services.telegram_bot.features.billing_alert.service import install_jobs

FEATURE = FeatureSpec(
    key="billing_alert",
    label="유료 API 잔량 알림",
    install_jobs=install_jobs,
    data_files=("storage/bot/billing_alert/state.json",),
)
