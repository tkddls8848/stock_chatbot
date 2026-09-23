"""리서치 실행: 저장된 주제로 분석하고, 결과를 저장·게시하고, 관심종목에 바로 적용한다.

정해진 시각(`RESEARCH_SCHEDULE_*`)에 한 번 돌고, 텔레그램 관리 패널의 "지금 실행"도
같은 함수를 부른다. 주제는 텔레그램에서 바꾸고(`MarketViewManager.sight`), 결과는
봇 상태와 웹 산출물(`data/webpub/research.json`)에 같은 한 벌로 남는다 — 브리핑과
웹 화면이 같은 결과를 읽는다.

**관심종목 추가·삭제는 묻지 않고 적용한다.** 예전에는 결과마다 텔레그램 적용 버튼을
눌러야 했다. 지금은 적용하고, 실제로 바뀐 것이 있을 때만 짧은 알림 한 통을 보낸다 —
근거와 전체 결과는 웹에서 본다. 한 번에 바뀌는 수는 분석기의 상한
(`RESEARCH_MAX_NEW_ACTIONS`)과 `collect_actions`의 걸러내기가 제한한다.
"""

from __future__ import annotations

import asyncio
import html
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from telegram.ext import Application

from services.telegram_bot.core.config import (
    RESEARCH_DISCOVERY_RESERVED_SLOTS,
    RESEARCH_MAX_CANDIDATES,
    TELEGRAM_CHAT_ID,
)
from services.telegram_bot.core.workers import burst_job, run_non_urgent, wait_for_urgent_idle
from services.telegram_bot.research.candidates import build_research_candidate_universe
from services.telegram_bot.research.discovery import collect_extra_candidates
from services.telegram_bot.research.results import collect_actions
from services.telegram_bot.research.state import MarketViewManager
from services.telegram_bot.watchlist.events import record_watchlist_event

logger = logging.getLogger(__name__)
_RESEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
# 알림에 적는 주제 길이. 주제 전체는 웹에 있다.
_TOPIC_PREVIEW_CHARS = 40


async def _build_candidates(bot_data: dict, watchlist: dict[str, str], news_items: list) -> list:
    stock_db = bot_data["stock_db"]
    candidates = build_research_candidate_universe(
        stock_db, watchlist, news_items, max_candidates=RESEARCH_MAX_CANDIDATES
    )
    # 시장별 발굴 후보(중화권 섹터, 미국 스크리너, 한국 등락률)를 병합한다.
    try:
        extra = await run_non_urgent(
            collect_extra_candidates, bot_data.get("quote_service"), stock_db, watchlist
        )
    except Exception as error:
        logger.warning("[RESEARCH] 추가 후보 수집 실패: %s", error)
        return candidates
    existing = {candidate["code"] for candidate in candidates}
    new = [candidate for candidate in extra if candidate["code"] not in existing]
    if not new:
        return candidates
    reserved = min(RESEARCH_DISCOVERY_RESERVED_SLOTS, len(new))
    kept = max(0, RESEARCH_MAX_CANDIDATES - reserved)
    return (candidates[:kept] + new)[:RESEARCH_MAX_CANDIDATES]


async def apply_actions(bot_data: dict, pending: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    """분석이 고른 추가·삭제를 관심종목에 적용하고 실제로 바뀐 것만 돌려준다."""
    manager = bot_data["watchlist_manager"]
    watchlist = await manager.get_all()
    applied: dict[str, list[str]] = {"add": [], "remove": []}
    for item in pending.get("remove", []):
        code = str(item.get("code") or "")
        if code not in watchlist:
            continue
        name = await manager.remove(code) or str(item.get("name") or code)
        watchlist.pop(code, None)
        applied["remove"].append(f"{name}({code})")
        await record_watchlist_event(
            bot_data, "remove", code, name, reason=str(item.get("reason") or "예약 리서치")
        )
    for item in pending.get("add", []):
        code = str(item.get("code") or "")
        if not code or code in watchlist:
            continue
        name = str(item.get("name") or code)
        await manager.add(code, name)
        watchlist[code] = name
        applied["add"].append(f"{name}({code})")
        await record_watchlist_event(
            bot_data, "add", code, name, reason=str(item.get("reason") or "예약 리서치")
        )
    return applied


def is_research_running(bot_data: dict) -> bool:
    lock = bot_data.get("research_lock")
    return lock is not None and lock.locked()


def format_applied_notice(topic: str, applied: dict[str, list[str]]) -> str:
    """바뀐 관심종목만 적는 짧은 알림. 근거·전체 결과는 웹에 있다."""
    preview = topic if len(topic) <= _TOPIC_PREVIEW_CHARS else topic[:_TOPIC_PREVIEW_CHARS] + "…"
    lines = [f"<b>🔎 리서치 반영</b> · {html.escape(preview)}"]
    if applied["add"]:
        lines.append("➕ " + ", ".join(html.escape(item) for item in applied["add"]))
    if applied["remove"]:
        lines.append("➖ " + ", ".join(html.escape(item) for item in applied["remove"]))
    return "\n".join(lines)


async def run_research(app: Application) -> dict[str, Any] | None:
    """저장된 주제로 한 번 분석한다. 주제가 없거나 재료가 없으면 None.

    예약 실행과 패널의 "지금 실행"이 겹치면 뒤에 온 쪽은 앞의 것이 끝나길
    기다린다 — 두 분석이 같은 history를 읽고 서로의 결과를 덮지 않게 한다.
    """
    lock = app.bot_data.setdefault("research_lock", asyncio.Lock())
    async with lock:
        return await _run_research(app)


@burst_job("리서치")
async def _run_research(app: Application) -> dict[str, Any] | None:
    bot_data = app.bot_data
    manager: MarketViewManager = bot_data["market_view_manager"]
    topic = manager.get_sight()
    if topic is None:
        logger.info("[RESEARCH] 주제가 없어 리서치를 건너뛴다.")
        return None

    watchlist = await bot_data["watchlist_manager"].get_all()
    news_items = await bot_data["research_news_collector"]()
    if not news_items:
        logger.warning("[RESEARCH] 최근 시장 뉴스가 없어 분석하지 않았다.")
        return None
    candidates = await _build_candidates(bot_data, watchlist, news_items)

    quote_service = bot_data.get("quote_service")
    sector_summary_context = None
    if quote_service is not None:
        try:
            sector_summary_context = await run_non_urgent(
                quote_service.build_sector_summary_context, watchlist
            )
        except Exception as error:
            logger.warning("[RESEARCH] 요약 컨텍스트 수집 실패: %s", error)

    # 분석은 중간에 양보할 수 없는 단일 LLM 호출이라, 뉴스 주기가 도는 동안에는
    # 시작을 미뤄 번역이 뒤에서 대기하지 않게 한다.
    await wait_for_urgent_idle("리서치 분석")
    result = await asyncio.get_running_loop().run_in_executor(
        _RESEARCH_EXECUTOR,
        bot_data["market_view_analyzer"].analyze,
        topic,
        watchlist,
        news_items,
        candidates,
        sector_summary_context,
        manager.get_history_summaries(),
    )

    await run_non_urgent(
        manager.save_result, result, news_count=len(news_items), candidate_count=len(candidates)
    )
    try:
        from services.web.export import publish_research

        await run_non_urgent(
            publish_research,
            topic,
            {**result, "news_count": len(news_items), "candidate_count": len(candidates)},
            manager.get_history_summaries(),
        )
    except Exception:
        # 공개용 사본 실패가 봇 상태 저장과 관심종목 적용을 막으면 안 된다.
        logger.warning("[WEBPUB] 리서치 산출물 저장 실패", exc_info=True)

    applied = await apply_actions(bot_data, collect_actions(result, watchlist, bot_data["stock_db"]))
    if applied["add"] or applied["remove"]:
        await app.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=format_applied_notice(topic, applied),
            parse_mode="HTML",
        )
    logger.info(
        "[RESEARCH] 리서치 완료: 뉴스 %d건, 후보 %d개, 추가 %d, 삭제 %d",
        len(news_items), len(candidates), len(applied["add"]), len(applied["remove"]),
    )
    return {"result": result, "applied": applied}
