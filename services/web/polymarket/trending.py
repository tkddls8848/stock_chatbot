"""그날 움직인 베팅을 골라 조명하는 독립 one-shot.

마지막으로 승격된 generation(`current.json`)과 **자기가 남긴 스냅숏**만 읽는다.
순회하지 않고 `current.json`·`generations/`를 쓰지도 않으며, LLM도 부르지 않는다
(Neurons 0). 실패해도 확률 숫자와 화면은 그대로다.

**이 파일이 답하는 질문은 "지금 얼마인가"가 아니라 "오늘 무엇이 움직였나"다.**
대시보드는 현재 스냅숏만 보여 주도록 설계돼 있고 generation도 두 벌만 남는다
(detail shard가 generation 하나에 100 MiB를 넘는다). 그래서 이력을 늘리는 대신,
이 one-shot이 후보 event의 {확률, 1위, 거래량}만 자기 파일에 들고 다음 주기와
뺀다. 남는 것은 수백 줄짜리 dict 두 벌이다.

기준선은 **그날(UTC +9) 첫 generation**이다. 날짜가 바뀌면 기준선을 그 주기
값으로 다시 세운다 — "그날 트렌드"의 하루는 JST 하루다. 기준선을 막 세운 주기는
비교할 오늘이 없으므로 직전 주기 대비 이동으로 대신 고르고(`basis`), 화면이 그
사실을 그대로 적는다.

"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from services.web.core.clock import now, today
from services.web.core.config import (
    POLYMARKET_MIN_HOURS_TO_END,
    POLYMARKET_TRENDING_CANDIDATE_LIMIT,
    POLYMARKET_TRENDING_EXCLUDED_CATEGORIES,
    POLYMARKET_TRENDING_FILE,
    POLYMARKET_TRENDING_LIST_LIMIT,
    POLYMARKET_TRENDING_MIN_VOLUME,
    POLYMARKET_TRENDING_MOVE_FLOOR,
    POLYMARKET_TRENDING_SPOTLIGHT_LIMIT,
    POLYMARKET_WEB_DIR,
)
from services.web.core.storage import write_json_atomic
from services.web.polymarket.dashboard.models import ends_before, title_probability
from services.web.polymarket.dashboard.taxonomy import CATEGORY_TAGS

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


def _excluded_category(event: dict[str, Any], excluded: frozenset[str]) -> bool:
    """제외 분야이거나, 복합 분야인데 구성 분야 하나가 제외 분야면 True.

    목록용 event에는 `category_reason`이 없어 복합의 구성은 tags로 다시 잰다.
    """
    category = event.get("category")
    if category in excluded:
        return True
    if category != "composite":
        return False
    tags = set(event.get("tags") or ())
    return any(tags & CATEGORY_TAGS.get(key, set()) for key in excluded)


def candidates(
    events: list[dict[str, Any]],
    *,
    min_volume: float = POLYMARKET_TRENDING_MIN_VOLUME,
    limit: int = POLYMARKET_TRENDING_CANDIDATE_LIMIT,
    min_hours_to_end: float = POLYMARKET_MIN_HOURS_TO_END,
    excluded_categories: frozenset[str] = POLYMARKET_TRENDING_EXCLUDED_CATEGORIES,
) -> list[dict[str, Any]]:
    """이동을 추적할 event를 거래량 상위부터 고른다.

    `data_status`가 정상인 것만 본다. 확률을 읽지 못한 event의 이동은 값이
    아니라 결측의 변화이고, 유동성이 0인 event의 이동은 호가 한 건이다.
    곧 마감하는 event와 경기·날씨 분야도 뺀다 — 결과 확정을 향한 수렴은
    컨센서스의 이동이 아니다.
    """
    horizon = now() + timedelta(hours=min_hours_to_end)
    rows = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("id") is not None
        and event.get("data_status") == "ok"
        and (_number(event.get("volume24hr")) or 0.0) >= min_volume
        and not ends_before(event, horizon)
        and not _excluded_category(event, excluded_categories)
    ]
    rows.sort(key=lambda event: _number(event.get("volume24hr")) or 0.0, reverse=True)
    return rows[:limit]


def snapshot(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """다음 주기가 뺄셈할 값. 제목 기준 확률·1위·거래량 셋만 남긴다."""
    return {
        str(event["id"]): {
            "p": title_probability(event),
            "leader": event.get("leader"),
            "v": _number(event.get("volume24hr")),
        }
        for event in events
    }


def movement(event: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
    """기준 스냅숏 대비 이동.

    **다지선다의 1위가 바뀌면 숫자를 빼지 않는다.** 그 값은 서로 다른 후보의
    확률이라 차이가 뜻을 갖지 않는다. 대신 교체 자체가 그 주기의 가장 큰
    사건이므로 `leader_changed`로 올려 보낸다.

    binary는 `title_probability`가 제목 방향으로 정규화해 두므로 Yes↔No가
    뒤집혀도 그대로 뺀다 — 그것이 정확히 조명하려는 이동이다.
    """
    current = title_probability(event)
    result: dict[str, Any] = {
        "change": None,
        "leader_changed": False,
        "crossed_half": False,
        "volume_change": None,
    }
    if not before:
        return result
    previous = _number(before.get("p"))
    current_volume = _number(event.get("volume24hr"))
    previous_volume = _number(before.get("v"))
    if current_volume is not None and previous_volume is not None:
        result["volume_change"] = round(current_volume - previous_volume, 2)
    if event.get("event_type") != "binary" and str(before.get("leader") or "") != str(
        event.get("leader") or ""
    ):
        result["leader_changed"] = True
        return result
    if previous is None or current is None:
        return result
    result["change"] = round(current - previous, 4)
    result["crossed_half"] = (previous - 0.5) * (current - 0.5) < 0
    return result


def build_rows(
    events: list[dict[str, Any]],
    base: dict[str, Any],
    previous: dict[str, Any],
) -> list[dict[str, Any]]:
    """후보 하나를 화면이 읽는 한 줄로 만든다. 이동은 기준선과 직전 주기 둘 다 잰다."""
    rows = []
    for event in events:
        identity = str(event["id"])
        against_base = movement(event, base.get(identity))
        against_previous = movement(event, previous.get(identity))
        rows.append(
            {
                "id": identity,
                "title": str(event.get("title") or ""),
                "category": event.get("category"),
                "category_label": event.get("category_label"),
                "event_type": event.get("event_type"),
                "leader": event.get("leader"),
                "probability": title_probability(event),
                "volume24hr": _number(event.get("volume24hr")),
                "liquidity": _number(event.get("liquidity")),
                "end_date": event.get("end_date"),
                "change_day": against_base["change"],
                "change_previous": against_previous["change"],
                "leader_changed": against_base["leader_changed"],
                "crossed_half": against_base["crossed_half"],
                "volume_change": against_base["volume_change"],
                "is_new": identity not in base,
            }
        )
    return rows


def spotlight(
    rows: list[dict[str, Any]],
    *,
    basis: str,
    limit: int = POLYMARKET_TRENDING_SPOTLIGHT_LIMIT,
    move_floor: float = POLYMARKET_TRENDING_MOVE_FLOOR,
) -> list[dict[str, Any]]:
    """조명할 줄을 고른다. 1위 교체가 먼저고 그다음이 이동 폭이다.

    1위가 바뀐 event를 이동 폭으로 줄 세울 수 없어(비교할 숫자가 없다) 두 묶음을
    순서대로 잇는다. 교체끼리는 거래량 순이다 — 참여가 많은 쪽이 먼저 읽힐
    값어치가 있다.
    """
    key = "change_day" if basis == "day" else "change_previous"
    flips = [row for row in rows if row["leader_changed"]]
    flips.sort(key=lambda row: row["volume24hr"] or 0.0, reverse=True)
    moves = [
        row
        for row in rows
        if not row["leader_changed"]
        and row[key] is not None
        and abs(row[key]) >= move_floor
    ]
    moves.sort(key=lambda row: (abs(row[key]), row["volume24hr"] or 0.0), reverse=True)
    selected = []
    for row in (*flips, *moves):
        selected.append({**row, "basis_change": None if row["leader_changed"] else row[key]})
        if len(selected) >= limit:
            break
    return selected


def build(
    *,
    root: Path = POLYMARKET_WEB_DIR,
    target: Path = POLYMARKET_TRENDING_FILE,
    candidate_limit: int = POLYMARKET_TRENDING_CANDIDATE_LIMIT,
    min_volume: float = POLYMARKET_TRENDING_MIN_VOLUME,
    spotlight_limit: int = POLYMARKET_TRENDING_SPOTLIGHT_LIMIT,
    move_floor: float = POLYMARKET_TRENDING_MOVE_FLOOR,
    list_limit: int = POLYMARKET_TRENDING_LIST_LIMIT,
) -> dict[str, Any] | None:
    manifest = _read_json(root / "current.json")
    events = manifest.get("events")
    if not manifest.get("generation_id") or not isinstance(events, list):
        logger.warning("[POLYMARKET_TRENDING] current generation이 없어 종료한다.")
        return None

    stored = _read_json(target)
    day = today().isoformat()
    stored_baseline = stored.get("baseline") if isinstance(stored.get("baseline"), dict) else {}
    stored_previous = stored.get("previous") if isinstance(stored.get("previous"), dict) else {}
    # 날짜가 바뀌었거나 기준선이 없으면 이번 주기가 그날의 기준선이 된다.
    fresh_baseline = str(stored_baseline.get("day") or "") != day
    selected = candidates(events, min_volume=min_volume, limit=candidate_limit)
    current_snapshot = snapshot(selected)

    baseline = (
        {
            "day": day,
            "generation_id": manifest.get("generation_id"),
            "captured_at": manifest.get("generated_at"),
            "events": current_snapshot,
        }
        if fresh_baseline
        else stored_baseline
    )
    base_events = baseline.get("events") if isinstance(baseline.get("events"), dict) else {}
    previous_events = (
        stored_previous.get("events") if isinstance(stored_previous.get("events"), dict) else {}
    )

    # 기준선을 막 세운 주기는 오늘 대비 이동이 전부 0이다. 그 0을 트렌드라고
    # 부르지 않는다 — 직전 주기 이동으로 고르고 화면이 그렇게 적는다.
    basis = "previous" if fresh_baseline else "day"
    rows = build_rows(selected, base_events, previous_events)
    if basis == "previous" and not previous_events:
        # 처음 도는 주기다. 비교할 것이 아무것도 없으니 조명하지 않고 스냅숏만
        # 남긴다. 다음 주기부터 이동이 나온다.
        state = "warming_up"
        chosen: list[dict[str, Any]] = []
    else:
        state = "ok"
        chosen = spotlight(rows, basis=basis, limit=spotlight_limit, move_floor=move_floor)

    new_entries = sorted(
        (row for row in rows if row["is_new"]),
        key=lambda row: row["volume24hr"] or 0.0,
        reverse=True,
    )[:list_limit]
    volume_movers = sorted(
        (row for row in rows if row["volume_change"] is not None and row["volume_change"] > 0),
        key=lambda row: row["volume_change"],
        reverse=True,
    )[:list_limit]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": manifest.get("generation_id"),
        "generated_at": manifest.get("generated_at"),
        "written_at": now().isoformat(),
        "day": day,
        "state": state,
        "basis": basis,
        "basis_at": baseline.get("captured_at") if basis == "day" else stored_previous.get("captured_at"),
        "baseline_at": baseline.get("captured_at"),
        "candidate_count": len(selected),
        "min_volume": min_volume,
        "move_floor": move_floor,
        "spotlight": chosen,
        "new_entries": new_entries,
        "volume_movers": volume_movers,
        # 아래 둘은 다음 주기가 뺄셈할 상태다. 화면은 읽지 않으며 server.py가
        # 내보내기 전에 잘라낸다.
        "baseline": baseline,
        "previous": {
            "generation_id": manifest.get("generation_id"),
            "captured_at": manifest.get("generated_at"),
            "events": current_snapshot,
        },
    }
    write_json_atomic(target, payload)
    return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = build()
    if result is None:
        raise SystemExit(1)
    print(
        json.dumps(
            {
                "generation_id": result["generation_id"],
                "state": result["state"],
                "basis": result["basis"],
                "candidate_count": result["candidate_count"],
                "spotlight_count": len(result["spotlight"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
