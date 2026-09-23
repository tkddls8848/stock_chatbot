"""A주 거래일 캘린더.

신랑 거래일 이력(tool_trade_date_hist_sina)을 하루 단위로 캐시해 휴장일에는
브리핑을 건너뛴다. 조회 실패 시 주중(월~금) 여부로 대체 판단한다.
홍콩 캘린더와 다소 다를 수 있으나 콘텐츠 대부분이 A주 기준이라 A주
캘린더를 우선한다.
"""

import logging
from datetime import date, datetime
from services.telegram_bot.core.clock import today

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


class TradeCalendar:
    def __init__(self):
        self._trade_dates: set[date] | None = None
        self._loaded_on: date | None = None

    def _load(self) -> None:
        current_day = today()
        if self._trade_dates is not None and self._loaded_on == current_day:
            return
        df = ak.tool_trade_date_hist_sina()
        dates = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
        self._trade_dates = {d for d in dates if pd.notna(d)}
        self._loaded_on = current_day
        logger.info("[Calendar] 거래일 %d일 로드", len(self._trade_dates))

    def is_trade_date(self, target: date | datetime | None = None) -> bool:
        """블로킹 호출(to_thread 권장). 조회 실패 시 주중이면 True."""
        if target is None:
            target = today()
        elif isinstance(target, datetime):
            target = target.date()
        try:
            self._load()
            assert self._trade_dates is not None
            return target in self._trade_dates
        except Exception as e:
            logger.warning("[Calendar] 거래일 조회 실패, 주중 여부로 대체: %s", e)
            return target.weekday() < 5
