"""종목 감성 뷰 기능 선언."""

from shared.core.config import PREDICTION_LOG_FILE
from telegram_bot.features.base import CallbackSpec, CommandSpec, FeatureSpec
from telegram_bot.features.signal_scoring.handlers import cmd_view, handle_view_callback
from telegram_bot.state import PredictionLog


def _install_services(app) -> None:
    app.bot_data["prediction_log"] = PredictionLog(PREDICTION_LOG_FILE)

FEATURE = FeatureSpec(
    key="signal_scoring",
    label="종목 감성 뷰",
    requires=frozenset({"news", "watchlist", "instruments"}),
    commands=(
        CommandSpec("view", "종목 감성 보기", cmd_view, usage="[종목코드]"),
    ),
    # 관심종목 목록의 "📈 감성" 버튼(watchlist/keyboards.py)이 이 접두사로 들어온다.
    callbacks=(CallbackSpec(("view:",), handle_view_callback),),
    install_services=_install_services,
    data_files=("data/signal_scoring/prediction_log.jsonl",),
)
