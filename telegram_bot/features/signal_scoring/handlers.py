"""종목 감성 뷰 명령 구현."""

import html
from typing import Any, Mapping

from telegram import Message, Update
from telegram.ext import ContextTypes

from telegram_bot.core.config import VIEW_LOOKBACK_DAYS
from telegram_bot.news.utils import normalize_stock_code
from telegram_bot.state import PredictionLog, aggregate_stock_views
from telegram_bot.stocks import StockDatabase

_VERDICT_LABELS = {
    "up": "🟢 상승 우위",
    "down": "🔴 하락 우위",
    "neutral": "⚪ 중립",
}


def _view_footer() -> str:
    return (
        f"\n최근 {VIEW_LOOKBACK_DAYS}일 뉴스 감성 집계 기반 참고 뷰이며 "
        "예측·투자 조언이 아닙니다."
    )


async def _send_stock_sentiment_view(
    message: Message,
    bot_data: Mapping[str, Any],
    args: list[str],
) -> None:
    """명령과 인라인 버튼이 공유하는 종목 감성 응답 경계."""
    prediction_log: PredictionLog | None = bot_data.get("prediction_log")
    if prediction_log is None:
        await message.reply_text("감성 신호 기록이 비활성화되어 있습니다.")
        return

    watchlist = await bot_data["watchlist_manager"].get_all()
    entries = await prediction_log.snapshot(VIEW_LOOKBACK_DAYS)

    # /view 종목코드 — 단일 종목 상세(관심종목이 아니어도 조회 가능)
    if args:
        code = normalize_stock_code(args[0])
        stock_db: StockDatabase = bot_data["stock_db"]
        name = watchlist.get(code) or stock_db.get_display_name(code) or code
        view = aggregate_stock_views(entries, {code: name}).get(code)
        if view is None:
            await message.reply_text(
                f"{name} ({code}): 최근 {VIEW_LOOKBACK_DAYS}일 감성 신호가 없습니다."
            )
            return
        lines = [
            f"<b>{html.escape(name)} ({code}) 감성 뷰</b>",
            f"{_VERDICT_LABELS[view['verdict']]} · "
            f"평균 감성 {view['avg_sentiment']:+.2f} · 뉴스 {view['count']}건",
            "",
            "<b>근거 뉴스</b>",
        ]
        lines.extend(
            f"  • ({item['sentiment']:+.1f}) {html.escape(item['title'])}"
            for item in view["recent"]
        )
        lines.append(_view_footer())
        await message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # /view — 관심종목 전체 요약
    if not watchlist:
        await message.reply_text("관심종목이 없습니다.\n/add 종목코드 로 추가하세요.")
        return
    views = aggregate_stock_views(entries, watchlist)
    lines = [f"<b>관심종목 감성 뷰 (최근 {VIEW_LOOKBACK_DAYS}일)</b>", ""]
    for code, name in watchlist.items():
        view = views.get(code)
        if view is None:
            lines.append(f"  • {html.escape(name)} ({code}) — 신호 없음")
        else:
            lines.append(
                f"  • {html.escape(name)} ({code}) {_VERDICT_LABELS[view['verdict']]}"
                f" ({view['avg_sentiment']:+.2f}, {view['count']}건)"
            )
    lines.append(_view_footer())
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """뉴스 감성 신호 집계로 종목별 상승/중립/하락 참고 뷰를 표시한다.

    규칙 기반(평균 감성 임계값)이며 LLM을 추가로 호출하지 않는다.
    """
    message = update.effective_message
    if message is None:
        return
    await _send_stock_sentiment_view(
        message,
        context.bot_data,
        list(context.args or []),
    )


async def handle_view_callback(query, context, data: str) -> bool:
    """관심종목 목록의 "📈 감성" 버튼(`view:<code>`) → `/view <code>`와 같은 경로."""
    code = data.removeprefix("view:")
    await _send_stock_sentiment_view(query.message, context.bot_data, [code])
    return True


