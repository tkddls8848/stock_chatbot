"""텔레그램 명령어 접근 제어.

ALLOWED_CHAT_IDS에 있는 chat_id에서 온 업데이트만 처리하고, 나머지는 조용히
무시한다(봇 존재를 드러내지 않기 위해 응답하지 않는다). 목록이 비는 경우는
없다 — `core/config.py`가 기동 시점에 막는다. 여기에 "비면 모두 허용" 분기를
되살리면 설정 누락이 곧바로 공개 봇이 된다.
"""

import asyncio
import functools
import logging
from typing import Awaitable, Callable

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import ContextTypes

from services.telegram_bot.core.config import (
    ALLOWED_CHAT_IDS,
    TELEGRAM_STATUS_MAX_ATTEMPTS,
    TELEGRAM_STATUS_RETRY_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


async def _status_request(operation, failure_message: str):
    """Best-effort status UI: it must never hold up the actual command."""
    for attempt in range(TELEGRAM_STATUS_MAX_ATTEMPTS):
        try:
            return await operation()
        except (TimedOut, NetworkError):
            if attempt + 1 < TELEGRAM_STATUS_MAX_ATTEMPTS:
                await asyncio.sleep(TELEGRAM_STATUS_RETRY_DELAY_SECONDS)
                continue
            logger.warning(failure_message, exc_info=True)
    return None


def _run_status_task(coroutine) -> None:
    task = asyncio.create_task(coroutine)

    def _log_unexpected_error(done_task) -> None:
        if done_task.cancelled():
            return
        try:
            done_task.result()
        except Exception:
            logger.warning("[STATUS] 상태 메시지 작업 실패", exc_info=True)

    task.add_done_callback(_log_unexpected_error)


async def _send_status(message, label: str):
    return await _status_request(
        lambda: message.reply_text(f"⏳ {label} 처리 중..."),
        "[STATUS] 처리 상태 메시지 전송 실패",
    )


async def _finish_status(status_task, text: str, failure_message: str) -> None:
    status = await status_task
    if status is None:
        return
    await _status_request(lambda: status.edit_text(text), failure_message)

_HANDLER_LABELS = {
    "cmd_start": "메뉴 열기",
    "cmd_help": "도움말 조회",
    "cmd_menu": "관심종목 메뉴 조회",
    "cmd_add": "관심종목 추가",
    "cmd_list": "관심종목 목록 조회",
    "cmd_market": "시장 감성 갱신",
    "cmd_research": "리서치 관리",
    "cmd_web": "웹 상태 조회",
    "cmd_briefing": "브리핑 생성",
    "cmd_stockdb": "종목 DB 갱신",
    "cmd_system": "시스템 상태 조회",
}

_MENU_LABELS = {
    "market": "시장 감성 갱신",
    "watch": "관심종목 관리",
    "research": "리서치 관리",
    "web": "웹 관리",
    "briefing": "브리핑 생성",
    "system": "시스템 상태 조회",
    "stockdb": "종목 DB 갱신",
    "home": "메인 메뉴 열기",
    "help": "도움말 조회",
}


def request_label(update: Update, handler_name: str) -> str:
    query = getattr(update, "callback_query", None)
    data = str(getattr(query, "data", ""))
    if data.startswith("nav:"):
        return _MENU_LABELS.get(data.removeprefix("nav:").split(":", 1)[0], "메뉴 작업")
    if data.startswith("remove:"):
        return "관심종목 삭제"
    return _HANDLER_LABELS.get(handler_name, "요청 작업")


def is_allowed_update(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.id in ALLOWED_CHAT_IDS


def restricted(handler: Handler, show_status: bool = True) -> Handler:
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not is_allowed_update(update):
            chat = update.effective_chat
            logger.warning(
                "[ACCESS] 허용되지 않은 채팅의 요청 무시: chat_id=%s",
                chat.id if chat else "unknown",
            )
            return
        message = getattr(update, "effective_message", None)
        status_task = None
        label = request_label(update, handler.__name__)
        bot_data = getattr(context, "bot_data", {})
        background_tasks_before = len(bot_data.get("research_tasks", set()))
        query = getattr(update, "callback_query", None)
        callback_data = str(getattr(query, "data", ""))
        suppress_menu_status = (
            handler.__name__ in {"cmd_research", "cmd_market"}
            or callback_data == "nav:research:run"
            or callback_data == "nav:market"
            or callback_data == "nav:briefing"
            or callback_data.startswith("nav:briefing:")
        )
        if show_status and not suppress_menu_status and message is not None:
            status_task = asyncio.create_task(_send_status(message, label))

        try:
            await handler(update, context)
        except Exception:
            if status_task is not None:
                _run_status_task(
                    _finish_status(
                        status_task,
                        f"❌ {label} 처리 실패. 잠시 후 다시 시도해 주세요.",
                        "[STATUS] 실패 상태 메시지 갱신 실패",
                    )
                )
            raise
        else:
            if status_task is not None:
                background_tasks_after = len(bot_data.get("research_tasks", set()))
                completion_text = (
                    f"⏳ {label} 백그라운드 실행 중..."
                    if background_tasks_after > background_tasks_before
                    else f"✅ {label} 처리 완료"
                )
                _run_status_task(
                    _finish_status(
                        status_task,
                        completion_text,
                        "[STATUS] 완료 상태 메시지 갱신 실패",
                    )
                )

    return wrapper
