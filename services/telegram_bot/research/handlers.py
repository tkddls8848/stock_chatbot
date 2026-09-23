"""텔레그램 관리 패널의 리서치 명령(`/research`).

텔레그램은 웹 서비스의 관리 패널이다. 여기서는 주제를 보고·바꾸고·비우고, 예약을
기다리지 않고 지금 한 번 돌린다. 분석 결과 전체와 근거는 웹의 /research 화면이
보여 준다 — 텔레그램에는 몇 줄만 남긴다. 관심종목 적용은 실행이 묻지 않고 하고,
바뀐 것이 있으면 `research/job.py`가 알림 한 통을 보낸다.
"""

import asyncio
import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.config import RESEARCH_SCHEDULE_HOUR, RESEARCH_SCHEDULE_MINUTE
from services.telegram_bot.llm.market_view import MarketViewError
from services.telegram_bot.research.job import is_research_running, run_research
from services.telegram_bot.research.state import MarketViewManager

logger = logging.getLogger(__name__)
# 패널에 보이는 직전 요약 길이. 전체는 웹에 있다.
_SUMMARY_PREVIEW_CHARS = 200
_USAGE = "/research — 주제 보기\n/research set 주제\n/research clear\n/research run — 지금 실행"


def _schedule_text() -> str:
    return f"매일 {RESEARCH_SCHEDULE_HOUR:02d}:{RESEARCH_SCHEDULE_MINUTE:02d}"


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    command = args[0].lower() if args else "show"
    manager: MarketViewManager = context.bot_data["market_view_manager"]

    if command == "show":
        await message.reply_text(_show_text(manager), parse_mode="HTML")
    elif command == "set":
        topic = " ".join(args[1:]).strip()
        if not topic:
            await message.reply_text("사용법: /research set 주제")
            return
        await asyncio.to_thread(manager.set_sight, topic)
        await message.reply_text(
            f"주제를 저장했습니다. 다음 실행({_schedule_text()})부터 적용됩니다.\n"
            "지금 돌리려면 /research run"
        )
    elif command == "clear":
        await asyncio.to_thread(manager.clear_sight)
        await message.reply_text("주제를 비웠습니다. 주제가 없으면 예약 리서치는 건너뜁니다.")
    elif command == "run":
        await _start_run(message, context)
    else:
        await message.reply_text(_USAGE)


def _show_text(manager: MarketViewManager) -> str:
    topic = manager.get_sight()
    lines = [
        "<b>🔎 리서치</b>",
        f"주제: {html.escape(topic) if topic else '없음 (예약 실행을 건너뜁니다)'}",
        f"예약: {_schedule_text()}",
    ]
    last = manager.get_last_result()
    if last:
        summary = str(last.get("summary") or "").strip()
        if len(summary) > _SUMMARY_PREVIEW_CHARS:
            summary = summary[:_SUMMARY_PREVIEW_CHARS] + "…"
        lines.append(f"마지막 실행: {html.escape(str(last.get('generated_at') or ''))}")
        if summary:
            lines.append(html.escape(summary))
    lines.append("전체 결과·근거는 웹 /research 에 있습니다.")
    lines.append("")
    lines.append(html.escape(_USAGE))
    return "\n".join(lines)


async def _start_run(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    manager: MarketViewManager = context.bot_data["market_view_manager"]
    if manager.get_sight() is None:
        await message.reply_text("주제가 없습니다. /research set 주제 로 먼저 저장하세요.")
        return
    if is_research_running(context.bot_data):
        await message.reply_text("리서치가 이미 실행 중입니다.")
        return
    await message.reply_text("리서치를 시작했습니다. 몇 분 걸립니다.")
    # 분석이 수 분 걸려 핸들러를 붙잡지 않는다. 끝나면 한 줄로 알린다.
    tasks: set[asyncio.Task] = context.bot_data.setdefault("research_tasks", set())
    task = asyncio.create_task(_run_and_report(message, context), name="research-analysis")
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _run_and_report(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        outcome = await run_research(context.application)
    except MarketViewError as error:
        await message.reply_text(f"리서치 실패: {error}")
        return
    except Exception:
        logger.exception("[RESEARCH] 수동 실행 실패")
        await message.reply_text("리서치 실패: 로그를 확인하세요.")
        return
    if outcome is None:
        await message.reply_text("리서치를 실행하지 않았습니다(주제 또는 최근 뉴스 없음).")
        return
    applied = outcome["applied"]
    if not (applied["add"] or applied["remove"]):
        await message.reply_text("리서치 완료 · 관심종목 변경 없음 · 결과는 웹 /research")
    # 바뀐 것이 있으면 run_research가 이미 알림을 보냈다.
