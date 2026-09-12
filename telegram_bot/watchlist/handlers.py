import html
import logging
import re

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from telegram_bot.stocks import StockDatabase
from telegram_bot.stocks.universe import stock_key
from telegram_bot.watchlist.keyboards import (
    build_add_market_keyboard,
    build_list_keyboard,
)
from telegram_bot.watchlist.manager import WatchlistManager

logger = logging.getLogger(__name__)


def _normalize_stock_code(value: str) -> str | None:
    code = value.strip()
    if not re.fullmatch(r"\d{1,6}", code):
        return None
    return code.zfill(5) if len(code) <= 5 else code.zfill(6)


def normalize_selected_stock_code(selection: str, value: str) -> str | None:
    try:
        market, exchange = selection.rsplit(":", 1)
    except ValueError:
        return None
    market = market.strip().upper()
    exchange = exchange.strip().upper()
    raw = value.strip().upper()
    if market in {"KR", "CN", "HK"}:
        if not raw.isdigit():
            return None
    elif market == "US":
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", raw) or raw.isdigit():
            return None
    else:
        return None
    return stock_key(market, exchange, raw)


def _unsupported_code_message(code: str) -> str:
    if len(code) == 6 and code.startswith(("300", "301")):
        return "ChiNext(300/301) 종목은 Stock Connect에서 기관 전문투자자 대상이라 제외했습니다."
    if len(code) == 6 and code.startswith(("688", "689")):
        return "STAR Market(688/689) 종목은 Stock Connect에서 기관 전문투자자 대상이라 제외했습니다."
    return "외국인 개인 거래 가능 목록에 없는 종목입니다."


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wm: WatchlistManager = context.bot_data["watchlist_manager"]
    message = update.effective_message
    if message is None:
        return
    watchlist = await wm.get_all()
    text = (
        "<b>관심종목 관리</b>\n종목 버튼을 누르면 삭제됩니다."
        if watchlist
        else "<b>관심종목이 없습니다.</b>\n종목추가 버튼으로 등록하세요."
    )
    send = (
        message.edit_text
        if getattr(update, "callback_query", None) is not None
        else message.reply_text
    )
    try:
        await send(
            text,
            parse_mode="HTML",
            reply_markup=build_list_keyboard(watchlist),
        )
    except BadRequest as exc:
        # 이미 같은 관심종목 화면을 보고 있을 때 버튼을 다시 누르면 편집 내용이
        # 동일해 "Message is not modified"가 난다. 사용자에겐 정상 상태이므로 무시.
        if "not modified" not in str(exc).lower():
            raise


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wm: WatchlistManager = context.bot_data["watchlist_manager"]
    stock_db: StockDatabase = context.bot_data["stock_db"]
    if len(context.args) < 1:
        await update.message.reply_text("추가할 국가와 시장을 선택하세요.", reply_markup=build_add_market_keyboard())
        return

    selection = context.user_data.pop("add_market", "")
    code = normalize_selected_stock_code(selection, context.args[0]) if selection else _normalize_stock_code(context.args[0])
    if code is None:
        await update.message.reply_text("종목코드는 숫자 1~6자리로 입력하세요.")
        return

    if len(context.args) >= 2:
        await update.message.reply_text(
            f"종목코드만 입력하세요.\n사용법: /add {code}"
        )
        return

    code = stock_db.resolve_code(code) or code
    name = stock_db.get_display_name(code)
    if not name:
        await update.message.reply_text(
            f"{code} 추가 불가\n{_unsupported_code_message(code)}"
        )
        return

    await wm.add(code, name)
    await update.message.reply_text(f"추가됨: {name} ({code})\n/menu 로 목록 확인")
    logger.info("[WATCHLIST] 추가 (DB): %s %s", code, name)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wm: WatchlistManager = context.bot_data["watchlist_manager"]
    message = update.effective_message
    if message is None:
        return
    watchlist = await wm.get_all()
    if not watchlist:
        await message.reply_text(
            "<b>관심종목이 없습니다.</b>",
            parse_mode="HTML",
            reply_markup=build_list_keyboard(watchlist),
        )
        return
    stock_list = "\n".join(
        f"  • {html.escape(name)} ({html.escape(code)})"
        for code, name in watchlist.items()
    )
    await message.reply_text(
        f"<b>현재 관심종목</b>\n\n{stock_list}",
        parse_mode="HTML",
        reply_markup=build_list_keyboard(watchlist),
    )


async def handle_watchlist_callback(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if data == "close":
        await query.message.delete()
        return True

    if data in {"add_stock", "add_help"}:
        context.user_data.pop("menu_input", None)
        context.user_data.pop("add_market", None)
        await query.message.edit_text(
            "<b>종목추가</b>\n추가할 국가와 시장을 선택하세요.",
            parse_mode="HTML",
            reply_markup=build_add_market_keyboard(),
        )
        return True

    if data.startswith("add_market:"):
        selection = data.split(":", 1)[1]
        context.user_data["add_market"] = selection
        prompt = "티커" if selection.startswith("US:") else "종목코드"
        await query.message.reply_text(f"{selection} {prompt}를 입력하세요.")
        return True

    if data.startswith("remove:"):
        wm: WatchlistManager = context.bot_data["watchlist_manager"]
        code = data.split(":", 1)[1]
        name = await wm.remove(code) or code
        logger.info("[WATCHLIST] 삭제: %s %s", code, name)
        watchlist = await wm.get_all()
        if not watchlist:
            await query.message.edit_text(
                "<b>관심종목이 없습니다.</b>\n종목추가 버튼으로 등록하세요.",
                parse_mode="HTML",
                reply_markup=build_list_keyboard(watchlist),
            )
            return True
        await query.message.edit_text(
            f"<b>{html.escape(name)} ({code}) 삭제됨</b>\n\n"
            "<b>관심종목 관리</b>\n종목 버튼을 누르면 삭제됩니다.",
            parse_mode="HTML",
            reply_markup=build_list_keyboard(watchlist),
        )
        return True

    return False
