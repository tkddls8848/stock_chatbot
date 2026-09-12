"""관심리스트 편입·편출 이벤트 로그.

이벤트 시점 가격을 함께 기록해 관리 웹의 `/api/events`가 편입·편출 내역을
읽는다. 가격 조회는 최선 노력이며 실패하면 None으로 남긴다.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared.core.clock import ensure_jst, now
from shared.core.storage import write_json_atomic
from shared.core.workers import run_non_urgent

logger = logging.getLogger(__name__)


class WatchlistEventLog:
    def __init__(self, file_path: Path, max_events: int = 500):
        self._file_path = file_path
        self._max_events = max_events
        self._events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(raw, list):
            self._events = [e for e in raw if isinstance(e, dict)]

    async def record(
        self,
        event: str,
        code: str,
        name: str,
        price: float | None,
        reason: str = "",
    ) -> None:
        async with self._lock:
            self._events.append(
                {
                    "ts": now().isoformat(timespec="seconds"),
                    "event": event,
                    "code": code,
                    "name": name,
                    "price": price,
                    "reason": reason[:200],
                }
            )
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events :]
            events = list(self._events)
            await asyncio.to_thread(
                write_json_atomic, self._file_path, events, indent=2
            )

    async def snapshot(self, lookback_days: int = 30) -> list[dict[str, Any]]:
        cutoff = now() - timedelta(days=max(1, lookback_days))
        async with self._lock:
            result = []
            for event in self._events:
                try:
                    if ensure_jst(datetime.fromisoformat(str(event.get("ts")))) >= cutoff:
                        result.append(dict(event))
                except (TypeError, ValueError):
                    continue
            return result


async def record_watchlist_event(
    bot_data: dict,
    event: str,
    code: str,
    name: str,
    reason: str = "",
) -> None:
    """핸들러 공용 헬퍼. 이벤트 로그가 없으면 조용히 무시한다."""
    event_log: WatchlistEventLog | None = bot_data.get("watchlist_events")
    if event_log is None:
        return
    price = None
    quote_service = bot_data.get("quote_service")
    if quote_service is not None:
        try:
            price = await run_non_urgent(quote_service.get_price, code)
        except Exception as e:
            logger.debug("[EVENTS] %s 가격 조회 실패: %s", code, e)
    try:
        await event_log.record(event, code, name, price, reason)
    except Exception as e:
        logger.warning("[EVENTS] 이벤트 기록 실패(%s %s): %s", event, code, e)
