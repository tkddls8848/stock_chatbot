"""뉴스 감성 신호 기록(JSONL append)과 종목별 뷰 집계.

전송한 뉴스의 감성·관련종목을 축적해 /view 명령의 집계 입력으로 사용한다.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from telegram_bot.core.clock import ensure_jst, now

logger = logging.getLogger(__name__)

# 평균 감성이 이 값 이상이면 상승(up), -이 값 이하면 하락(down), 사이면 중립(neutral).
VIEW_SENTIMENT_THRESHOLD = 0.2


class PredictionLog:
    """append 전용 JSONL 신호 로그. 한 줄 = 전송된 뉴스 1건."""

    def __init__(self, file_path: Path):
        self._file_path = file_path
        self._lock = asyncio.Lock()

    async def record(
        self,
        source: str,
        title: str,
        sentiment: float,
        impact: str,
        codes: list[str],
        market: str = "",
    ) -> None:
        """신호 1건을 기록한다. 실패해도 뉴스 전송 흐름을 막지 않는다."""
        entry = {
            "ts": now().isoformat(timespec="seconds"),
            "source": source,
            "title": str(title)[:120],
            "sentiment": sentiment,
            "impact": impact,
            "market": str(market).upper().strip(),
            "codes": [c for c in codes if c],
        }
        line = json.dumps(entry, ensure_ascii=False)
        async with self._lock:
            try:
                await asyncio.to_thread(self._append_line, line)
            except Exception as e:
                logger.warning("[PREDICT] 신호 기록 실패: %s", e)

    def _append_line(self, line: str) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    async def snapshot(self, lookback_days: int) -> list[dict[str, Any]]:
        """최근 lookback_days 이내 신호 목록(과거→최신 순)."""
        async with self._lock:
            return await asyncio.to_thread(self._read_recent, lookback_days)

    def _read_recent(self, lookback_days: int) -> list[dict[str, Any]]:
        if not self._file_path.exists():
            return []
        cutoff = now() - timedelta(days=max(1, lookback_days))
        entries: list[dict[str, Any]] = []
        for line in self._file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if ensure_jst(datetime.fromisoformat(str(entry.get("ts")))) >= cutoff:
                    entries.append(entry)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return entries


def aggregate_stock_views(
    entries: list[dict[str, Any]],
    watchlist: dict[str, str],
    threshold: float = VIEW_SENTIMENT_THRESHOLD,
) -> dict[str, dict[str, Any]]:
    """종목별 감성 신호 집계(순수 함수, 테스트 대상).

    watchlist에 있는 코드만 집계한다.
    반환: code -> {count, avg_sentiment, verdict(up/neutral/down), recent(최신순 최대 5건)}
    """
    per_code: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        try:
            sentiment = float(entry.get("sentiment"))
        except (TypeError, ValueError):
            continue
        title = str(entry.get("title") or "")
        for code in entry.get("codes") or []:
            code = str(code).strip()
            if code in watchlist:
                per_code.setdefault(code, []).append(
                    {"title": title, "sentiment": sentiment}
                )

    views: dict[str, dict[str, Any]] = {}
    for code, items in per_code.items():
        avg = sum(item["sentiment"] for item in items) / len(items)
        if avg >= threshold:
            verdict = "up"
        elif avg <= -threshold:
            verdict = "down"
        else:
            verdict = "neutral"
        views[code] = {
            "count": len(items),
            "avg_sentiment": avg,
            "verdict": verdict,
            "recent": items[-5:][::-1],
        }
    return views
