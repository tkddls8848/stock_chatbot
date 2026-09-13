"""텔레그램 콜백 라우팅과 메뉴 구성.

명령 구현은 각 기능 패키지(features/*/handlers.py, watchlist, research,
briefing)에 있고, 여기는 공통 진입점(콜백 라우팅·메뉴 등록)만 남긴다.
콜백은 nav 메뉴 처리 후 FeatureRegistry.dispatch_callback으로 각 기능이
선언한 CallbackSpec에 위임한다.
"""

import logging

from telegram import MenuButtonCommands, Update
from telegram.error import NetworkError
from telegram.ext import Application, ContextTypes

from telegram_bot.core.config import TELEGRAM_CHAT_ID
from telegram_bot.handlers.navigation import handle_menu_callback, persistent_menu

logger = logging.getLogger(__name__)


async def _answer_callback_safely(query) -> None:
    try:
        await query.answer()
    except NetworkError as exc:
        # A callback acknowledgement is best-effort. Telegram may have accepted
        # it even when the response times out, so keep processing the action.
        logger.warning("[TELEGRAM] 버튼 응답 확인 실패(동작은 계속): %s", exc)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await _answer_callback_safely(query)
    data = query.data

    if await handle_menu_callback(update, context, data):
        return

    registry = context.bot_data["feature_registry"]
    await registry.dispatch_callback(query, context, data)


async def configure_telegram_menu(app: Application) -> None:
    registry = app.bot_data["feature_registry"]
    commands = registry.telegram_commands()
    await app.bot.set_my_commands(commands)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    try:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="⌨️ 봇 재시작에 맞춰 하단 메뉴를 갱신했습니다.",
            reply_markup=persistent_menu(registry),
        )
    except Exception:
        logger.warning("Telegram 하단 메뉴 자동 갱신 실패", exc_info=True)
    logger.info("Telegram Menu 버튼 명령어 등록 완료: %s", [cmd.command for cmd in commands])
