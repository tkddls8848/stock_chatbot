"""텔레그램 관리 패널의 쇼츠 운영(`/shorts`).

| 명령 | 하는 일 |
|---|---|
| `/shorts` | 최근 제작 상태·검수 여부·다음 예약 시각 |
| `/shorts run` (`run force`) | 지금 제작(하루 한 편 규칙을 넘기려면 force) |
| `/shorts preview` | 현재 수정본 MP4와 제목·설명·태그 |
| `/shorts edit [검토번호] <자연어>` | 자료·내용 수정 → 재렌더 → 새 원고 검토 |
| `/shorts review` · `/shorts hold [검토번호]` | 원고 보기 · 자동 승인 보류 |
| `/shorts done` | 현재 수정본을 검수 완료로 기록하고 업로드 |
| `/shorts upload` | 검수 완료본 업로드 재시도 |

제작·수정은 수 분이 걸려 접수 안내 뒤 백그라운드로 돌고 끝나면 알린다. 잠금 하나로
줄을 세운다 — 제작과 수정이 겹치면 같은 산출물 폴더를 서로 덮는다(예약 제작과의
충돌은 쇼츠 쪽 폴더 잠금이 막는다). 업로드는 검수 완료본만 기본 비공개로 한다.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.config import (
    SHORTS_EDIT_TIMEOUT_SECONDS,
    SHORTS_RUN_TIMEOUT_SECONDS,
    SHORTS_SCHEDULE_HOUR,
    SHORTS_STATUS_TIMEOUT_SECONDS,
    SHORTS_TELEGRAM_VIDEO_MAX_BYTES,
)
from services.telegram_bot.features.shorts.runner import ShortsError
from services.telegram_bot.features.shorts.review import (
    lock as _lock, runner as _runner, send_scenario, upload_text,
)

logger = logging.getLogger(__name__)

USAGE = "/shorts · /shorts run [force] · /shorts review · /shorts preview · /shorts edit [검토번호] 수정할 내용 · /shorts hold [검토번호] · /shorts done [검토번호] · /shorts upload"
_TOKEN = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:ko|en)-[0-9a-f]{32}$")
_REVIEW = {"pending": "검수 대기", "reviewed": "검수 완료", "superseded": "새 수정본으로 대체됨"}
_SELECTION = {"no_suitable_issues": "적합한 이슈 없음", "failed": "제작 실패", "script_ready": "원고 준비됨"}


def next_schedule_text(moment=None) -> str:
    moment = moment or now()
    scheduled = moment.replace(hour=SHORTS_SCHEDULE_HOUR, minute=0, second=0, microsecond=0)
    if scheduled <= moment:
        scheduled += timedelta(days=1)
    return scheduled.strftime("%m-%d %H:%M")


def status_text(status: dict[str, Any], *, busy: bool) -> str:
    lines = ["<b>🎬 쇼츠</b>"]
    if status.get("state") == "empty":
        lines.append("아직 제작한 영상이 없습니다.")
    else:
        lines.append(f"최근 제작일: {html.escape(str(status.get('date') or '-'))}")
        selection = status.get("selection_status")
        if selection and selection != "script_ready":
            lines.append(f"선별: {html.escape(_SELECTION.get(selection, selection))}")
        if status.get("failure"):
            lines.append(f"실패: {html.escape(str(status['failure'])[:200])}")
        review = status.get("review_status")
        if review:
            revision = " · 수정본" if status.get("revision") else ""
            lines.append(f"검수: {html.escape(_REVIEW.get(review, review))}{revision}")
            title = (status.get("metadata") or {}).get("title")
            if title:
                lines.append(f"제목: {html.escape(str(title))}")
            if status.get("duration_seconds"):
                lines.append(f"길이: {float(status['duration_seconds']):.0f}초")
    approval = status.get("approval") or {}
    if approval:
        states = {"pending": "원고 검토 대기", "paused": "자동 승인 보류", "approved": "승인 완료"}
        lines.append("시나리오: " + html.escape(states.get(approval.get("state"), str(approval.get("state")))))
        if approval.get("deadline"):
            lines.append("자동 승인 예정: " + html.escape(str(approval["deadline"])))
        elif approval.get("state") == "pending":
            lines.append("원고 전달 완료 후 1시간 뒤 자동 승인")
    lines.append(f"다음 예약 제작: {next_schedule_text()} (한국 시간)")
    if status.get("uploaded"):
        lines.append("YouTube 업로드 완료: " + html.escape(str(status.get("url") or "")))
    if busy:
        lines.append("⏳ 지금 제작·수정 작업이 돌고 있습니다.")
    return "\n".join(lines)


def shorts_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ 지금 제작", callback_data="nav:shorts:run"),
         InlineKeyboardButton("🎞 미리보기", callback_data="nav:shorts:preview")],
        [InlineKeyboardButton("✏️ 수정 요청", callback_data="nav:shorts:edit"),
         InlineKeyboardButton("✅ 검수 완료", callback_data="nav:shorts:done")],
        [InlineKeyboardButton("📜 원고 검토", callback_data="nav:shorts:review"),
         InlineKeyboardButton("⏸ 보류", callback_data="nav:shorts:hold")],
        [InlineKeyboardButton("🏠 처음", callback_data="nav:home")],
    ])


async def _send_preview(message, status: dict[str, Any]) -> None:
    video = status.get("video_path")
    if not video:
        await message.reply_text("보낼 영상이 없습니다.")
        return
    metadata = status.get("metadata") or {}
    caption = html.escape(str(metadata.get("title") or "쇼츠"))[:900]
    if (status.get("video_bytes") or 0) > SHORTS_TELEGRAM_VIDEO_MAX_BYTES:
        await message.reply_text(f"영상이 텔레그램 업로드 상한(50MB)을 넘습니다. 서버 경로: {video}")
    else:
        with Path(video).open("rb") as stream:
            await message.reply_video(stream, caption=caption, parse_mode="HTML",
                                      supports_streaming=True, read_timeout=120, write_timeout=120)
    details = [
        "<b>설명</b>", html.escape(str(metadata.get("description") or "-")),
        "<b>태그</b> " + html.escape(", ".join(metadata.get("tags") or [])),
    ]
    await message.reply_text("\n".join(details)[:4000], parse_mode="HTML")


async def _background(message, context, label: str, args: list[str], timeout: float, *, preview: bool) -> None:
    lock = _lock(context)
    if lock.locked():
        await message.reply_text("쇼츠 작업이 이미 돌고 있습니다. 끝나면 알려 드립니다.")
        return
    await lock.acquire()

    async def job() -> None:
        try:
            try:
                result = await _runner(context).call(args, timeout=timeout)
                if args == ["--complete"]:
                    await message.reply_text("검수 완료로 기록했습니다. YouTube에 업로드합니다.")
                    # 완료한 수정본을 고정한다. 업로드 단계는 검수 여부를 다시 확인한다.
                    target = result.get("target")
                    if not target:
                        raise ShortsError("검수한 수정본 경로가 없습니다. /shorts에서 상태를 확인하세요.")
                    result = await _runner(context).call(
                        ["--upload", target], timeout=SHORTS_RUN_TIMEOUT_SECONDS)
                if args and args[0] in {"--complete", "--upload", "--review-approve"}:
                    await message.reply_text(upload_text(result))
                    return
                status = result if args and args[0] == "--edit" else await _runner(context).call(
                    ["--status"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            except ShortsError as error:
                await message.reply_text(f"{label} 실패: {error}")
                return
            except Exception:
                logger.exception("[SHORTS] %s 실패", label)
                await message.reply_text(f"{label} 실패: 로그를 확인하세요.")
                return
        finally:
            lock.release()
        head = f"{label} 끝."
        if result.get("summary"):
            head += f"\n변경: {html.escape(str(result['summary']))}"
        if result.get("status") == "already_produced":
            head += "\n오늘 영상이 이미 있습니다. 다시 만들려면 /shorts run force"
        await message.reply_text(head + "\n\n" + status_text(status, busy=False), parse_mode="HTML",
                                 reply_markup=shorts_keyboard())
        if preview and status.get("video_path"):
            await _send_preview(message, status)

    try:
        if args and args[0] == "--edit" and "--review-token" in args:
            token = args[args.index("--review-token") + 1]
            await _runner(context).call(["--review-pause", token], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
        await message.reply_text(f"{label}을(를) 시작했습니다. 몇 분 걸립니다.")
    except BaseException:
        lock.release()
        raise
    tasks: set[asyncio.Task] = context.bot_data.setdefault("shorts_tasks", set())
    task = asyncio.create_task(job(), name="shorts-" + args[0] if args else "shorts-run")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _edit_prompt(message, context, token: str | None = None) -> None:
    # 입력창을 여는 순간 정지한다. 사용자가 문장을 쓰는 동안에도 시간이 흐른다.
    async with _lock(context):
        if token is None:
            status = await _runner(context).call(["--status"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            token = (status.get("approval") or {}).get("token")
        if token:
            await _runner(context).call(["--review-pause", token], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
        context.user_data["menu_input"] = "shorts_edit"
        context.user_data["shorts_review_token"] = token
    prefix = "이 원고의 자동 승인을 보류했습니다. " if token else ""
    await message.reply_text(prefix + "자료·내용을 어떻게 고칠지 한 번에 적어 보내세요.")


async def cmd_shorts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = list(getattr(context, "args", None) or [])
    command = args[0].lower() if args else ""
    runner = _runner(context)
    try:
        if command == "":
            status = await runner.call(["--status"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            await message.reply_text(status_text(status, busy=_lock(context).locked()),
                                     parse_mode="HTML", reply_markup=shorts_keyboard())
        elif command == "run":
            force = len(args) > 1 and args[1].lower() == "force"
            await _background(message, context, "쇼츠 제작", ["--force"] if force else [],
                              SHORTS_RUN_TIMEOUT_SECONDS, preview=True)
        elif command == "preview":
            status = await runner.call(["--status"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            await _send_preview(message, status)
        elif command == "edit_prompt":
            await _edit_prompt(message, context)
        elif command == "edit":
            token = args[1] if len(args) > 1 and _TOKEN.fullmatch(args[1]) else None
            instruction = " ".join(args[2:] if token else args[1:]).strip()
            if not instruction:
                await message.reply_text("수정할 내용을 적어 주세요. 예: /shorts edit 첫 멘트를 질문형으로")
                return
            await _background(message, context, "쇼츠 수정", ["--edit", instruction] + (["--review-token", token] if token else []),
                              SHORTS_EDIT_TIMEOUT_SECONDS, preview=True)
        elif command == "review":
            async with _lock(context):
                pending = await runner.call(["--review-pending"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
                if not pending.get("items"):
                    await message.reply_text("검토 대기 중인 원고가 없습니다.")
                for item in pending.get("items", []):
                    await send_scenario(message.reply_text, item)
                    if item.get("state") == "pending" and not item.get("delivered_at"):
                        await runner.call(["--review-ack", item["token"]], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
        elif command in {"done", "hold"}:
            token = args[1] if len(args) > 1 else None
            if token is None:
                status = await runner.call(["--status"], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
                token = (status.get("approval") or {}).get("token")
            if command == "hold":
                if not token:
                    await message.reply_text("보류할 원고가 없습니다. /shorts review에서 확인하세요.")
                    return
                async with _lock(context):
                    await runner.call(["--review-pause", token], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
                await message.reply_text("자동 승인을 보류했습니다. 수정하거나 /shorts done 검토번호로 승인하세요.")
            else:
                await _background(message, context, "검수 완료·YouTube 업로드",
                                  ["--review-approve", token] if token else ["--complete"],
                                  SHORTS_RUN_TIMEOUT_SECONDS, preview=False)
        elif command == "upload":
            await _background(message, context, "YouTube 업로드", ["--upload"],
                              SHORTS_RUN_TIMEOUT_SECONDS, preview=False)
        else:
            await message.reply_text(USAGE)
    except ShortsError as error:
        await message.reply_text(f"쇼츠: {error}")


async def review_callback(query, context, data: str) -> bool:
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "shr" or parts[1] not in {"a", "p", "e"}:
        return False
    action, token = parts[1:]
    if not _TOKEN.fullmatch(token):
        await query.message.reply_text("유효하지 않은 검토 번호입니다. /shorts review에서 확인하세요.")
        return True
    try:
        if action == "e":
            await _edit_prompt(query.message, context, token)
        elif action == "a":
            await _background(query.message, context, "원고 승인·YouTube 업로드",
                              ["--review-approve", token], SHORTS_RUN_TIMEOUT_SECONDS, preview=False)
        else:
            async with _lock(context):
                await _runner(context).call(["--review-pause", token], timeout=SHORTS_STATUS_TIMEOUT_SECONDS)
            await query.message.reply_text("이 원고의 자동 승인을 보류했습니다.")
    except ShortsError as error:
        await query.message.reply_text(f"쇼츠: {error}")
    return True
