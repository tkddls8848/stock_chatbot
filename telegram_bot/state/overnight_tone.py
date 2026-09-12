"""시장 세션별 전일 등락·당일 개장 전 센티먼트 저장소."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from shared.core.clock import today
from shared.core.storage import write_json_atomic

logger = logging.getLogger(__name__)


class OvernightToneStore:
    def __init__(self, file_path: Path, retention_days: int = 180):
        self._file_path = file_path
        self._retention_days = max(1, retention_days)
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._load()

    @staticmethod
    def key(market: str, price_session: date | str) -> str:
        day = price_session.isoformat() if isinstance(price_session, date) else str(price_session)
        return f"{market.strip().upper()}:{day}"

    def _load(self) -> None:
        if not self._file_path.exists():
            return
        try:
            raw = json.loads(self._file_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[ANOMALY] 상태 파일을 읽지 못해 빈 상태로 시작합니다.")
            return
        if isinstance(raw, dict):
            self._entries = {key: value for key, value in raw.items() if isinstance(value, dict)}
            self._evict()

    def _evict(self) -> None:
        cutoff = today() - timedelta(days=self._retention_days)
        kept = {}
        for key, entry in self._entries.items():
            try:
                if date.fromisoformat(str(entry["price_session"])) >= cutoff:
                    kept[key] = entry
            except (KeyError, TypeError, ValueError):
                continue
        self._entries = kept

    async def put(self, entry: dict[str, Any]) -> None:
        market = str(entry.get("market") or "").strip().upper()
        price_session = str(entry.get("price_session") or "")
        if not market or not price_session:
            raise ValueError("market and price_session are required")
        async with self._lock:
            previous = dict(self._entries)
            self._entries[self.key(market, price_session)] = dict(entry, market=market)
            self._evict()
            try:
                write_json_atomic(self._file_path, self._entries)
            except OSError:
                self._entries = previous
                raise

    async def contains(self, market: str, price_session: date | str) -> bool:
        async with self._lock:
            return self.key(market, price_session) in self._entries

    async def entries(self, markets: set[str] | None = None) -> list[dict[str, Any]]:
        wanted = {market.upper() for market in markets} if markets else None
        async with self._lock:
            return [
                dict(entry)
                for entry in self._entries.values()
                if wanted is None or str(entry.get("market") or "").upper() in wanted
            ]

    async def scored(self, markets: set[str]) -> dict[str, list]:
        from telegram_bot.features.market_sentiment.anomaly import score_entries

        rows = await self.entries(markets)
        return {
            market: score_entries(row for row in rows if row.get("market") == market)
            for market in sorted(markets)
        }

    async def gate_report(self, markets: set[str]) -> dict[str, dict[str, Any]]:
        from telegram_bot.features.market_sentiment.anomaly import apply_holm, market_gate_report

        rows = await self.entries(markets)
        reports = {
            market: market_gate_report(row for row in rows if row.get("market") == market)
            for market in sorted(markets)
        }
        apply_holm(reports)
        return reports
