"""3시간 시장상황 보고서가 읽을 원문 기사 큐.

매시간 수집한 원문을 보고서 전송 시점까지 보존한다. 큐에 담긴 기사는 이미
`SentNewsTracker`에 예약되어 있어 보고서가 확정되기 전에 다시 수집되지 않는다.
"""

import asyncio
import json
import logging
from pathlib import Path

from shared.core.clock import now
from shared.core.storage import write_json_atomic

logger = logging.getLogger(__name__)


class NewsReportQueue:
    """보고서 원문 큐. 기사 ID와 사건 ID 양쪽으로 중복을 막는다."""

    def __init__(self, file_path: Path, per_source_limit: int, max_items: int):
        self._file_path = file_path
        self._per_source_limit = max(1, per_source_limit)
        self._max_items = max(1, max_items)
        self._lock = asyncio.Lock()
        self._items: list[dict] = []
        self._opened_at = ""
        self._load()

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8").strip() or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[NEWS REPORT] 큐 파일을 읽지 못해 빈 큐로 시작합니다: %s (%s)",
                self._file_path,
                exc,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("[NEWS REPORT] 큐 파일 형식이 올바르지 않아 빈 큐로 시작합니다.")
            return
        items = raw.get("items")
        self._items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        self._opened_at = str(raw.get("opened_at") or "")

    def _keys(self) -> tuple[set[str], set[str]]:
        return (
            {str(item.get("article_id")) for item in self._items},
            {str(item.get("event_id")) for item in self._items if item.get("event_id")},
        )

    async def enqueue(self, items: list[dict]) -> list[dict]:
        """소스 하나가 한 수집 주기에 모은 기사를 담고 실제 저장된 것만 돌려준다.

        저장에 실패하면 메모리도 되돌리고 예외를 올린다 — 담겼다고 보고한 뒤
        파일에 없으면 호출자가 예약을 확정으로 착각해 그 기사를 잃는다.
        """
        if not items:
            return []
        async with self._lock:
            article_ids, event_ids = self._keys()
            accepted: list[dict] = []
            for item in items:
                if len(accepted) >= self._per_source_limit:
                    break
                article_id = str(item.get("article_id") or "")
                event_id = str(item.get("event_id") or "")
                if not article_id or article_id in article_ids:
                    continue
                # 같은 사건을 여러 소스가 옮겨 적은 것은 한 건만 담는다.
                if event_id and event_id in event_ids:
                    continue
                article_ids.add(article_id)
                if event_id:
                    event_ids.add(event_id)
                accepted.append(item)
            if not accepted:
                return []

            previous_items = list(self._items)
            previous_opened = self._opened_at
            self._items.extend(accepted)
            # 상한을 넘으면 가장 오래된 것부터 버린다. 보고서는 최근 구간의
            # 시장상황을 다루므로 최근 쪽을 남긴다.
            if len(self._items) > self._max_items:
                dropped = len(self._items) - self._max_items
                self._items = self._items[dropped:]
                logger.warning("[NEWS REPORT] 큐 상한 초과로 오래된 %d건을 버립니다.", dropped)
            if not self._opened_at:
                self._opened_at = now().isoformat(timespec="seconds")
            try:
                await self._persist()
            except Exception:
                self._items = previous_items
                self._opened_at = previous_opened
                raise
            return accepted

    async def snapshot(self) -> tuple[str, list[dict]]:
        async with self._lock:
            return self._opened_at, list(self._items)

    async def clear(self) -> None:
        async with self._lock:
            previous_items = list(self._items)
            previous_opened = self._opened_at
            self._items = []
            self._opened_at = ""
            try:
                await self._persist()
            except Exception:
                self._items = previous_items
                self._opened_at = previous_opened
                raise

    async def _persist(self) -> None:
        payload = {"opened_at": self._opened_at, "items": list(self._items)}
        await asyncio.to_thread(write_json_atomic, self._file_path, payload)
