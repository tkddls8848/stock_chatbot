"""웹 상태 기능 선언 — 텔레그램 관리 패널에서 공개 웹 산출물의 갱신 상태를 본다."""

from services.telegram_bot.features.base import CommandSpec, FeatureSpec, MenuSpec
from services.telegram_bot.features.web_status.handlers import cmd_web

FEATURE = FeatureSpec(
    key="web_status",
    label="웹 상태",
    commands=(CommandSpec("web", "웹 산출물 갱신 상태", cmd_web),),
    menus=(MenuSpec("🌐 웹 상태", "nav:web", 2, "🌐 웹", 2),),
)
