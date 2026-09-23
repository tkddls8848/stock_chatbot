"""국가·증시별 일일 감성 다이제스트 캐시.

`/market` 차트는 기사별 감성을 매번 평균하지 않고 이 캐시의 확정값을 읽는다.
하루치를 한 번의 LLM 호출로 계산해 저장하므로 같은 기간을 다시 조회해도 값이
변하지 않는다(`final=True`인 날은 재계산하지 않는다).

보존 기간은 `/market`의 최대 조회 범위(30일)와 맞춘다. 그보다 오래된 항목은
차트가 읽을 수 없으므로 남겨 둘 이유가 없다.
"""

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from services.telegram_bot.core.clock import now, today
from services.telegram_bot.core.storage import write_json_atomic

logger = logging.getLogger(__name__)


def digest_key(market: str, day: date) -> str:
    return f"{str(market).strip().upper()}:{day.isoformat()}"


class MarketDigestStore:
    def __init__(self, file_path: Path, retention_days: int = 30):
        self._file_path = file_path
        self._retention_days = max(1, retention_days)
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _cutoff(self) -> date:
        return today() - timedelta(days=self._retention_days - 1)

    def _evict(self) -> None:
        cutoff = self._cutoff()
        kept = {}
        for key, entry in self._entries.items():
            try:
                if date.fromisoformat(str(entry.get("date"))) >= cutoff:
                    kept[key] = entry
            except (TypeError, ValueError):
                continue
        self._entries = kept

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[DIGEST] 캐시를 읽지 못해 빈 상태로 시작합니다.")
            return
        if not isinstance(raw, dict):
            return
        self._entries = {
            key: entry for key, entry in raw.items() if isinstance(entry, dict)
        }
        before = len(self._entries)
        self._evict()
        if len(self._entries) != before:
            self._write()

    def _write(self) -> None:
        # 실패를 삼키지 않는다. 이전에는 로그만 남기고 정상 반환해서, 하루치
        # 다이제스트가 디스크에 없는데 메모리에는 있는 상태로 갈렸다.
        write_json_atomic(self._file_path, self._entries)

    async def put(
        self,
        market: str,
        day: date,
        *,
        sentiment: float,
        article_count: int,
        summary: str = "",
        positive: int | None = None,
        negative: int | None = None,
        neutral: int | None = None,
        headlines: list[str] | None = None,
        final: bool = False,
    ) -> None:
        """하루치 다이제스트를 저장한다. 같은 날을 다시 넣으면 덮어쓴다.

        저장에 실패하면 메모리도 되돌리고 예외를 올린다. 디스크에 없는 날을
        "계산 완료"로 기억하면 `missing_digest_days()`가 그 날을 건너뛰어,
        재시작 전까지 영영 빈 자리로 남는다.
        """
        async with self._lock:
            previous = dict(self._entries)
            self._entries[digest_key(market, day)] = {
                "market": str(market).strip().upper(),
                "date": day.isoformat(),
                "sentiment": max(-1.0, min(1.0, float(sentiment))),
                "article_count": int(article_count),
                "summary": str(summary or ""),
                "positive": positive,
                "negative": negative,
                "neutral": neutral,
                "headlines": list(headlines or []),
                "computed_at": now().isoformat(timespec="seconds"),
                "final": bool(final),
            }
            self._evict()
            try:
                self._write()
            except OSError:
                self._entries = previous
                raise

    async def missing_digest_days(
        self,
        markets: set[str],
        lookback_days: int,
    ) -> dict[str, list[date]]:
        """다시 계산해야 하는 (시장, 날짜)를 반환한다.

        캐시에 없는 날과 `final=False`인 날이 대상이다. 오늘은 아직 기사가 다
        모이지 않았으므로 저장 시점에 `final=False`로 남고, 따라서 `/market`을
        부를 때마다 갱신된다. 날이 지나면 `final=True`로 굳어 다시는 계산하지
        않는다 — 이것이 "같은 기간은 같은 값" 보장의 근거다.
        """
        current_day = today()
        expected = [
            current_day - timedelta(days=offset)
            for offset in range(max(1, lookback_days))
        ]
        oldest_kept = self._cutoff()
        async with self._lock:
            result: dict[str, list[date]] = {}
            for market in sorted(markets):
                pending = []
                for day in expected:
                    if day < oldest_kept:
                        # 보존 범위 밖이라 저장해도 곧 지워진다. 계산하지 않는다.
                        continue
                    entry = self._entries.get(digest_key(market, day))
                    if entry is None or not entry.get("final"):
                        pending.append(day)
                result[market] = pending
            return result

    async def series(
        self,
        markets: set[str],
        lookback_days: int,
    ) -> dict[str, dict[str, Any]]:
        """차트가 그대로 쓰는 시장별 집계를 만든다.

        `aggregate_market_sentiment()`와 같은 모양({avg_sentiment, count, daily})을
        돌려주므로 차트·게이트 코드는 형태를 바꾸지 않아도 된다.
        """
        current_day = today()
        window = {
            (current_day - timedelta(days=offset)).isoformat()
            for offset in range(max(1, lookback_days))
        }
        buckets: dict[str, dict[str, Any]] = {}
        async with self._lock:
            for entry in self._entries.values():
                market = str(entry.get("market") or "").strip().upper()
                if market not in markets or str(entry.get("date")) not in window:
                    continue
                sentiment = entry.get("sentiment")
                if not isinstance(sentiment, (int, float)):
                    continue
                bucket = buckets.setdefault(market, {"sum": 0.0, "days": []})
                bucket["sum"] += float(sentiment)
                bucket["days"].append(
                    {
                        "date": str(entry.get("date")),
                        "avg_sentiment": float(sentiment),
                        # 게이트가 하루치 표본을 검사할 수 있게 기사 수를 싣는다.
                        "count": int(entry.get("article_count") or 0),
                        "summary": str(entry.get("summary") or ""),
                    }
                )
        for bucket in buckets.values():
            days = sorted(bucket.pop("days"), key=lambda point: point["date"])
            bucket["daily"] = days
            bucket["avg_sentiment"] = bucket.pop("sum") / len(days)
            # `count`는 시장 단위 총 기사 수다(일수가 아니라). 기존 게이트가
            # `MARKET_CHART_MIN_ARTICLES`와 비교하므로 의미를 맞춘다.
            bucket["count"] = sum(point["count"] for point in days)
        return buckets

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            final_days = sum(1 for entry in self._entries.values() if entry.get("final"))
            return {"entries": len(self._entries), "final": final_days}


def market_history_gaps(
    markets: dict[str, dict[str, Any]],
    required_markets: set[str],
    minimum_articles: int,
    minimum_days: int,
    minimum_articles_per_day: int = 1,
) -> dict[str, str]:
    """Return markets that lack enough daily digest samples for a chart."""
    gaps: dict[str, str] = {}
    per_day_threshold = max(1, minimum_articles_per_day)
    for market in sorted(required_markets):
        stats = markets.get(market)
        if stats is None:
            gaps[market] = "no scored articles"
        elif stats["count"] < minimum_articles:
            gaps[market] = f"{stats['count']}/{minimum_articles} scored articles"
        elif len(stats["daily"]) < minimum_days:
            gaps[market] = f"{len(stats['daily'])}/{minimum_days} calendar days"
        else:
            dense_days = sum(
                int(point.get("count") or 0) >= per_day_threshold
                for point in stats["daily"]
            )
            if dense_days < minimum_days:
                gaps[market] = (
                    f"{dense_days}/{minimum_days} days with "
                    f"{per_day_threshold}+ articles"
                )
    return gaps
