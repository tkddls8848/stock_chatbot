import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from shared.core.clock import ensure_jst, now
from shared.core.storage import write_json_atomic

logger = logging.getLogger(__name__)


class SentNewsTracker:
    """중복 전송 방지. retention_days 이전에 저장된 ID는 자동 만료된다."""

    def __init__(self, file_path: Path, retention_days: int = 7):
        self._file_path = file_path
        self._retention_days = retention_days
        self._id_ts: dict[str, str] = {}  # id → ISO timestamp
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._load()

    def _cutoff(self) -> datetime:
        return now() - timedelta(days=self._retention_days)

    def _evict(self) -> None:
        cutoff = self._cutoff()
        expired = []
        for article_id, timestamp in self._id_ts.items():
            try:
                is_expired = ensure_jst(datetime.fromisoformat(timestamp)) < cutoff
            except (TypeError, ValueError):
                is_expired = True
            if is_expired:
                expired.append(article_id)
        for k in expired:
            del self._id_ts[k]

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            text = self._file_path.read_text(encoding="utf-8").strip()
            if not text:
                logger.warning("[SENT] 전송 이력 파일이 비어 있어 새 이력으로 시작합니다: %s", self._file_path)
                return
            raw = json.loads(text)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[SENT] 전송 이력 파일을 읽지 못해 새 이력으로 시작합니다: %s (%s)", self._file_path, exc)
            return

        if not isinstance(raw, dict):
            logger.warning("[SENT] 전송 이력 파일 형식이 올바르지 않아 새 이력으로 시작합니다: %s", self._file_path)
            return
        self._id_ts = {
            article_id: timestamp
            for article_id, timestamp in raw.items()
            if isinstance(article_id, str) and isinstance(timestamp, str)
        }
        self._evict()

    async def reserve(self, article_id: str) -> bool:
        """처리 중이거나 이미 보낸 ID가 아니면 pending으로 예약한다."""
        async with self._lock:
            if article_id in self._id_ts or article_id in self._pending:
                return False
            self._pending.add(article_id)
            return True

    async def confirm(self, article_id: str) -> None:
        """텔레그램 전송 성공 후 sent 목록에 확정한다."""
        async with self._lock:
            self._pending.discard(article_id)
            if article_id in self._id_ts:
                return
            self._id_ts[article_id] = now().isoformat()

    async def release(self, article_id: str) -> None:
        """번역 또는 전송 실패 시 다음 주기에 재시도할 수 있도록 예약을 해제한다."""
        async with self._lock:
            self._pending.discard(article_id)

    async def persist(self):
        async with self._lock:
            self._evict()
            id_ts = dict(self._id_ts)
            await asyncio.to_thread(write_json_atomic, self._file_path, id_ts)
