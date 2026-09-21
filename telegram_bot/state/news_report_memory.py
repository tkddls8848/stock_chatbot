"""시장별로 마지막에 발행한 시장상황 보고서와 연속 보류 횟수.

**보고서가 직전 보고서를 모르면 통찰이 나올 수 없다.** 매 호출이 무상태이던
동안 모델은 비교 대상 없이 같은 국면을 매번 새 얘기처럼 다시 썼고, 프롬프트가
"직전 흐름에서 달라진 점"을 요구해도 직전 흐름이 입력에 없었다. 여기 남긴
마지막 본문이 다음 호출의 입력으로 들어가 "지난번 관찰 포인트가 확인됐는지"를
쓸 근거가 된다.

발행 판정의 보류 횟수도 여기 있다. 보류는 재료가 쌓일 때까지 기다리는 것이지
건너뛰는 것이 아니므로, 얼마나 오래 기다렸는지를 알아야 상한에서 발행할 수 있다.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from telegram_bot.core.clock import ensure_jst, now
from telegram_bot.core.storage import write_json_atomic

logger = logging.getLogger(__name__)

# 다음 호출 입력에 넣을 직전 본문의 길이 상한. 본문이 450~650자라 통째로
# 넣어도 입력 토큰은 1,000 선이다.
_ANALYSIS_MAX_CHARS = 800


class NewsReportMemory:
    """시장별 마지막 발행 보고서와 보류 상태."""

    def __init__(self, file_path: Path, retention_days: int = 30):
        self._file_path = file_path
        self._retention_days = max(1, retention_days)
        self._markets: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8").strip() or "{}")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "[NEWS REPORT] 보고서 기억 파일을 읽지 못해 빈 상태로 시작합니다: %s (%s)",
                self._file_path,
                exc,
            )
            return
        markets = raw.get("markets") if isinstance(raw, dict) else None
        if not isinstance(markets, dict):
            return
        self._markets = {
            str(key): value for key, value in markets.items() if isinstance(value, dict)
        }
        self._evict()

    def _evict(self) -> None:
        """오래 보이지 않은 시장은 지운다. 소스가 준 임의의 시장 코드도 들어온다."""
        cutoff = now() - timedelta(days=self._retention_days)
        kept = {}
        for market, entry in self._markets.items():
            seen = _parse(entry.get("seen_at") or entry.get("published_at"))
            if seen is None or seen >= cutoff:
                kept[market] = entry
        self._markets = kept

    def previous(self, market: str) -> dict[str, Any] | None:
        """직전 **발행** 보고서. 보류분은 사용자가 보지 못했으므로 남기지 않는다."""
        entry = self._markets.get(market)
        if not entry or not entry.get("analysis"):
            return None
        return {
            "window": str(entry.get("window") or ""),
            "published_at": str(entry.get("published_at") or ""),
            "analysis": str(entry.get("analysis") or ""),
        }

    def last_published_at(self, market: str) -> datetime | None:
        """그 시장에 마지막으로 발행한 시각. 보고서 구간의 시작이다."""
        return _parse((self._markets.get(market) or {}).get("published_at"))

    def held_hours(self, market: str) -> float:
        """그 시장이 마지막으로 발행한 뒤 지난 시간.

        발행 이력이 없으면 이번 보류 구간이 시작된 시각을 쓴다. 한 번도
        발행하지 못한 시장이 상한에 영영 닿지 않으면 보류가 끝나지 않는다.
        """
        entry = self._markets.get(market)
        if not entry:
            return 0.0
        started = _parse(entry.get("published_at")) or _parse(entry.get("held_since"))
        if started is None:
            return 0.0
        return max(0.0, (now() - started).total_seconds() / 3600)

    def held_windows(self, market: str) -> int:
        entry = self._markets.get(market)
        return int((entry or {}).get("held_windows") or 0)

    async def record_published(self, market: str, window: str, analysis: str) -> None:
        moment = now().isoformat(timespec="seconds")
        await self._store(
            market,
            {
                "published_at": moment,
                "seen_at": moment,
                "window": window,
                "analysis": analysis[:_ANALYSIS_MAX_CHARS],
                "held_windows": 0,
            },
        )

    async def record_held(self, market: str, reason: str) -> None:
        """보류를 기록한다. 직전 본문은 그대로 둔다 — 다음 호출도 그것과 견준다."""
        entry = dict(self._markets.get(market) or {})
        moment = now().isoformat(timespec="seconds")
        entry["seen_at"] = moment
        entry.setdefault("held_since", moment)
        entry["held_windows"] = int(entry.get("held_windows") or 0) + 1
        entry["hold_reason"] = reason[:200]
        await self._store(market, entry)

    async def _store(self, market: str, entry: dict[str, Any]) -> None:
        """메모리는 저장에 성공한 뒤에만 바꾼다."""
        async with self._lock:
            previous = {key: dict(value) for key, value in self._markets.items()}
            self._markets[market] = entry
            self._evict()
            try:
                await asyncio.to_thread(
                    write_json_atomic, self._file_path, {"markets": self._markets}
                )
            except Exception:
                self._markets = previous
                raise


def _parse(value: Any) -> datetime | None:
    try:
        return ensure_jst(datetime.fromisoformat(str(value)))
    except (TypeError, ValueError):
        return None
