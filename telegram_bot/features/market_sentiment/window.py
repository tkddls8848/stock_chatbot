"""거래소 세션으로 전일 종가 이후~당일 개장 전 창을 만든다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

import exchange_calendars as xcals

from shared.core.clock import JST

CALENDAR_BY_MARKET = {
    "KR": "XKRX",
    "CN": "XSHG",
    "HK": "XHKG",
    "US": "XNYS",
}


@dataclass(frozen=True)
class MarketSessionWindow:
    market: str
    price_session: date
    sentiment_for_session: date
    start: datetime
    end: datetime

    @property
    def window_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@lru_cache(maxsize=len(CALENDAR_BY_MARKET))
def market_calendar(market: str):
    key = str(market).strip().upper()
    try:
        name = CALENDAR_BY_MARKET[key]
    except KeyError as exc:
        raise ValueError(f"unsupported market: {market}") from exc
    return xcals.get_calendar(name)


def session_window(market: str, price_session: date) -> MarketSessionWindow | None:
    """Return the window after ``price_session`` or None for a non-session."""
    key = str(market).strip().upper()
    calendar = market_calendar(key)
    label = price_session.isoformat()
    if not calendar.is_session(label):
        return None
    next_label = calendar.next_session(label)
    close = calendar.session_close(label).to_pydatetime().astimezone(JST)
    next_open = calendar.session_open(next_label).to_pydatetime().astimezone(JST)
    return MarketSessionWindow(
        market=key,
        price_session=price_session,
        sentiment_for_session=next_label.date(),
        start=close,
        end=next_open,
    )


def completed_windows(
    market: str,
    moment: datetime,
    *,
    lookback_sessions: int = 10,
) -> list[MarketSessionWindow]:
    """Return recent windows whose next session has already opened."""
    current = moment if moment.tzinfo is not None else moment.replace(tzinfo=JST)
    current = current.astimezone(JST)
    calendar = market_calendar(market)
    sessions = calendar.sessions_in_range(
        (current.date() - timedelta(days=max(20, lookback_sessions * 3))).isoformat(),
        current.date().isoformat(),
    )
    windows = []
    for label in sessions[-(lookback_sessions + 1) :]:
        window = session_window(market, label.date())
        if window is not None and window.end <= current:
            windows.append(window)
    return windows[-lookback_sessions:]


def recent_session_windows(
    market: str,
    moment: datetime,
    count: int,
) -> list[MarketSessionWindow]:
    """Return up to ``count`` completed windows for offline backfill."""
    current = moment if moment.tzinfo is not None else moment.replace(tzinfo=JST)
    current = current.astimezone(JST)
    calendar = market_calendar(market)
    sessions = calendar.sessions_in_range(
        (current.date() - timedelta(days=max(400, count * 3))).isoformat(),
        current.date().isoformat(),
    )
    windows = []
    for label in sessions:
        window = session_window(market, label.date())
        if window is not None and window.end <= current:
            windows.append(window)
    return windows[-count:]
