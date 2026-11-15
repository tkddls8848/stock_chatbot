"""명령·메뉴 없이 매시간 공개 트렌드를 읽는 운영자 알림."""

from services.telegram_bot.features.base import FeatureSpec
from services.telegram_bot.features.consensus_alert.service import install_jobs

FEATURE = FeatureSpec(
    key="consensus_alert",
    label="컨센서스 조기 이동 알림",
    install_jobs=install_jobs,
    data_files=("storage/bot/consensus_alert/state.json",),
)
