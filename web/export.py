"""공개 웹이 읽을 마지막 산출물을 파일로 내보낸다.

이 모듈은 봇 안에서만 호출된다. 공개 웹은 이 파일들을 읽기만 하므로 웹 요청이
LLM, matplotlib, 혹은 봇의 상태를 실행시키지 않는다.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from web.core.clock import now
from web.core.storage import write_bytes_atomic, write_json_atomic

logger = logging.getLogger(__name__)

WEBPUB_DIR = Path(__file__).resolve().parents[1] / "data" / "webpub"
MARKET_JSON = WEBPUB_DIR / "market.json"
MARKET_CHART = WEBPUB_DIR / "market_chart.png"
RESEARCH_JSON = WEBPUB_DIR / "research.json"
META_JSON = WEBPUB_DIR / "meta.json"
_META_LOCK = threading.Lock()


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
