"""CLI 검토 계약으로 원고를 전달하고, 전달 완료 뒤에만 승인 시계를 시작한다."""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.telegram_bot.core.config import (
    SHORTS_RUN_TIMEOUT_SECONDS, SHORTS_STATUS_TIMEOUT_SECONDS, TELEGRAM_CHAT_ID,
)
from services.telegram_bot.features.shorts.runner import ShortsRunner

logger = logging.getLogger(__name__)


def runner(context) -> ShortsRunner:
    return context.bot_data.setdefault("shorts_runner", ShortsRunner())


def lock(context) -> asyncio.Lock:
    return context.bot_data.setdefault("shorts_lock", asyncio.Lock())


def review_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 이 원고 승인·업로드", callback_data=f"shr:a:{token}"),
        InlineKeyboardButton("⏸ 자동 승인 보류", callback_data=f"shr:p:{token}"),
    ], [
        InlineKeyboardButton("✏️ 자료·내용 수정", callback_data=f"shr:e:{token}"),
    ]])


async def send_scenario(send, item: dict) -> None:
    if item.get("state") == "error":
        await send(f"원고 확인 실패 · {item.get('date', '')}: {item.get('error') or '로그를 확인하세요'}")
        return
    token = item["token"]
    delivered = item.get("delivered_at")
    state = item.get("state")
    if state == "paused":
        timing = "자동 승인이 보류되었습니다. 승인 버튼으로 재개할 수 있습니다."
    elif state == "approved":
        timing = "승인 완료 · 업로드 대기/재시도 중입니다. 보류 버튼으로 재시도를 멈출 수 있습니다."
    elif not item.get("auto_publish", True):
        timing = "자동 업로드가 꺼져 있습니다. 검토 후 승인 버튼을 눌러 주세요."
    elif delivered:
        timing = f"자동 승인 예정: {item.get('deadline') or '-'}"
    else:
        timing = "원고가 모두 전달된 뒤 1시간 동안 수정·보류 요청이 없으면 자동 승인하여 업로드합니다."
    text = (
        f"🎬 쇼츠 원고 검토 · {item.get('date', '')}\n"
        f"검토 번호: {token}\n{timing}\n\n{item.get('script') or '원고 없음'}\n\n"
        f"자료·내용 수정: /shorts edit {token} 수정할 내용\n"
        f"보류: /shorts hold {token}"
    )
    # 일반 텍스트로 분할하므로 HTML entity/태그가 경계에서 끊기지 않는다.
    for start in range(0, len(text), 3500):
        chunk = text[start:start + 3500]
        kwargs = {"reply_markup": review_keyboard(token)} if start + 3500 >= len(text) else {}
        await send(chunk, **kwargs)


def upload_text(result: dict) -> str:
    outcome = result.get("status")
    if outcome in {"uploaded", "already_uploaded"}:
        return "YouTube 업로드 완료: " + str(result.get("url") or "")
    reasons = {
        "not_reviewed": "현재 수정본을 먼저 검수 완료하세요.",
        "no_credentials": ".env에 YouTube 자격 증명을 설정한 뒤 /shorts upload로 재시도하세요.",
        "stale": "이 원고는 새 수정본으로 대체되었습니다. /shorts review에서 확인하세요.",
    }
    return "YouTube 업로드: " + str(reasons.get(outcome) or result.get("reason") or result.get("error") or outcome or "응답을 확인하세요.")


async def poll_reviews(app) -> None:
    """재시작해도 전달 확인 전에는 마감이 생기지 않는다. 실패는 다음 주기에 재시도한다."""
    if not TELEGRAM_CHAT_ID or lock(app).locked():
        return
    async with lock(app):
        async def send(text, **kwargs):
            await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, **kwargs)

        try:
            pending = await runner(app).call(["--review-pending"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            for item in pending.get("items", []):
                if item.get("state") != "pending" or item.get("delivered_at"):
                    continue
                try:
                    await send_scenario(send, item)
                    await runner(app).call(["--review-ack", item["token"]], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
                except Exception:
                    logger.exception("[SHORTS] 원고 전달·확인 실패: %s", item.get("token"))
            results = await runner(app).call(["--review-tick"], timeout=SHORTS_RUN_TIMEOUT_SECONDS)
            notified = app.bot_data.setdefault("shorts_review_notified", set())
            for item in results.get("items", []):
                key = (item.get("token") or item.get("date"), item.get("status"))
                if key not in notified:
                    await send(f"쇼츠 {item.get('token', '')}\n{upload_text(item)}")
                    notified.add(key)
        except Exception:
            logger.exception("[SHORTS] 원고 검토 예약 작업 실패")


def install_jobs(scheduler, app) -> None:
    scheduler.add_job(poll_reviews, trigger="interval", minutes=1, args=[app],
                      id="shorts_review", max_instances=1, coalesce=True)
