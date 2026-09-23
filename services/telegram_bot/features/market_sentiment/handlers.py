"""텔레그램 관리 패널의 시장 감성 명령(`/market`) — 예약을 기다리지 않고 지금 갱신한다.

차트는 텔레그램으로 보내지 않는다. 웹의 시장 화면이 같은 산출물을 보여 준다.
"""

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.core.config import (
    MARKET_SENTIMENT_SCHEDULE_HOURS,
    MARKET_SENTIMENT_SCHEDULE_MINUTE,
)
from services.telegram_bot.features.market_sentiment.refresh import refresh_market_sentiment

logger = logging.getLogger(__name__)
# 텔레그램 문구용 한국어 이름. 차트(chart.py)는 폰트 때문에 영문 라벨을 쓴다.
_MARKET_NAMES = {"CN": "중국", "HK": "홍콩", "US": "미국", "KR": "한국", "JP": "일본"}


def schedule_text() -> str:
    return "·".join(f"{hour:02d}:{MARKET_SENTIMENT_SCHEDULE_MINUTE:02d}" for hour in MARKET_SENTIMENT_SCHEDULE_HOURS)


async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(f"시장 감성을 갱신합니다(예약: 매일 {schedule_text()}).")
    try:
        ready = await refresh_market_sentiment(context.application)
    except Exception:
        logger.exception("[MARKET] 수동 갱신 실패")
        await message.reply_text("시장 감성 갱신 실패: 로그를 확인하세요. 웹에는 직전 차트가 남아 있습니다.")
        return
    if ready is None:
        await message.reply_text("시계열이 부족해 차트를 굽지 않았습니다. 웹에는 직전 차트가 남아 있습니다.")
        return
    ranking = " | ".join(
        f"{_MARKET_NAMES.get(market, market)} {stats['avg_sentiment']:+.2f}"
        for market, stats in sorted(ready.items(), key=lambda item: item[1]["avg_sentiment"], reverse=True)
    )
    await message.reply_text(f"시장 감성 갱신 완료 · 웹 시장 화면에 반영\n{html.escape(ranking)}")
