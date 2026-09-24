"""쇼츠 운영 기능 선언 — 텔레그램 관리 패널에서 제작·미리보기·수정·검수 완료.

제작 자체는 지금처럼 `polymarket-shorts.timer`(매일 21:00)가 돌린다. 이 기능은 그
결과를 운영하는 창이다. 쇼츠 패키지를 import하지 않고 하위 프로세스로 부른다.
"""

from services.telegram_bot.features.base import CommandSpec, FeatureSpec
from services.telegram_bot.features.shorts.handlers import USAGE, cmd_shorts

FEATURE = FeatureSpec(
    key="shorts",
    label="쇼츠 운영",
    # 웹 관리 허브(handlers/menus.py의 web_admin_menu)의 "🎬 쇼츠"로도 연다.
    commands=(CommandSpec("shorts", "쇼츠 상태·제작·수정·검수", cmd_shorts, usage=USAGE),),
    data_files=("storage/shorts/",),
)
