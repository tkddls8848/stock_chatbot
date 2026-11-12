"""뷰 검증 기능 선언 — 운영자 개인용 `/view`.

내 시장 뷰를 예측 컨센서스에 대조하고, 다듬은 뷰로 ETF·ETN·관심종목 포지션 아이디어를 낸다. 공개 웹이 아니라
허용된 채팅만 보는 이 방에 둔다(운영자 지시 2026-11-12). 예약 작업은 없고 명령으로만 돈다.
"""

from services.telegram_bot.features.base import CommandSpec, FeatureSpec
from services.telegram_bot.features.view_check.handlers import USAGE, cmd_view
from services.telegram_bot.llm import build_view_checker


def _install_services(app) -> None:
    # 기동할 때 만든다 — 프롬프트 파일이 없으면 첫 실행이 아니라 여기서 드러난다.
    app.bot_data["view_checker"] = build_view_checker()


FEATURE = FeatureSpec(
    key="view_check",
    label="뷰 검증",
    # 포지션 후보의 개별주는 관심종목에서 온다.
    requires=frozenset({"watchlist"}),
    commands=(CommandSpec("view", "내 뷰를 컨센서스로 검증·포지션 아이디어", cmd_view, usage=USAGE),),
    install_services=_install_services,
    data_files=("storage/bot/view_check/view_check.json",),
)
