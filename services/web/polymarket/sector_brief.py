"""경제·금융·지정학 베팅을 분야별 줄글 컨센서스로 정리하는 독립 one-shot.

마지막으로 승격된 generation(`current.json`)만 읽는다. 순회도 하지 않고
`current.json`·`generations/`를 쓰지도 않는다. 실패해도 확률 숫자는 그대로다.

**대상은 `category`가 아니라 `tags`로 고른다.** `classify()`는 둘 이상 분야에
걸린 event를 `other`로 보내는데, 지정학과 경제가 동시에 걸린 event가 바로 이
브리프가 보려는 것이다. 자세한 근거는 `docs/polymarket-sector-brief.md` 2-2.

계획서: `docs/polymarket-sector-brief.md`
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import statistics
from typing import Any

from services.web.core.clock import now
from services.web.core.config import (
    POLYMARKET_BRIEF_FILE,
    POLYMARKET_BRIEF_MIN_EVENTS,
    POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP,
    POLYMARKET_BRIEF_NAMED_LIMIT,
    POLYMARKET_BRIEF_QUIET_HOURS,
    POLYMARKET_WEB_DIR,
)
from services.web.core.storage import write_json_atomic
from services.web.llm import PolymarketBriefError, build_polymarket_brief_analyzer
from services.web.polymarket.dashboard.models import title_probability
from services.web.polymarket.dashboard.taxonomy import assign_brief_group, brief_groups

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def collect_groups(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """event를 그룹 key별로 나눈다. 감시 태그가 없는 event는 어디에도 안 들어간다."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        group = assign_brief_group(event.get("tags") or [])
        if group is None:
            continue
        buckets.setdefault(group["key"], []).append(event)
    return buckets


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """집계는 **전부** 반영한다. 자르는 것은 이름을 부르는 자리뿐이다."""
    probabilities = [
        value
        for value in (_number(event.get("leader_probability")) for event in events)
        if value is not None
    ]
    status_counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("data_status") or "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "event_count": len(events),
        "volume24hr": round(sum(_number(e.get("volume24hr")) or 0.0 for e in events), 2),
        "liquidity": round(sum(_number(e.get("liquidity")) or 0.0 for e in events), 2),
        "status_counts": status_counts,
        "probability": {
            "median": round(statistics.median(probabilities), 4) if probabilities else None,
            "strong": sum(value >= 0.9 for value in probabilities),
            "tight": sum(0.4 <= value <= 0.6 for value in probabilities),
        },
    }


def named_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """거래량 상위부터 이름을 채운다. 프롬프트에 들어가는 것은 이것뿐이다."""
    ordered = sorted(events, key=lambda e: _number(e.get("volume24hr")) or 0.0, reverse=True)
    rows = []
    for event in ordered[:limit]:
        row = {
            "title": str(event.get("title") or ""),
            "volume24hr": _number(event.get("volume24hr")),
            "end_date": event.get("end_date"),
            "event_type": event.get("event_type"),
            "data_status": event.get("data_status"),
        }
        if event.get("event_type") == "binary":
            # 제목 기준 확률 하나만 넘긴다. leader를 같이 보내면 모델이 둘을
            # 섞어 쓴다.
            row["title_probability"] = title_probability(event)
        else:
            # 다지선다는 제목이 참·거짓 명제가 아니다. 앞선 후보와 그 확률을
            # 그대로 넘긴다.
            row["leader"] = event.get("leader")
            row["leader_probability"] = _number(event.get("leader_probability"))
        rows.append(row)
    return rows


def snapshot_probabilities(buckets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """다음 실행이 이동을 계산할 기준. 지금은 저장만 하고 읽지 않는다.

    첫 실행에 비교 대상이 없어 이동 서술은 두 번째 실행부터다. 그래서 값을
    처음부터 남겨 둔다 — 나중에 붙일 때 하루를 더 기다리지 않으려는 것이다.
    """
    return {
        str(event["id"]): {
            "p": _number(event.get("leader_probability")),
            "leader": event.get("leader"),
        }
        for events in buckets.values()
        for event in events
        if event.get("id") is not None
    }


def build(
    *,
    root: Path = POLYMARKET_WEB_DIR,
    target: Path = POLYMARKET_BRIEF_FILE,
    analyzer: Any | None = None,
    named_limit: int = POLYMARKET_BRIEF_NAMED_LIMIT,
    min_events: int = POLYMARKET_BRIEF_MIN_EVENTS,
    min_events_by_group: dict[str, int] | None = None,
    quiet_hours: set[int] | None = None,
) -> dict[str, Any] | None:
    # 야간에는 줄글만 멈춘다. refresh는 계속 돌아 확률 숫자는 미장 마감 직전
    # 구간을 놓치지 않는다 — 비용의 실체는 LLM이고 API 순회는 공짜다.
    # 파일을 건드리지 않으므로 직전 줄글이 last-good으로 남고, 화면은 그것을
    # 계속 보여 준다. 실패가 아니라 의도된 정지라 종료 코드도 0이다.
    hours = POLYMARKET_BRIEF_QUIET_HOURS if quiet_hours is None else quiet_hours
    hour = now().hour
    if hour in hours:
        logger.info("[POLYMARKET_BRIEF] %d시는 야간 정지 구간이라 건너뛴다.", hour)
        return {"state": "skipped_quiet_hours", "hour": hour}

    manifest = _read_json(root / "current.json")
    events = manifest.get("events")
    if not manifest.get("generation_id") or not isinstance(events, list):
        logger.warning("[POLYMARKET_BRIEF] current generation이 없어 종료한다.")
        return None

    buckets = collect_groups(events)
    analyzer = analyzer or build_polymarket_brief_analyzer()
    previous = _read_json(target)
    previous_groups = {
        str(group.get("key")): group
        for group in previous.get("groups", [])
        if isinstance(group, dict)
    }

    overrides = (
        POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP
        if min_events_by_group is None
        else min_events_by_group
    )
    groups: list[dict[str, Any]] = []
    written = 0
    for spec in brief_groups():
        selected = buckets.get(spec["key"], [])
        totals = summarize(selected)
        row: dict[str, Any] = {
            "key": spec["key"],
            "label": spec["label"],
            "sector": spec["sector"],
            **totals,
            "named_count": min(len(selected), named_limit),
        }
        if len(selected) < overrides.get(spec["key"], min_events):
            # 모델은 3건짜리 그룹에도 그럴듯한 단락을 써 준다. 그게 제일 위험하다.
            row["status"] = "insufficient_sample"
            groups.append(row)
            continue
        try:
            row["paragraph"] = analyzer.analyze(
                spec["label"], totals, named_events(selected, named_limit)
            )
            row["overview"] = row["paragraph"].split(". ", 1)[0].rstrip(".") + "."
            row["status"] = "ok"
            written += 1
        except PolymarketBriefError as error:
            logger.warning(
                "[POLYMARKET_BRIEF] group=%s 실패: %s", spec["key"], error
            )
            row["status"] = "failed"
            # 직전 단락을 이어받아 화면이 통째로 비지 않게 한다.
            stale = previous_groups.get(spec["key"], {})
            if stale.get("paragraph"):
                row["paragraph"] = stale["paragraph"]
                if stale.get("overview"):
                    row["overview"] = stale["overview"]
                row["stale"] = True
        groups.append(row)

    if written == 0:
        # 전부 실패하면 아무것도 쓰지 않는다. 직전 파일이 last-good으로 남는다.
        logger.error("[POLYMARKET_BRIEF] 모든 분야가 실패해 기존 파일을 유지한다.")
        return None

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": manifest.get("generation_id"),
        "generated_at": manifest.get("generated_at"),
        "written_at": now().isoformat(),
        "state": "ok",
        "named_limit": named_limit,
        "groups": groups,
        "previous": snapshot_probabilities(buckets),
    }
    write_json_atomic(target, payload)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = build()
    if result is None:
        raise SystemExit(1)
    if result.get("state") == "skipped_quiet_hours":
        # 의도된 정지다. systemd가 실패로 세지 않게 0으로 끝낸다.
        print(json.dumps(result, ensure_ascii=False))
        return
    print(
        json.dumps(
            {
                "generation_id": result["generation_id"],
                "groups": {row["key"]: row["status"] for row in result["groups"]},
                "event_counts": {row["key"]: row["event_count"] for row in result["groups"]},
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
