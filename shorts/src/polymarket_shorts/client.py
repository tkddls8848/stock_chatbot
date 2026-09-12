from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    brief: dict[str, Any]
    summary: dict[str, Any]

    @property
    def generation_id(self) -> str:
        return str(self.brief["generation_id"])


class PolymarketWebClient:
    def __init__(self, base_url: str, *, timeout: float = 20, session: Any | None = None):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str) -> dict[str, Any]:
        url = urljoin(self.base_url, path.lstrip("/"))
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SourceError(f"웹 API를 읽지 못했습니다: {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"웹 API 응답이 JSON 객체가 아닙니다: {url}")
        return payload

    def snapshot(self) -> Snapshot:
        brief = self._get("api/polymarket/sector-brief")
        summary = self._get("api/polymarket/summary")
        brief_generation = str(brief.get("generation_id") or "")
        summary_generation = str(summary.get("generation_id") or "")
        if not brief_generation or brief_generation != summary_generation:
            raise SourceError(
                "컨센서스와 대시보드 generation이 다릅니다: "
                f"brief={brief_generation or '-'} summary={summary_generation or '-'}"
            )
        freshness = str((summary.get("freshness") or {}).get("state") or "unknown")
        if freshness in {"missing", "delayed", "stale"}:
            raise SourceError(f"대시보드 데이터가 최신 상태가 아닙니다: {freshness}")
        return Snapshot(brief=brief, summary=summary)

