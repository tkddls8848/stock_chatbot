"""종목 DB 갱신 명령 구현."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from shared.core.workers import run_non_urgent
from telegram_bot.stocks import StockDatabase

logger = logging.getLogger(__name__)


async def cmd_stockdb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    stock_db: StockDatabase = context.bot_data["stock_db"]
    args = context.args or []
    command = args[0].lower() if args else ""

    if command == "build":
        status = await message.reply_text("종목 DB 빌드 중...")
        try:
            await run_non_urgent(stock_db.build)
            total = len(stock_db.get_all())
            await status.edit_text(f"종목 DB 빌드 완료: {total:,}종목")
        except Exception as e:
            logger.exception("[StockDB] build failed")
            await status.edit_text(f"빌드 실패: {e}")
        return

    await message.reply_text(
        "<b>stockdb 명령어</b>\n\n"
        "/stockdb build — 종목 코드·이름 목록 갱신",
        parse_mode="HTML",
    )
