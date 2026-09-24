"""공개 웹이 읽을 마지막 산출물을 공유 저장소(`storage/public/`)에 쓴다.

봇 코드다. 예전에는 웹 패키지(`services/web/export.py`)에 있어 봇이 웹을 지연
import했는데, 공유 저장소로 옮기며 그 유일한 교차 import를 없앴다 — 봇은 자기
`core/storage.py`로 쓰고, 웹은 같은 파일을 자기 코드로 읽기만 한다. 경로와
JSON 형식이 둘 사이의 계약이고, 형식을 바꾸면 웹의 읽는 쪽 테스트를 함께 고친다.
공개 웹 요청이 LLM, matplotlib, 혹은 봇의 상태를 실행시키지 않는 것은 그대로다.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
from datetime import date, timedelta

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.config import PUBLIC_DIR
from services.telegram_bot.core.storage import write_bytes_atomic, write_json_atomic

logger = logging.getLogger(__name__)

MARKET_JSON = PUBLIC_DIR / "market.json"
MARKET_CHART = PUBLIC_DIR / "market_chart.png"
RESEARCH_JSON = PUBLIC_DIR / "research.json"
NEWS_JSON = PUBLIC_DIR / "news.json"
META_JSON = PUBLIC_DIR / "meta.json"
_META_LOCK = threading.Lock()
_NEWS_LOCK = threading.Lock()


def publish_news(documents: list[dict[str, Any]]) -> None:
    """발행된 보고서·근거 제목만 공개한다. 최근 30일, 최대 3,000건을 보존한다.

    원문 전체·관심종목·리서치 상태는 받지 않는다. 검색 요청은 이 산출물만 읽는다.
    """
    moment = now()
    cutoff = (moment.date() - timedelta(days=29)).isoformat()
    target = NEWS_JSON
    fields = (
        "id", "kind", "market", "title", "text", "date", "published_at",
        "source", "url", "sentiment",
    )
    with _NEWS_LOCK:
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            existing = {}
        # 손상된 산출물은 덮어써서 성공으로 보고하지 않는다.
        rows = {row["id"]: row for row in existing.get("documents", [])}
        for document in documents:
            day = date.fromisoformat(document["date"]).isoformat()
            if cutoff <= day <= moment.date().isoformat():
                rows[document["id"]] = {key: document[key] for key in fields if key in document}
        kept = sorted(
            (row for row in rows.values() if cutoff <= row["date"] <= moment.date().isoformat()),
            key=lambda row: (row["date"], row.get("published_at", ""), row["id"]),
            reverse=True,
        )[:3000]
        generated_at = moment.isoformat(timespec="seconds")
        write_json_atomic(target, {"generated_at": generated_at, "documents": kept})
    _update_meta("news_generated_at", generated_at)


def _update_meta(key: str, generated_at: str) -> None:
    """산출물 시각을 합쳐 쓴다. 동시 갱신이 다른 키를 지우면 안 된다."""
    with _META_LOCK:
        try:
            existing = json.loads(META_JSON.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        existing[key] = generated_at
        existing["updated_at"] = now().isoformat(timespec="seconds")
        write_json_atomic(META_JSON, existing, indent=2)


def publish_market(
    image: bytes,
    markets: dict[str, dict[str, Any]],
    lookback_days: int,
) -> None:
    """렌더된 차트와 그 수치를 함께 저장한다."""
    generated_at = now().isoformat(timespec="seconds")
    write_bytes_atomic(MARKET_CHART, image)
    write_json_atomic(
        MARKET_JSON,
        {
            "generated_at": generated_at,
            "lookback_days": lookback_days,
            "markets": markets,
        },
        indent=2,
    )
    _update_meta("market_generated_at", generated_at)


def publish_research(
    sight: str,
    result: dict[str, Any],
    history: list[dict[str, Any]],
) -> None:
    """완료된 리서치의 전체 결과와 프롬프트용 압축 이력을 분리해 저장한다."""
    generated_at = now().isoformat(timespec="seconds")
    write_json_atomic(
        RESEARCH_JSON,
        {
            "generated_at": generated_at,
            "sight": sight,
            "last_result": result,
            "history": history,
        },
        indent=2,
    )
    _update_meta("research_generated_at", generated_at)
