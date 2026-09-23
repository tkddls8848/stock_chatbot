"""자연어 검색용 event 주석을 다는 독립 one-shot.

마지막으로 승격된 generation(`current.json`)을 읽고, **새로 생겼거나 제목이
바뀐 event만** LLM으로 한국어 요약·검색 키워드·세부 주제를 달아
`search_index.json`에 누적한다. 검색하는 순간에는 LLM을 부르지 않는다 — 공개
웹이 요청을 받아 밖으로 나가지 않는다는 규칙을 그대로 지키려는 것이다.

**refresh 안에 넣지 않는다.** LLM 실패가 generation 승격을 막으면 Cloudflare가
죽은 날 확률 숫자까지 멈춘다. 섹터 줄글과 같은 이유다.

**하루 Neurons 예산을 스스로 지킨다.** 별도 프로세스라 봇의 부하 조절이 닿지
않는다. refresh가 CPU·요청 예산을 지키는 방식과 같게, 최근 24시간 표본을
더해 상한을 넘으면 그 주기를 건너뛴다. 실패한 호출도 센다 — 세지 않으면
반복 실패가 예산을 그대로 통과한다.

계획서: `docs/polymarket-nl-search.md`
"""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from services.web.core.clock import ensure_jst, now
from services.web.core.config import (
    POLYMARKET_ANNOTATE_BATCH_SIZE,
    POLYMARKET_ANNOTATE_DESCRIPTION_CHARS,
    POLYMARKET_ANNOTATE_MAX_BATCHES_PER_RUN,
    POLYMARKET_ANNOTATE_MAX_DAILY_NEURONS,
    POLYMARKET_ANNOTATE_NUM_PREDICT,
    POLYMARKET_ANNOTATE_STATUS_FILE,
    POLYMARKET_SEARCH_INDEX_FILE,
    POLYMARKET_WEB_DIR,
)
from services.web.core.storage import write_json_atomic
from services.web.llm import PolymarketAnnotationError, build_polymarket_annotator
from services.web.polymarket.dashboard.storage import read_detail

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
BUDGET_WINDOW_HOURS = 24
# 모델에 넘기는 태그 수. 검색 키워드의 힌트일 뿐이라 앞 몇 개면 충분하다.
TAG_LIMIT = 8

# Cloudflare가 응답에 Neurons를 담지 않았을 때 토큰으로 환산한다. 현재 모델에서
# 실측한 식이다(code_guide.md, 2026-09-21 — 입력·출력 네 점이 남는 몫 없이 맞는다).
NEURONS_PER_INPUT_TOKEN = 0.00463
NEURONS_PER_OUTPUT_TOKEN = 0.0304
# 응답을 받지 못한 호출은 토큰도 모른다. 입력은 글자 수로 어림하고 출력은
# 예약한 상한 전부가 나갔다고 본다. 과소 집계보다 과대 집계가 안전하다 —
# 틀려도 그 주기를 쉬는 것이지 무료 한도를 넘기는 것이 아니다.
CHARS_PER_TOKEN_ESTIMATE = 2


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def title_hash(title: str) -> str:
    """제목이 바뀌면 주석을 다시 단다. 판정 대상이 바뀐 것일 수 있다."""
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]


def recent_neurons(status: dict[str, Any], *, hours: int = BUDGET_WINDOW_HOURS) -> tuple[list[dict], float]:
    """최근 `hours` 시간 안의 표본과 Neurons 합.

    개수가 아니라 시각으로 자른다 — 주기가 바뀌어도 하루 예산이 하루를 뜻하게
    한다(refresh의 `recent_usage`와 같다).
    """
    cutoff = now() - timedelta(hours=hours)
    samples = []
    for sample in status.get("samples", []):
        if not isinstance(sample, dict):
            continue
        stamp = _iso(str(sample.get("at", "")))
        if stamp is None or ensure_jst(stamp) < cutoff:
            continue
        samples.append(sample)
    return samples, round(sum(float(s.get("neurons") or 0.0) for s in samples), 2)


def call_neurons(backend: Any, prompt_chars: int, max_tokens: int) -> float:
    """호출 한 번이 쓴 Neurons. 응답의 값 → 토큰 환산 → 최악 어림 순으로 쓴다."""
    usage = getattr(backend, "last_usage", None)
    if usage is not None and usage.neurons is not None:
        return float(usage.neurons)
    if usage is not None and usage.input_tokens is not None and usage.output_tokens is not None:
        return (
            usage.input_tokens * NEURONS_PER_INPUT_TOKEN
            + usage.output_tokens * NEURONS_PER_OUTPUT_TOKEN
        )
    return (
        prompt_chars / CHARS_PER_TOKEN_ESTIMATE * NEURONS_PER_INPUT_TOKEN
        + max_tokens * NEURONS_PER_OUTPUT_TOKEN
    )


def pending_events(events: list[dict[str, Any]], index: dict[str, dict]) -> list[dict[str, Any]]:
    """주석이 없거나 제목이 바뀐 event. 24시간 거래량이 큰 것부터다.

    백필이 여러 날 걸리므로 사람들이 실제로 찾는 event가 먼저 검색되게 한다.
    """
    pending = [
        event for event in events
        if isinstance(event, dict) and event.get("id") is not None
        and (index.get(str(event["id"])) or {}).get("h") != title_hash(str(event.get("title") or ""))
    ]
    return sorted(pending, key=lambda event: float(event.get("volume24hr") or 0.0), reverse=True)


def _description(root: Path, event: dict[str, Any], limit: int) -> str:
    """detail을 이 event 한 행만 seek해서 읽는다. 전량을 읽지 않는다."""
    try:
        detail = read_detail(root, event)
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    return " ".join(str(detail.get("description") or "").split())[:limit]


def build(
    *,
    root: Path = POLYMARKET_WEB_DIR,
    index_path: Path = POLYMARKET_SEARCH_INDEX_FILE,
    status_path: Path = POLYMARKET_ANNOTATE_STATUS_FILE,
    annotator: Any | None = None,
    batch_size: int = POLYMARKET_ANNOTATE_BATCH_SIZE,
    max_batches: int = POLYMARKET_ANNOTATE_MAX_BATCHES_PER_RUN,
    max_daily_neurons: float = POLYMARKET_ANNOTATE_MAX_DAILY_NEURONS,
    description_chars: int = POLYMARKET_ANNOTATE_DESCRIPTION_CHARS,
    max_tokens: int = POLYMARKET_ANNOTATE_NUM_PREDICT,
) -> dict[str, Any]:
    manifest = _read_json(root / "current.json")
    events = manifest.get("events")
    if not manifest.get("generation_id") or not isinstance(events, list):
        logger.warning("[POLYMARKET_ANNOTATE] current generation이 없어 종료한다.")
        return {"state": "no_generation"}

    stored = _read_json(index_path)
    index: dict[str, dict] = {
        str(key): value for key, value in (stored.get("events") or {}).items()
        if isinstance(value, dict)
    }
    # 닫힌 event는 지운다. 화면은 "지금"만 보고 과거 조회는 만들지 않는다.
    open_ids = {str(event["id"]) for event in events if isinstance(event, dict) and event.get("id") is not None}
    pruned = [key for key in index if key not in open_ids]
    for key in pruned:
        del index[key]

    pending = pending_events(events, index)
    status = _read_json(status_path)
    samples, used = recent_neurons(status)
    started_at = now().isoformat()
    result: dict[str, Any] = {
        "state": "ok",
        "generation_id": manifest.get("generation_id"),
        "total": len(open_ids),
        "pruned": len(pruned),
        "annotated_now": 0,
        "dropped_now": 0,
        "calls": 0,
        "failed_calls": 0,
        "neurons": 0.0,
        "rolling_neurons_before": used,
    }

    if not pending:
        result["state"] = "up_to_date"
    elif used >= max_daily_neurons:
        logger.info(
            "[POLYMARKET_ANNOTATE] 24시간 예산 초과(%.1f/%.0f Neurons)라 건너뛴다.",
            used, max_daily_neurons,
        )
        result["state"] = "skipped_budget"
    else:
        annotator = annotator or build_polymarket_annotator()
        for start in range(0, len(pending), batch_size):
            if result["calls"] >= max_batches:
                break
            if used + result["neurons"] >= max_daily_neurons:
                result["state"] = "stopped_budget"
                break
            chunk = pending[start : start + batch_size]
            payload = [
                {
                    "n": number,
                    "title": str(event.get("title") or ""),
                    "tags": list(event.get("tags") or [])[:TAG_LIMIT],
                    "description": _description(root, event, description_chars),
                }
                for number, event in enumerate(chunk, start=1)
            ]
            result["calls"] += 1
            try:
                batch = annotator.annotate(payload)
            except PolymarketAnnotationError as error:
                result["failed_calls"] += 1
                result["neurons"] += call_neurons(
                    annotator.backend, len(json.dumps(payload, ensure_ascii=False)), max_tokens
                )
                logger.warning("[POLYMARKET_ANNOTATE] 배치 실패: %s", error)
                if error.stop:
                    result["state"] = "stopped_llm"
                    break
                continue
            result["neurons"] += call_neurons(
                annotator.backend, len(json.dumps(payload, ensure_ascii=False)), max_tokens
            )
            for number, row in batch.rows.items():
                event = chunk[number - 1]
                index[str(event["id"])] = {"h": title_hash(str(event.get("title") or "")), **row}
            result["annotated_now"] += len(batch.rows)
            result["dropped_now"] += batch.dropped
            if batch.dropped:
                logger.info(
                    "[POLYMARKET_ANNOTATE] %d건 버림: %s", batch.dropped, "; ".join(batch.reasons[:5])
                )
        # 할당량 소진·회로 차단(stopped_llm)은 다음 주기에 저절로 풀리는 정지라
        # 실패로 올리지 않는다. 그 밖에 호출이 전부 실패했으면 실패다.
        if result["state"] == "ok" and result["calls"] and result["failed_calls"] == result["calls"]:
            result["state"] = "failed"

    result["neurons"] = round(result["neurons"], 2)
    result["annotated"] = sum(1 for key in index if key in open_ids)
    result["pending"] = result["total"] - result["annotated"]

    if result["annotated_now"] or pruned:
        write_json_atomic(
            index_path,
            {
                "schema_version": SCHEMA_VERSION,
                "written_at": now().isoformat(),
                "generation_id": manifest.get("generation_id"),
                "events": index,
            },
        )
    if result["calls"]:
        samples.append({"at": started_at, "neurons": result["neurons"], "calls": result["calls"]})
    write_json_atomic(
        status_path,
        {
            "last_attempt_at": started_at,
            "last_result": result["state"],
            "rolling_neurons": round(sum(float(s.get("neurons") or 0.0) for s in samples), 2),
            "max_daily_neurons": max_daily_neurons,
            "annotated": result["annotated"],
            "pending": result["pending"],
            "total": result["total"],
            "samples": samples,
        },
    )
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = build()
    print(json.dumps(result, ensure_ascii=False))
    # 예산 건너뜀·할 일 없음은 의도된 결과라 0으로 끝낸다. 모든 호출이 실패한
    # 경우만 systemd가 실패로 보게 한다.
    if result["state"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
