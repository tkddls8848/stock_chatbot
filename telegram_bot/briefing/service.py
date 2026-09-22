"""요청 시각에 맞는 장전/장중/장후 브리핑 생성·전송.

장전·장후는 스케줄러 cron도 호출하며, /briefing 명령과 메뉴는 현재 JST
세션을 자동 판정해 수동 실행(휴장일 무시)한다. LLM 코멘트 생성이 실패하면
데이터 전용 브리핑으로 대체한다.
"""

import html
import logging
from datetime import datetime

from telegram_bot.core.clock import now as clock_now

from telegram import Update
from telegram.ext import Application, ContextTypes

from telegram_bot.core.config import (
    BRIEFING_MARKET_CLOSE_HOUR,
    BRIEFING_MARKET_CLOSE_MINUTE,
    BRIEFING_MARKET_OPEN_HOUR,
    BRIEFING_MARKET_OPEN_MINUTE,
    BRIEFING_NEWS_MAX_ITEMS,
    BRIEFING_NEWS_MARKETS,
    TELEGRAM_CHAT_ID,
    TELEGRAM_MESSAGE_LIMIT,
)
from telegram_bot.core.menu_status import set_menu_button_text
from telegram_bot.core.telegram_html import truncate_html
from telegram_bot.core.workers import run_non_urgent, wait_for_urgent_idle
from telegram_bot.research.news import collect_global_market_news_items
from telegram_bot.state.news_log import aggregate_sentiment_by_code
from telegram_bot.stocks.quotes import format_sector_summary_text

logger = logging.getLogger(__name__)

_WEEKDAYS_KO = ["월", "화", "수", "목", "금", "토", "일"]


def select_briefing_kind(moment: datetime | None = None) -> str:
    """JST 요청 시각을 아시아 시장의 장전·장중·장후로 분류한다."""
    current = moment or clock_now()
    minute_of_day = current.hour * 60 + current.minute
    open_minute = BRIEFING_MARKET_OPEN_HOUR * 60 + BRIEFING_MARKET_OPEN_MINUTE
    close_minute = BRIEFING_MARKET_CLOSE_HOUR * 60 + BRIEFING_MARKET_CLOSE_MINUTE
    if minute_of_day < open_minute:
        return "morning"
    if minute_of_day < close_minute:
        return "intraday"
    return "evening"


def _today_header() -> str:
    now = clock_now()
    return f"{now.strftime('%Y-%m-%d')} ({_WEEKDAYS_KO[now.weekday()]})"


def _sentiment_marker(sentiment) -> str:
    if not isinstance(sentiment, (int, float)):
        return "⚪"
    if sentiment >= 0.15:
        return "🟢"
    if sentiment <= -0.15:
        return "🔴"
    return "⚪"


async def _is_holiday(app: Application) -> bool:
    calendar = app.bot_data.get("trade_calendar")
    if calendar is None:
        return False
    return not await run_non_urgent(calendar.is_trade_date)


async def _build_sector_summary_section(app: Application, include_fund_flow: bool) -> tuple[dict, str]:
    quote_service = app.bot_data.get("quote_service")
    wm = app.bot_data["watchlist_manager"]
    watchlist = await wm.get_all()
    if quote_service is None or not watchlist:
        return {}, ""
    context = await run_non_urgent(
        quote_service.build_sector_summary_context, watchlist, include_fund_flow
    )
    return context, format_sector_summary_text(context, watchlist)


async def _collect_briefing_news(app: Application) -> list[dict]:
    registry = app.bot_data.get("news_registry")
    try:
        return await collect_global_market_news_items(
            registry,
            max_items=BRIEFING_NEWS_MAX_ITEMS,
            markets=BRIEFING_NEWS_MARKETS,
        )
    except Exception as e:
        logger.error("[BRIEFING] 뉴스 수집 실패: %s", e)
        return []


def _format_news_section(news_items: list[dict], title: str) -> str:
    if not news_items:
        return ""
    lines = [f"<b>{title}</b>"]
    for item in news_items:
        headline = html.escape(str(item.get("title") or item.get("content") or "")[:80])
        source = html.escape(str(item.get("source") or ""))
        market = html.escape(str(item.get("market") or ""))
        marker = _sentiment_marker(item.get("sentiment"))
        market_label = f"[{market}] " if market else ""
        lines.append(f"{marker} {market_label}{headline} <i>({source})</i>")
    return "\n".join(lines)


async def _write_llm_comment(app: Application, payload: dict) -> str:
    writer = app.bot_data.get("briefing_writer")
    if writer is None or not writer.enabled:
        return ""
    await wait_for_urgent_idle("브리핑 LLM 코멘트")
    try:
        return await run_non_urgent(writer.write, payload)
    except Exception as e:
        logger.warning("[BRIEFING] LLM 코멘트 생성 실패, 데이터 전용으로 전송: %s", e)
        return ""


async def _deliver(app: Application, sections: list[str], label: str) -> None:
    """브리핑을 보낸다. 전송에 실패하면 예외를 그대로 올린다.

    여기서 로그만 남기고 정상 반환하면 `/briefing morning`이 한 글자도 보내지
    못한 채 "처리 완료"로 끝난다 — 운영자가 journalctl을 열기 전에는 실패를
    알 수 없다. 부를 사람이 없는 예약 실행만 스케줄러 경계에서 잡는다
    (`features/briefing/feature.py`).
    """
    await app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=truncate_html("\n\n".join(sections), TELEGRAM_MESSAGE_LIMIT),
        parse_mode="HTML",
    )
    logger.info("[BRIEFING] %s 전송 완료", label)


def _news_headlines_payload(news_items: list[dict]) -> list[dict]:
    return [
        {
            "title": str(item.get("title") or "")[:120],
            "source": str(item.get("source") or ""),
            "market": str(item.get("market") or ""),
            "sentiment": item.get("sentiment"),
        }
        for item in news_items
    ]


async def _build_sentiment_section(
    app: Application,
    watchlist: dict[str, str],
) -> tuple[dict, str]:
    news_log = app.bot_data.get("news_log")
    if news_log is None or not watchlist:
        return {}, ""

    entries = await news_log.snapshot(since_hours=24)
    sentiment_stats = aggregate_sentiment_by_code(entries, watchlist)
    if not sentiment_stats:
        return {}, ""

    lines = ["<b>관심종목 뉴스 감성(24시간)</b>"]
    for code, stats in sentiment_stats.items():
        name = html.escape(watchlist.get(code, code))
        avg = stats.get("avg_sentiment")
        marker = _sentiment_marker(avg)
        avg_part = f"{avg:+.2f}" if isinstance(avg, (int, float)) else "-"
        lines.append(f"{marker} {name} ({code}): {stats['count']}건, 평균 {avg_part}")
    return sentiment_stats, "\n".join(lines)


def _sentiment_payload(sentiment_stats: dict) -> dict:
    return {
        code: {
            "count": stats.get("count"),
            "avg_sentiment": stats.get("avg_sentiment"),
            "titles": stats.get("titles", []),
        }
        for code, stats in sentiment_stats.items()
    }


async def send_morning_briefing(app: Application, force: bool = False) -> None:
    if not force and await _is_holiday(app):
        logger.info("[BRIEFING] 휴장일이라 모닝 브리핑을 건너뜁니다.")
        return

    sector_summary_context, sector_summary_text = await _build_sector_summary_section(app, include_fund_flow=False)
    news_items = await _collect_briefing_news(app)
    market_view = app.bot_data["market_view_manager"].get_sight() or ""

    comment = await _write_llm_comment(
        app,
        {
            "kind": "morning",
            "market_view": market_view,
            "sector_summary_context": sector_summary_context,
            "news_headlines": _news_headlines_payload(news_items),
        },
    )

    sections = [f"<b>📅 모닝 브리핑</b> {_today_header()}"]
    if sector_summary_text:
        sections.append(sector_summary_text)
    news_section = _format_news_section(news_items, "밤사이 주요 뉴스")
    if news_section:
        sections.append(news_section)
    if comment:
        sections.append(f"<b>오늘 볼 포인트</b>\n{html.escape(comment)}")
    if len(sections) == 1:
        sections.append("표시할 데이터가 없습니다.")

    await _deliver(app, sections, "모닝 브리핑")


async def send_intraday_briefing(app: Application, force: bool = False) -> None:
    if not force and await _is_holiday(app):
        logger.info("[BRIEFING] 휴장일이라 장중 브리핑을 건너뜁니다.")
        return

    wm = app.bot_data["watchlist_manager"]
    watchlist = await wm.get_all()
    sector_summary_context, sector_summary_text = await _build_sector_summary_section(app, include_fund_flow=True)
    news_items = await _collect_briefing_news(app)
    sentiment_stats, sentiment_section = await _build_sentiment_section(app, watchlist)
    market_view = app.bot_data["market_view_manager"].get_sight() or ""

    comment = await _write_llm_comment(
        app,
        {
            "kind": "intraday",
            "market_view": market_view,
            "sector_summary_context": sector_summary_context,
            "news_headlines": _news_headlines_payload(news_items),
            "sentiment_stats": _sentiment_payload(sentiment_stats),
        },
    )

    sections = [f"<b>📈 장중 브리핑</b> {_today_header()}"]
    if sector_summary_text:
        sections.append(sector_summary_text)
    news_section = _format_news_section(news_items, "장중 주요 뉴스")
    if news_section:
        sections.append(news_section)
    if sentiment_section:
        sections.append(sentiment_section)
    if comment:
        sections.append(f"<b>장중 관찰 포인트</b>\n{html.escape(comment)}")
    if len(sections) == 1:
        sections.append("표시할 데이터가 없습니다.")

    await _deliver(app, sections, "장중 브리핑")


async def send_evening_briefing(app: Application, force: bool = False) -> None:
    if not force and await _is_holiday(app):
        logger.info("[BRIEFING] 휴장일이라 마감 브리핑을 건너뜁니다.")
        return

    wm = app.bot_data["watchlist_manager"]
    watchlist = await wm.get_all()
    sector_summary_context, sector_summary_text = await _build_sector_summary_section(app, include_fund_flow=True)
    market_view = app.bot_data["market_view_manager"].get_sight() or ""
    news_items = await _collect_briefing_news(app)

    sentiment_stats, sentiment_section = await _build_sentiment_section(app, watchlist)

    comment = await _write_llm_comment(
        app,
        {
            "kind": "evening",
            "market_view": market_view,
            "sector_summary_context": sector_summary_context,
            "news_headlines": _news_headlines_payload(news_items),
            "sentiment_stats": _sentiment_payload(sentiment_stats),
        },
    )

    sections = [f"<b>🌙 마감 브리핑</b> {_today_header()}"]
    if sector_summary_text:
        sections.append(sector_summary_text)
    news_section = _format_news_section(news_items, "마감 주요 뉴스")
    if news_section:
        sections.append(news_section)
    if sentiment_section:
        sections.append(sentiment_section)
    if comment:
        sections.append(f"<b>마감 코멘트</b>\n{html.escape(comment)}")
    if len(sections) == 1:
        sections.append("표시할 데이터가 없습니다.")

    await _deliver(app, sections, "마감 브리핑")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/briefing [morning|intraday|evening] — 기본은 요청 시각으로 자동 선택."""
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    command = args[0].lower() if args else select_briefing_kind()
    app = context.application
    actions = {
        "morning": ("모닝", send_morning_briefing),
        "intraday": ("장중", send_intraday_briefing),
        "evening": ("마감", send_evening_briefing),
    }
    selected = actions.get(command)
    if selected is None:
        await message.reply_text(
            "사용법:\n"
            "/briefing — 요청 시각에 맞춰 장전·장중·장후 자동 실행\n"
            "/briefing morning — 모닝 브리핑 즉시 실행\n"
            "/briefing intraday — 장중 브리핑 즉시 실행\n"
            "/briefing evening — 마감 브리핑 즉시 실행"
        )
        return

    query = getattr(update, "callback_query", None)
    callback_data = str(getattr(query, "data", ""))
    is_menu = callback_data in {"nav:briefing", f"nav:briefing:{command}"}
    label, action = selected
    if is_menu:
        await set_menu_button_text(message, callback_data, "◐ 생성 중")
    try:
        await action(app, force=True)
    finally:
        if is_menu:
            restored_label = "📰 브리핑" if callback_data == "nav:briefing" else label
            await set_menu_button_text(message, callback_data, restored_label)
