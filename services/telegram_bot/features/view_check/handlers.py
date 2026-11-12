"""텔레그램 `/view` — 내 시장 뷰를 컨센서스에 대조하고 포지션 아이디어를 받는다.

**운영자 개인용이다.** 특정 상품에 방향을 붙여 보이는 기능이라 공개 웹에 두지 않고, 허용된 채팅
(`ALLOWED_CHAT_IDS`)만 보는 이 방에 둔다(운영자 지시 2026-11-12).

- `/view 뷰 문장` — 검증을 시작한다. 모델 호출 셋이라 1~5분 걸리고, 끝나면 결과를 보낸다.
- `/view` — 최근 검증을 다시 보이고, 그때 보인 선택지의 지금 확률을 옆에 적는다(LLM 없음).
- `/view run` — 최근 뷰로 다시 검증한다.

결과는 최근 한 건만 둔다(`storage/bot/view_check/view_check.json`). 실패한 실행도 하루 횟수에 센다 —
실패도 모델을 불렀다.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.clock import today
from services.telegram_bot.core.config import (
    VIEW_CHECK_DRIFT_ALERT,
    VIEW_CHECK_MAX_DAILY,
    VIEW_CHECK_STATE_FILE,
    VIEW_CHECK_VIEW_CHARS,
)
from services.telegram_bot.core.storage import write_json_atomic
from services.telegram_bot.core.telegram_html import MARK_FAIL, MARK_NONE, MARK_OK, MARK_WARN, truncate_html
from services.telegram_bot.core.workers import ShutdownThreadPool
from services.telegram_bot.features.view_check import service
from services.telegram_bot.features.view_check.consensus import Consensus, ConsensusUnavailable
from services.telegram_bot.llm.view_check import ViewCheckError

logger = logging.getLogger(__name__)

USAGE = "뷰 문장 | run"
_HELP = ("/view 뷰 문장 — 컨센서스로 검증하고 포지션 아이디어 받기(1~5분)\n"
         "/view — 최근 검증과 그 뒤 컨센서스 이동\n"
         "/view run — 최근 뷰로 다시 검증\n"
         "예: /view 연준이 내년 상반기까지 금리를 내리지 못하고 장기 금리가 더 오른다. 유가는 안정된다.")
_NO_CLAIMS = "뷰에서 컨센서스로 확인할 예상을 찾지 못했습니다. 무엇이 어떻게 될지 문장으로 적어 보세요."
_STAGE = {"claims": "전제 나누기", "judge": "판정", "positions": "포지션"}
_VERDICT_MARK = {"supported": MARK_OK, "contradicted": MARK_FAIL, "mixed": MARK_WARN, "unpriced": MARK_NONE}
_STANCE_MARK = {"long": "📈", "reduce": "📉", "hedge": "🛡", "wait": "👀"}
_FOOTER = ("<i>참고용입니다. 컨센서스 확률은 참여자들의 기대일 뿐 결과를 보장하지 않고, AI 판정은 틀릴 수 있습니다. "
           "해설보다 옆의 질문·선택지·확률을 보고 판단하세요.</i>")
# 텔레그램 메시지 한 통의 상한은 4,096자다. 태그 몫을 남긴다.
_MESSAGE_CHARS = 3800
_EXECUTOR = ShutdownThreadPool(max_workers=1, thread_name_prefix="view-check")


def read_state() -> dict[str, Any]:
    """최근 결과와 오늘 쓴 횟수. 파일이 없거나 지금 형식이 아니면 빈 상태다."""
    try:
        state = json.loads(VIEW_CHECK_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) and state.get("format") == service.FORMAT else {}


def _used(state: dict[str, Any]) -> int:
    usage = state.get("usage") or {}
    return int(usage.get("count") or 0) if usage.get("day") == today().isoformat() else 0


def _write_state(report: dict[str, Any] | None, used: int) -> None:
    write_json_atomic(VIEW_CHECK_STATE_FILE, {
        "format": service.FORMAT, "report": report, "usage": {"day": today().isoformat(), "count": used}})


def _percent(probability: float) -> str:
    if 0 < probability < 0.01:
        return "1% 미만"
    if 0.99 < probability < 1:
        return "99% 초과"
    return f"{probability * 100:.0f}%"


def _option(option: dict[str, Any], latest: dict[str, float] | None) -> str:
    text = f"{html.escape(option['label'])} {_percent(option['probability'])}"
    value = (latest or {}).get(option["label"])
    if value is not None and round(value * 100) != round(option["probability"] * 100):
        text += f" → {_percent(value)} ({(value - option['probability']) * 100:+.0f}%p)"
    return f"<b>{text}</b>" if option.get("focus") else text


def _signal_lines(signal: dict[str, Any], current: dict[str, Any] | None) -> list[str]:
    """질문 하나: 우리말 요약, 원문 제목, 선택지와 확률. `current`가 있으면 검증 뒤 이동을 붙인다."""
    title = html.escape(signal["title"])
    ends = str(signal.get("end_date") or "")[:10]
    head = html.escape(signal["summary"]) if signal.get("summary") else title
    lines = [f"· {head}" + (f" (~{ends})" if ends else "")]
    if signal.get("summary"):
        lines.append(f"  <i>{title}</i>")
    latest = None
    if current is not None:
        latest = current.get(signal["id"])
        if latest is None:
            lines.append("  (지금 수집분에 없는 질문입니다 — 닫혔을 수 있습니다)")
    lines.append("  " + " · ".join(_option(option, latest) for option in signal["options"]))
    return lines


def _position_block(position: dict[str, Any]) -> str:
    ticker = position["code"].rsplit(":", 1)[-1]
    traits = " · ".join(part for part in (ticker, position["kind"], position.get("direction"), position.get("tracks"))
                        if part)
    lines = [f"{_STANCE_MARK[position['stance']]} <b>{html.escape(position['name'])}</b> ({html.escape(traits)})"
             f" — {service.STANCE_LABEL[position['stance']]}"]
    claims = "·".join(str(c) for c in position["claims"])
    lines.append(f"  전제 {claims} | {html.escape(position['reason'])}")
    if position.get("invalidation"):
        lines.append(f"  접을 조건: {html.escape(position['invalidation'])}")
    lines.extend(f"  ⚠️ {html.escape(caution)}" for caution in position["cautions"])
    if position["alternatives"]:
        others = ", ".join(
            html.escape(f"{other['name']}({' · '.join(filter(None, (other['code'].rsplit(':', 1)[-1], other['note'])))})")
            for other in position["alternatives"])
        lines.append(f"  같은 성격: {others}")
    return "\n".join(lines)


def render(report: dict[str, Any], *, used: int, current: dict[str, Any] | None = None) -> list[str]:
    """결과를 텔레그램 메시지들로. `current`(`service.drift`)가 있으면 검증 뒤 이동을 함께 적는다."""
    stamp = str(report.get("generated_at") or "").replace("T", " ")[5:16].replace("-", "/")
    blocks = [f"<b>🧭 뷰 검증</b> · {stamp}\n<b>내 뷰</b>\n{html.escape(report['view'])}"]
    if current is not None and service.moved(report, current):
        blocks.append(f"⚠️ 근거로 삼은 컨센서스가 {VIEW_CHECK_DRIFT_ALERT * 100:.0f}%p 넘게 움직였습니다. "
                      "/view run 으로 다시 검증하세요.")
    if report.get("refined_view"):
        blocks.append(f"<b>다듬은 뷰</b>\n{html.escape(report['refined_view'])}")
    blocks.append("<b>전제별 판정</b>")
    for claim in report["claims"]:
        lines = [f"{_VERDICT_MARK[claim['verdict']]} <b>{claim['c']}. {html.escape(claim['claim'])}</b>"
                 f" — {service.VERDICT_LABEL[claim['verdict']]}"]
        if claim.get("note"):
            lines.append(html.escape(claim["note"]))
        for signal in claim["signals"]:
            lines.extend(_signal_lines(signal, current))
        blocks.append("\n".join(lines))
    if report["blind_spots"]:
        blocks.append("<b>뷰가 놓친 신호</b>")
        for spot in report["blind_spots"]:
            blocks.append("\n".join([html.escape(spot["note"]), *_signal_lines(spot["signal"], current)]))
    blocks.append("<b>포지션 아이디어</b>")
    if report["positions"]:
        blocks.extend(_position_block(position) for position in report["positions"])
    else:
        blocks.append({
            "failed": "포지션 아이디어를 받지 못했습니다. /view run 으로 다시 실행하세요.",
            "skipped": "컨센서스로 확인된 전제가 없어 포지션 아이디어를 내지 않았습니다.",
        }.get(report["positions_status"], "지금 뷰로 낼 만한 포지션 아이디어가 없습니다."))
    cost = f"이번 검증 약 {report['neurons']:,} Neurons · " if report.get("neurons") else ""
    blocks.append(f"{_FOOTER}\n{cost}오늘 {used}/{VIEW_CHECK_MAX_DAILY}회")

    messages: list[str] = []
    for block in blocks:
        block = truncate_html(block, _MESSAGE_CHARS)
        if messages and len(messages[-1]) + len(block) + 2 <= _MESSAGE_CHARS:
            messages[-1] += "\n\n" + block
        else:
            messages.append(block)
    return messages


async def _send(message, texts: list[str]) -> None:
    for text in texts:
        await message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def cmd_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    state = await asyncio.to_thread(read_state)
    report = state.get("report")
    if not args:
        if not report:
            await message.reply_text(_HELP)
            return
        # 검증이 도는 중에도 바로 답하게 실행기를 따로 쓴다(검증 실행기는 한 줄짜리다).
        current = await asyncio.to_thread(service.drift, Consensus(), report)
        await _send(message, render(report, used=_used(state), current=current))
        return
    if len(args) == 1 and args[0].lower() in ("help", "도움말"):
        await message.reply_text(_HELP)
        return
    if len(args) == 1 and args[0].lower() == "run":
        if not report:
            await message.reply_text("다시 검증할 뷰가 없습니다.\n" + _HELP)
            return
        view = report["view"]
    else:
        view = " ".join(args)
    low, high = VIEW_CHECK_VIEW_CHARS
    if not low <= len(view) <= high:
        await message.reply_text(f"뷰는 {low}~{high}자로 적어 주세요(지금 {len(view)}자).\n" + _HELP)
        return
    task = context.bot_data.get("view_check_task")
    if task is not None and not task.done():
        await message.reply_text("뷰 검증이 이미 진행 중입니다. 끝나면 결과를 보냅니다.")
        return
    used = _used(state)
    if used >= VIEW_CHECK_MAX_DAILY:
        await message.reply_text(f"뷰 검증은 하루 {VIEW_CHECK_MAX_DAILY}회까지입니다. 내일 다시 실행하세요.")
        return
    # 시작할 때 센다. 실패한 실행도 모델을 불렀다.
    await asyncio.to_thread(_write_state, report, used + 1)
    await message.reply_text(f"뷰 검증을 시작했습니다. 1~5분 걸립니다. (오늘 {used + 1}/{VIEW_CHECK_MAX_DAILY}회)")
    # 모델 호출 셋이 수 분 걸려 핸들러를 붙잡지 않는다.
    context.bot_data["view_check_task"] = asyncio.create_task(
        _run_and_report(message, context, view, used + 1), name="view-check")


async def _run_and_report(message, context: ContextTypes.DEFAULT_TYPE, view: str, used: int) -> None:
    try:
        watchlist = await context.bot_data["watchlist_manager"].get_all()
        report = await asyncio.get_running_loop().run_in_executor(
            _EXECUTOR, service.check, view, watchlist, Consensus(), context.bot_data["view_checker"])
    except ViewCheckError as error:
        logger.warning("[VIEW_CHECK] 검증 실패(%s): %s", error.stage, error)
        # 전제 단계가 두 번 다 반려됐으면 모델 장애가 아니라 뷰에서 나눌 예상을 찾지 못한 것이다.
        await message.reply_text(_NO_CLAIMS if error.stage == "claims" and error.rejected else
                                 f"뷰 검증 실패({_STAGE[error.stage]} 단계): 결과를 받지 못했습니다. /view run 으로 다시 실행하세요.")
        return
    except ConsensusUnavailable as error:
        logger.warning("[VIEW_CHECK] 컨센서스를 읽지 못했다: %s", error)
        await message.reply_text("뷰 검증 실패: 컨센서스 자료를 읽지 못했습니다. /web 으로 웹 상태를 확인하세요.")
        return
    except Exception:
        logger.exception("[VIEW_CHECK] 실행 오류")
        await message.reply_text("뷰 검증 실패: 로그를 확인하세요.")
        return
    await asyncio.to_thread(_write_state, report, used)
    logger.info("[VIEW_CHECK] 검증 완료: 전제 %d, 포지션 %d(%s), neurons=%s",
                len(report["claims"]), len(report["positions"]), report["positions_status"], report["neurons"])
    await _send(message, render(report, used=used))
