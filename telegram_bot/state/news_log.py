"""전송한 뉴스의 감성·관련종목 로그.

마감 브리핑의 관심종목별 감성 집계에 쓰인다. retention_days 이전 항목은
자동 만료된다.
"""

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from telegram_bot.core.clock import ensure_jst, now
from telegram_bot.core.storage import write_json_atomic


class NewsLog:
    def __init__(self, file_path: Path, retention_days: int = 3):
        self._file_path = file_path
        self._retention_days = max(1, retention_days)
        self._entries: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._load()

    def _cutoff(self) -> datetime:
        return now() - timedelta(days=self._retention_days)

    def _evict(self) -> None:
        cutoff = self._cutoff()
        kept = []
        for entry in self._entries:
            try:
                if ensure_jst(datetime.fromisoformat(str(entry.get("ts")))) >= cutoff:
                    kept.append(entry)
            except (TypeError, ValueError):
                continue
        self._entries = kept

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(raw, list):
            self._entries = [entry for entry in raw if isinstance(entry, dict)]
            self._evict()

    async def record(
        self,
        source: str,
        title: str,
        sentiment: float | None,
        impact: str,
        codes: list[str],
        occurred_at: datetime,
        market: str = "OTHER",
        article_id: str = "",
    ) -> bool:
        async with self._lock:
            normalized_id = str(article_id).strip()
            if normalized_id and any(
                entry.get("article_id") == normalized_id for entry in self._entries
            ):
                return False
            self._entries.append(
                {
                    "ts": occurred_at.isoformat(timespec="seconds"),
                    "source": source,
                    "article_id": normalized_id,
                    "title": title[:120],
                    "sentiment": sentiment,
                    "impact": impact,
                    "codes": list(codes),
                    "market": str(market or "OTHER").strip().upper(),
                }
            )
            self._evict()
            entries = list(self._entries)
            await asyncio.to_thread(write_json_atomic, self._file_path, entries)
            return True

    async def snapshot(self, since_hours: int = 24) -> list[dict[str, Any]]:
        cutoff = now() - timedelta(hours=max(1, since_hours))
        async with self._lock:
            result = []
            for entry in self._entries:
                try:
                    if ensure_jst(datetime.fromisoformat(str(entry.get("ts")))) >= cutoff:
                        result.append(dict(entry))
                except (TypeError, ValueError):
                    continue
            return result


def aggregate_sentiment_by_code(
    entries: list[dict[str, Any]],
    watchlist: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """관심종목 코드별 뉴스 건수·평균 감성을 집계한다."""
    stats: dict[str, dict[str, Any]] = {}
    for entry in entries:
        sentiment = entry.get("sentiment")
        codes = entry.get("codes") or []
        for code in codes:
            code = str(code)
            if code not in watchlist:
                continue
            bucket = stats.setdefault(
                code, {"count": 0, "sentiment_sum": 0.0, "scored": 0, "titles": []}
            )
            bucket["count"] += 1
            if len(bucket["titles"]) < 3:
                bucket["titles"].append(str(entry.get("title") or ""))
            if isinstance(sentiment, (int, float)):
                bucket["sentiment_sum"] += float(sentiment)
                bucket["scored"] += 1
    for bucket in stats.values():
        bucket["avg_sentiment"] = (
            bucket["sentiment_sum"] / bucket["scored"] if bucket["scored"] else None
        )
    return stats
