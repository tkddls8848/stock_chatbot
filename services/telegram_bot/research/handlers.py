import asyncio
import html
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from services.telegram_bot.core.clock import now

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.config import RESEARCH_DISCOVERY_RESERVED_SLOTS, RESEARCH_MAX_CANDIDATES, TELEGRAM_MESSAGE_LIMIT
from services.telegram_bot.core.menu_status import set_menu_button_text
from services.telegram_bot.core.workers import burst_job, run_non_urgent, wait_for_urgent_idle
from services.telegram_bot.llm.market_view import MarketViewError
from services.telegram_bot.news.utils import chunk_message_items
from services.telegram_bot.research.candidates import build_research_candidate_universe
from services.telegram_bot.research.discovery import collect_extra_candidates
from services.telegram_bot.research.results import (
    collect_actions,
    format_result_sections,
)
from services.telegram_bot.research.state import MarketViewManager
from services.telegram_bot.stocks import StockDatabase
from services.telegram_bot.watchlist.events import record_watchlist_event

logger = logging.getLogger(__name__)
_RESEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
_SECTION_SEPARATOR = "\n\n"
# 결과 메시지에 나열할 항목 상한. 프롬프트가 정한 개수보다 넉넉히 잡아,
# 모델이 상한까지 답했을 때 화면에서 다시 잘리지 않게 한다.


def _log_research_task_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.info("[RESEARCH] background analysis cancelled")
    except Exception:
        logger.exception("[RESEARCH] background analysis failed")


def build_research_result_keyboard(uid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("적용", callback_data=f"research_apply:{uid}"),
                InlineKeyboardButton("취소", callback_data=f"research_cancel:{uid}"),
            ],
            [InlineKeyboardButton("🏠 처음", callback_data="nav:home")],
        ]
    )


def build_research_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏠 처음", callback_data="nav:home")]]
    )


def _is_menu_research_run(update: Update) -> bool:
    query = getattr(update, "callback_query", None)
    return str(getattr(query, "data", "")) == "nav:research:run"


async def _show_research_phase(message, icon: str, label: str) -> None:
    try:
        await set_menu_button_text(
            message,
            "nav:research:run",
            f"{icon} {label}",
        )
    except Exception:
        logger.warning("[RESEARCH] 진행 상태 메뉴 갱신 실패", exc_info=True)


async def _deliver_research_text(
    request_message,
    menu_message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    with_default_keyboard: bool = True,
) -> None:
    if menu_message is not None:
        markup = reply_markup
        if markup is None and with_default_keyboard:
            markup = build_research_done_keyboard()
        await menu_message.edit_text(
            text,
            parse_mode=parse_mode,
            reply_markup=markup,
        )
        return
    await request_message.reply_text(
        text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def _deliver_research_sections(
    request_message,
    menu_message,
    sections: list[str],
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """섹션을 텔레그램 길이 상한에 맞춰 여러 메시지로 나눠 보낸다.

    심층 분석 결과는 한 메시지에 들어가지 않는다. 통째로 잘라내면 뒤쪽의
    리스크·반론이 먼저 사라져 정작 읽어야 할 내용이 없어지므로, 섹션 경계
    에서 나눈다(각 섹션은 태그가 닫힌 HTML이라 나눠도 서식이 깨지지 않는다).
    첫 메시지는 메뉴 자리를 대체하고 버튼은 마지막 메시지에만 붙인다.
    """
    chunks = chunk_message_items(
        sections,
        text_getter=lambda section: section,
        max_body_length=TELEGRAM_MESSAGE_LIMIT,
        separator=_SECTION_SEPARATOR,
    )
    # 홈 버튼은 메뉴에서 실행했을 때만 붙는다(_deliver_research_text와 같은
    # 규칙). 명령으로 실행한 경우에는 버튼 없이 본문만 보낸다.
    from_menu = menu_message is not None
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        text = _SECTION_SEPARATOR.join(chunk)
        if index == 0:
            await _deliver_research_text(
                request_message,
                menu_message,
                text,
                parse_mode="HTML",
                reply_markup=reply_markup if is_last else None,
                with_default_keyboard=is_last,
            )
            continue
        markup = None
        if is_last:
            markup = reply_markup
            if markup is None and from_menu:
                markup = build_research_done_keyboard()
        await request_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] command update has no effective message: %s", update)
        return

    args = context.args or []
    if not args:
        await _handle_research_show(update, context)
        return

    command = args[0].lower()
    if command == "show":
        await _handle_research_show(update, context)
    elif command == "set":
        await _handle_research_set(update, context, " ".join(args[1:]).strip())
    elif command == "run":
        mvm: MarketViewManager = context.bot_data["market_view_manager"]
        market_view = mvm.get_sight()
        if not market_view:
            await message.reply_text(
                "저장된 리서치 주제가 없습니다.\n/research set 리서치주제 으로 먼저 저장하세요."
            )
            return
        await _handle_research_run(update, context, market_view)
    elif command == "clear":
        await _handle_research_clear(update, context)
    else:
        await _handle_research_run(update, context, " ".join(args).strip(), temporary=True)


async def _handle_research_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] show update has no effective message: %s", update)
        return

    mvm: MarketViewManager = context.bot_data["market_view_manager"]
    view = mvm.get_sight()
    last_result = mvm.get_last_result()

    view_text = html.escape(view) if view else "저장된 리서치 주제 없음"
    header = (
        "<b>리서치 주제</b>\n"
        f"{view_text}\n\n"
        "<b>명령</b>\n"
        "/research show\n"
        "/research set 리서치주제\n"
        "/research run\n"
        "/research clear"
    )

    if not last_result:
        await message.reply_text(
            f"{header}\n\n<b>최근 분석</b>\n- 최근 분석 없음",
            parse_mode="HTML",
        )
        return

    # 실행 직후와 같은 화면을 다시 그린다. 요약만 보여 주면 근거·리스크·반론이
    # 사라져 재확인이 안 되므로 같은 포매터를 그대로 쓴다.
    wm = context.bot_data["watchlist_manager"]
    stock_db: StockDatabase = context.bot_data["stock_db"]
    watchlist = await wm.get_all()
    pending = collect_actions(last_result, watchlist, stock_db)
    generated_at = html.escape(str(last_result.get("generated_at") or ""))
    sections = format_result_sections(
        last_result,
        pending,
        int(last_result.get("news_count") or 0),
        int(last_result.get("candidate_count") or 0),
    )
    await _deliver_research_sections(
        message,
        None,
        [header, f"<b>마지막 실행</b>\n{generated_at}", *sections],
    )


async def _handle_research_set(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    market_view: str,
) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] set update has no effective message: %s", update)
        return

    if not market_view:
        await message.reply_text("사용법: /research set 리서치주제")
        return

    mvm: MarketViewManager = context.bot_data["market_view_manager"]
    await asyncio.to_thread(mvm.set_sight, market_view)
    await message.reply_text(
        "리서치 주제를 저장했습니다.\n/research run 으로 분석을 실행하세요."
    )


async def _handle_research_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] clear update has no effective message: %s", update)
        return

    mvm: MarketViewManager = context.bot_data["market_view_manager"]
    await asyncio.to_thread(mvm.clear_sight)
    context.bot_data["research_pending"] = {}
    await message.reply_text("리서치 주제를 삭제했습니다. 워치리스트는 변경하지 않았습니다.")


async def _handle_research_run(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    market_view: str,
    temporary: bool = False,
) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] run update has no effective message: %s", update)
        return
    if not market_view:
        await message.reply_text("사용법: /research set 리서치주제")
        return

    tasks: set[asyncio.Task] = context.bot_data.setdefault("research_tasks", set())
    active_tasks = {task for task in tasks if not task.done()}
    context.bot_data["research_tasks"] = active_tasks
    menu_message = message if _is_menu_research_run(update) else None
    if active_tasks:
        if menu_message is not None:
            await _show_research_phase(
                menu_message,
                "◉",
                "이미 분석 중입니다",
            )
        else:
            await message.reply_text(
                "이미 리서치 분석이 실행 중입니다. 완료 후 다시 요청해 주세요."
            )
        return

    if menu_message is not None:
        await _show_research_phase(menu_message, "◐", "뉴스 수집 중")
    task = asyncio.create_task(
        _run_research_job(
            update,
            context,
            market_view,
            temporary,
            menu_message=menu_message,
        ),
        name="research-analysis",
    )
    active_tasks.add(task)
    task.add_done_callback(active_tasks.discard)
    task.add_done_callback(_log_research_task_error)


@burst_job("리서치")
async def _run_research_job(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    market_view: str,
    temporary: bool = False,
    menu_message=None,
) -> None:
    message = update.effective_message
    if message is None:
        logger.warning("[RESEARCH] run update has no effective message: %s", update)
        return

    if not market_view:
        await message.reply_text("사용법: /research set 리서치주제")
        return

    wm = context.bot_data["watchlist_manager"]
    stock_db: StockDatabase = context.bot_data["stock_db"]
    analyzer = context.bot_data["market_view_analyzer"]
    mvm: MarketViewManager = context.bot_data["market_view_manager"]
    collect_global_news = context.bot_data["research_news_collector"]

    watchlist = await wm.get_all()
    news_items = await collect_global_news()
    if not news_items:
        await _deliver_research_text(
            message,
            menu_message,
            "최근 전체 시장 뉴스가 없어 분석을 실행하지 않았습니다.",
        )
        return
    if menu_message is not None:
        await _show_research_phase(menu_message, "◓", "후보 구성 중")
    candidate_universe = build_research_candidate_universe(
        stock_db,
        watchlist,
        news_items,
        max_candidates=RESEARCH_MAX_CANDIDATES,
    )

    # 시장별 발굴 후보(중화권 섹터, 미국 스크리너, 한국 등락률)를 병합한다.
    quote_service = context.bot_data.get("quote_service")
    try:
        extra_candidates = await run_non_urgent(
            collect_extra_candidates, quote_service, stock_db, watchlist
        )
        existing_codes = {c["code"] for c in candidate_universe}
        new_candidates = [
            c for c in extra_candidates if c["code"] not in existing_codes
        ]
        if new_candidates:
            reserved = min(RESEARCH_DISCOVERY_RESERVED_SLOTS, len(new_candidates))
            kept = max(0, RESEARCH_MAX_CANDIDATES - reserved)
            candidate_universe = (candidate_universe[:kept] + new_candidates)[
                :RESEARCH_MAX_CANDIDATES
            ]
    except Exception as e:
        logger.warning("[RESEARCH] 추가 후보 수집 실패: %s", e)

    # 요약 스냅샷과 직전 분석 이력을 분석 입력에 주입한다(각각 최선 노력).
    sector_summary_context = None
    if quote_service is not None:
        try:
            sector_summary_context = await run_non_urgent(
                quote_service.build_sector_summary_context, watchlist
            )
        except Exception as e:
            logger.warning("[RESEARCH] 요약 컨텍스트 수집 실패: %s", e)
    previous_analyses = mvm.get_history_summaries()

    # 분석은 중간에 양보할 수 없는 단일 LLM 호출이므로, 뉴스 주기가 도는
    # 동안에는 시작을 미뤄 번역이 뒤에서 대기하지 않게 한다.
    await wait_for_urgent_idle("리서치 분석")

    if menu_message is not None:
        await _show_research_phase(menu_message, "◑", "AI 분석 중")
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            _RESEARCH_EXECUTOR,
            analyzer.analyze,
            market_view,
            watchlist,
            news_items,
            candidate_universe,
            sector_summary_context,
            previous_analyses,
        )
    except MarketViewError as e:
        logger.error("[RESEARCH] analysis failed: %s", e)
        await _deliver_research_text(
            message,
            menu_message,
            f"분석 실패: {html.escape(str(e))}",
            parse_mode="HTML",
        )
        return
    except Exception as e:
        logger.error("[RESEARCH] analysis error: %s", e)
        await _deliver_research_text(
            message,
            menu_message,
            f"분석 실패: {html.escape(str(e))}",
            parse_mode="HTML",
        )
        return

    pending = collect_actions(result, watchlist, stock_db)
    if not temporary:
        await run_non_urgent(
            mvm.save_result,
            result,
            news_count=len(news_items),
            candidate_count=len(candidate_universe),
        )
        try:
            from services.web.export import publish_research

            public_result = {
                **result,
                "news_count": len(news_items),
                "candidate_count": len(candidate_universe),
            }
            await run_non_urgent(
                publish_research,
                market_view,
                public_result,
                mvm.get_history_summaries(),
            )
        except Exception:
            # 공개용 사본 실패가 리서치 결과와 텔레그램 전달을 막으면 안 된다.
            logger.warning("[WEBPUB] 리서치 산출물 저장 실패", exc_info=True)

    sections = format_result_sections(
        result,
        pending,
        len(news_items),
        len(candidate_universe),
        temporary,
    )
    has_changes = bool(pending["add"] or pending["remove"])
    if not has_changes:
        await _deliver_research_sections(
            message,
            menu_message,
            sections + ["변경 적용 후보가 없습니다."],
        )
        return

    uid = uuid.uuid4().hex[:8]
    context.bot_data.setdefault("research_pending", {})[uid] = {
        "created_at": now().isoformat(timespec="seconds"),
        "market_view": market_view,
        "add": pending["add"],
        "remove": pending["remove"],
        "summary": result.get("summary") or "",
    }
    await _deliver_research_sections(
        message,
        menu_message,
        sections,
        reply_markup=build_research_result_keyboard(uid),
    )


async def handle_research_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> bool:
    if data.startswith("research_apply:"):
        await _handle_research_apply(query, context, data.split(":", 1)[1])
        return True
    if data.startswith("research_cancel:"):
        await _handle_research_cancel(query, context, data.split(":", 1)[1])
        return True
    return False


async def _handle_research_apply(query, context: ContextTypes.DEFAULT_TYPE, uid: str) -> None:
    pending_map = context.bot_data.setdefault("research_pending", {})
    pending = pending_map.pop(uid, None)
    if not pending:
        await query.message.edit_text("요청을 찾을 수 없습니다. /research run을 다시 실행하세요.")
        return

    wm = context.bot_data["watchlist_manager"]
    watchlist = await wm.get_all()
    added: list[str] = []
    removed: list[str] = []
    skipped: list[str] = []

    for item in pending.get("remove", []):
        code = str(item.get("code") or "")
        if code in watchlist:
            name = await wm.remove(code)
            removed.append(f"{name or item.get('name') or code} ({code})")
            watchlist.pop(code, None)
            await record_watchlist_event(
                context.bot_data,
                "remove",
                code,
                name or str(item.get("name") or code),
                reason=str(item.get("reason") or "리서치 적용"),
            )
        else:
            skipped.append(f"{item.get('name') or code} ({code})")

    for item in pending.get("add", []):
        code = str(item.get("code") or "")
        if code in watchlist:
            skipped.append(f"{watchlist[code]} ({code})")
            continue
        name = str(item.get("name") or code)
        await wm.add(code, name)
        added.append(f"{name} ({code})")
        watchlist[code] = name
        await record_watchlist_event(
            context.bot_data,
            "add",
            code,
            name,
            reason=str(item.get("reason") or "리서치 적용"),
        )

    text = (
        "<b>리서치 변경 적용 완료</b>\n\n"
        f"<b>추가</b>\n{chr(10).join('- ' + html.escape(x) for x in added) or '- 없음'}\n\n"
        f"<b>삭제</b>\n{chr(10).join('- ' + html.escape(x) for x in removed) or '- 없음'}"
    )
    if skipped:
        text += f"\n\n<b>건너뜀</b>\n{chr(10).join('- ' + html.escape(x) for x in skipped)}"
    await query.message.edit_text(text, parse_mode="HTML")


async def _handle_research_cancel(query, context: ContextTypes.DEFAULT_TYPE, uid: str) -> None:
    context.bot_data.setdefault("research_pending", {}).pop(uid, None)
    await query.message.edit_text("리서치 변경 적용을 취소했습니다.")
