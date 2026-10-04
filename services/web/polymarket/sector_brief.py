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
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
import statistics
from typing import Any, Callable

from services.web.core.clock import now
from services.web.core.config import (
    POLYMARKET_BRIEF_FILE,
    POLYMARKET_BRIEF_MIN_EVENTS,
    POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP,
    POLYMARKET_BRIEF_MIN_VOLUME,
    POLYMARKET_BRIEF_NAMED_LIMIT,
    POLYMARKET_BRIEF_PARAGRAPH_FORMAT,
    POLYMARKET_BRIEF_QUIET_HOURS,
    POLYMARKET_SEARCH_INDEX_FILE,
    POLYMARKET_WEB_DIR,
)
from services.web.core.storage import write_json_atomic
from services.web.llm import PolymarketBriefError, build_polymarket_brief_analyzer
from services.web.llm.polymarket_brief import join_facts, outlook
from services.web.polymarket.annotate import title_hash
from services.web.polymarket.dashboard.models import title_probability
from services.web.polymarket.dashboard.storage import read_detail
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


# 단락 형식(config). 실패한 분야는 같은 형식의 직전 단락만 이어받고, 공개 API도 이 형식만 내보낸다.
PARAGRAPH_FORMAT = POLYMARKET_BRIEF_PARAGRAPH_FORMAT
# independent_multi에서 사실 문장에 넣는 열린 선택지 수.
_MAX_OPTIONS = 3


def _percent(value: float) -> str:
    """0~1 확률을 화면 표기로. 소수 첫째 자리 반올림, .0은 떼고, 0·1도 그대로 쓴다."""
    number = (Decimal(str(value)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = format(number, "f")
    return (text[:-2] if text.endswith(".0") else text) + "%"


def _open_options(detail: dict[str, Any]) -> tuple[list[tuple[str, float]], int]:
    """(열린 선택지 (라벨, 예 확률) 상위 몇 개, 열린 선택지 전체 수).

    열린 선택지 판정은 대시보드 정규화와 같다(`closed is not True`, `active is not False`).
    '예'가 그 선택지를 가리키는 일반형(yes_label == "Yes")만 쓴다 — 결과 이름이 붙은 선택지의
    yes_probability는 첫 결과의 확률이라 라벨과 방향이 어긋날 수 있다.
    """
    options = []
    for market in detail.get("markets") or []:
        if not isinstance(market, dict) or market.get("closed") is True or market.get("active") is False:
            continue
        probability = _number(market.get("yes_probability"))
        label = str(market.get("outcome_label") or "").strip()
        if (market.get("price_valid") is not True or probability is None or not label
                or str(market.get("yes_label") or "") != "Yes"):
            continue
        options.append((label, probability))
    options.sort(key=lambda item: (-item[1], item[0]))
    return options[:_MAX_OPTIONS], len(options)


# 흔한 선택지 이름의 한국어. 방향이 바뀌지 않는 것만 옮기고 나머지(인물·날짜·가격대)는 원문을 둔다.
_CHOICE_KO = {"yes": "예", "no": "아니오", "up": "상승", "down": "하락", "no change": "변동 없음"}
_MONTHS = {name: str(number) for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}


def _choice(name: str) -> str:
    return _CHOICE_KO.get(name.strip().lower(), name)


def _label(event: dict[str, Any], labels: dict[str, Any] | None) -> str:
    """모델 입력의 한국어 이름(`label`). 사실 문장의 주어는 아니다 — 그것은 원문 제목이다(`_fact`).

    검색 주석의 한국어 요약을 쓰되, 믿을 수 있을 때만.

    (1) 주석이 지금 제목으로 단 것이어야 하고(제목 해시), (2) 요약의 숫자가 전부 원문 제목에 있어야
    한다(영문 달 이름은 숫자로 친다). 요약은 모델이 쓴 것이라 날짜를 틀리게 옮긴 경우가 있다
    (2026-10-03: "Fed rate cut by...?" → "연준이 2025년 12월 금리를 인하할지"). 아니면 원문 제목을 쓴다.
    이 검사를 통과해도 의미까지 맞다는 보장은 없어 사실 문장에는 쓰지 않는다(2차 검수 재현).
    """
    title = str(event.get("title") or "")
    entry = (labels or {}).get(str(event.get("id")))
    summary, digest = (entry.get("summary"), entry.get("h")) if isinstance(entry, dict) else (entry, None)
    if not summary or (digest is not None and digest != title_hash(title)):
        return title
    allowed = set(re.findall(r"\d+", title)) | {
        number for name, number in _MONTHS.items() if re.search(rf"\b{name}\b", title, re.IGNORECASE)}
    return str(summary) if set(re.findall(r"\d+", str(summary))) <= allowed else title


def _fact(event: dict[str, Any], detail: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """(모델에 보낼 확률 등급 칸, 서버가 쓸 사실 문장). 숫자가 없으면 사실 문장도 없다.

    모델 칸에는 확률 숫자를 두지 않고 등급(`outlook`)만 둔다. 숫자를 받은 모델이 다른 질문에 옮겨 붙였고,
    쓴 숫자를 정규식으로 잡으려 하자 표기 변형("100분의 이십"·"⅕"·"공점이공오")이 검수마다 새로 나왔다.
    입력에 없는 숫자는 옮겨 붙일 수 없다. 출력 쪽 탐지(`has_model_probability`)는 흔한 표기를 막는 이중 장치다.

    **주어는 원문 제목이다.** 검색 주석의 한국어 요약은 모델이 쓴 것이라 다른 질문을 가리키거나 조건을
    바꿀 수 있고(해시·숫자 검사를 통과한 잘못된 요약으로 "원유 … 20.5%"가 재현됐다 — 2차 검수), 그러면
    숫자를 서버가 써도 문장은 틀린다. 원문 제목은 그 event의 것이 확실하다.
    """
    quoted = f"‘{str(event.get('title') or '').strip()}’"
    if event.get("event_type") == "binary":
        probability = title_probability(event)
        if probability is not None:
            return ({"title_outlook": outlook(probability)},
                    f"{quoted}에 대해 참여자들은 그 가능성을 {_percent(probability)}로 본다.")
    if event.get("event_type") == "independent_multi":
        options, total = _open_options(detail or {})
        if options:
            listed = ", ".join(f"{_choice(name)} {_percent(value)}" for name, value in options)
            scope = "열린 선택지별 확률은" if total <= len(options) else f"열린 선택지 {total}개 중 상위 {len(options)}개의 확률은"
            return ({"options": [{"label": name, "outlook": outlook(value)} for name, value in options],
                     "open_option_count": total},
                    f"{quoted}의 {scope} {listed}다.")
        return {"probability_available": False}, None
    # 이름 붙은 두 선택지 binary("Up or Down")·exclusive_multi: 앞선 선택지와 그 확률.
    leader = event.get("leader")
    probability = _number(event.get("leader_probability"))
    if leader and probability is not None:
        return ({"leader": leader, "leader_outlook": outlook(probability)},
                f"{quoted}에서는 {_choice(str(leader))} 쪽이 {_percent(probability)}로 가장 앞선다.")
    return {"probability_available": False}, None


def named_events(
    events: list[dict[str, Any]],
    limit: int,
    *,
    labels: dict[str, str] | None = None,
    detail: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
) -> list[dict[str, Any]]:
    """거래량 상위부터 이름을 채운다. 프롬프트에 들어가는 것은 이것뿐이다.

    질문마다 한국어 이름(`label`, 검색 주석의 요약 — 모델이 읽기 쉬우라고 붙인다)을 붙이고, 확률 숫자는
    **서버가 원문 제목을 주어로 쓴 사실 문장(`fact`)**으로 둔다. 모델은 숫자를 쓰지 않고, 분석기가 참여 규모
    상위 질문의 `fact`를 단락 뒤에 붙인다(`llm/polymarket_brief.py`의 `attach_facts`). `fact`는 모델에
    보내지 않는다. 숫자가 없는 질문은 `probability_available: false`로 밝힌다 — 빈칸을 두면 모델이 다른
    질문의 숫자를 빌려 쓴다(2026-10-03 복합).
    """
    ordered = sorted(events, key=lambda e: _number(e.get("volume24hr")) or 0.0, reverse=True)
    rows = []
    for event in ordered[:limit]:
        title = str(event.get("title") or "")
        label = _label(event, labels)
        needs_detail = event.get("event_type") == "independent_multi" and detail is not None
        probabilities, fact = _fact(event, detail(event) if needs_detail else None)
        rows.append({
            "title": title,
            "label": label,
            "volume24hr": _number(event.get("volume24hr")),
            "end_date": event.get("end_date"),
            "event_type": event.get("event_type"),
            "data_status": event.get("data_status"),
            **probabilities,
            "fact": fact,
        })
    return rows


def _search_labels(path: Path) -> dict[str, dict[str, str]]:
    """검색 주석(event id → {summary, h}). 없거나 깨졌으면 빈 표 — 원문 제목을 쓴다."""
    events = _read_json(path).get("events")
    if not isinstance(events, dict):
        return {}
    return {str(key): {"summary": str(value.get("summary") or "").strip(), "h": str(value.get("h") or "")}
            for key, value in events.items()
            if isinstance(value, dict) and str(value.get("summary") or "").strip()}


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
    min_volume: float = POLYMARKET_BRIEF_MIN_VOLUME,
    quiet_hours: set[int] | None = None,
    search_index: Path = POLYMARKET_SEARCH_INDEX_FILE,
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

    # 잔가지를 먼저 친다. 집계의 "전부"는 하한을 넘은 전부다.
    buckets = collect_groups([
        event for event in events
        if isinstance(event, dict)
        and (_number(event.get("volume24hr")) or 0.0) >= min_volume
    ])
    labels = _search_labels(search_index)
    detail_failures: list[str] = []
    detail_reads = {"count": 0, "seconds": 0.0, "bytes": 0}

    def detail(event: dict[str, Any]) -> dict[str, Any] | None:
        # 처음 읽은 compact event의 ref로 같은 generation을 읽는다. 상세 하나가 깨져도
        # 그 질문만 확률 없음으로 두고 분야·실행은 계속한다.
        started = time.monotonic()
        detail_reads["count"] += 1
        try:
            detail_reads["bytes"] += int((event.get("detail_ref") or {}).get("length") or 0)
            return read_detail(root, event)
        except Exception as error:  # 파일·참조·해시 불일치 — 그 event만 비운다
            detail_failures.append(f"{event.get('id')}:{type(error).__name__}")
            return None
        finally:
            detail_reads["seconds"] += time.monotonic() - started

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
    attempted = 0
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
        rows = named_events(selected, named_limit, labels=labels, detail=detail)
        attempted += 1
        try:
            row["paragraph"] = analyzer.analyze(spec["label"], totals, rows)
            row["paragraph_format"] = PARAGRAPH_FORMAT
            row["paragraph_written_at"] = now().isoformat(timespec="seconds")
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
            # 같은 형식의 직전 단락만 이어받는다. 옛 형식은 모델이 숫자를 직접 써서 다른 질문의 확률을 붙인
            # 글이 있었다. 이어받을 것이 없으면 아래에서 서버 사실 문장만 둔다.
            if stale.get("paragraph") and stale.get("paragraph_format") == PARAGRAPH_FORMAT:
                row["paragraph"] = stale["paragraph"]
                if stale.get("overview"):
                    row["overview"] = stale["overview"]
                row["paragraph_format"] = PARAGRAPH_FORMAT
                row["stale"] = True
                # 원래 쓴 시각과 "확률만" 여부를 이어 둔다 — 장애가 길어져도 매번 새 글처럼 보이지 않게.
                if stale.get("paragraph_written_at"):
                    row["paragraph_written_at"] = stale["paragraph_written_at"]
                if stale.get("facts_only"):
                    row["facts_only"] = True
            else:
                # 믿을 만한 직전 단락이 없으면 서버가 쓴 사실 문장만 둔다 — 해설은 없어도 숫자는
                # 맞다. 분야가 통째로 비는 것보다 낫다.
                # 성공 경로와 같은 공개 검사(금지어·개수)를 거친다. overview는 두지 않는다 — 해설이 없다.
                facts_text = join_facts("", [item.get("fact") for item in rows])
                if facts_text:
                    row["paragraph"] = facts_text
                    row["paragraph_format"] = PARAGRAPH_FORMAT
                    row["paragraph_written_at"] = now().isoformat(timespec="seconds")
                    row["facts_only"] = True
        groups.append(row)

    if detail_failures:
        logger.warning("[POLYMARKET_BRIEF] 상세를 읽지 못해 확률 없음으로 둔 질문 %d건: %s",
                       len(detail_failures), ", ".join(detail_failures[:10]))
    if detail_reads["count"]:
        logger.info("[POLYMARKET_BRIEF] 상세 읽기 %d건 · %.2f초 · %d bytes",
                    detail_reads["count"], detail_reads["seconds"], detail_reads["bytes"])
    if attempted == 0:
        # 모든 분야가 표본 미달이라 부른 적이 없다. 쓸 것이 없다.
        return None
    if written == 0:
        # 전부 실패해도 쓴다. 예전에는 직전 파일을 그대로 두었는데, 그 파일에 모델이 숫자를 직접 써서
        # 다른 질문의 확률을 붙인 글이 있으면 그대로 계속 나갔다(검수 재현). 이번 행들은 믿을 만한 직전
        # 단락이나 서버 사실 문장만 담는다.
        logger.error("[POLYMARKET_BRIEF] 모든 분야가 실패했다. 직전 단락(안전한 것)과 사실 문장으로 쓴다.")

    analyzed = [group for group in groups if group.get("status") in ("ok", "failed")]
    counts = {
        "ok": sum(1 for group in analyzed if group["status"] == "ok"),
        "stale": sum(1 for group in analyzed if group.get("stale")),
        "facts_only": sum(1 for group in analyzed if group.get("facts_only") and not group.get("stale")),
        "empty": sum(1 for group in analyzed if group["status"] == "failed" and not group.get("paragraph")),
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": manifest.get("generation_id"),
        "generated_at": manifest.get("generated_at"),
        "written_at": now().isoformat(),
        # 해설이 하나도 안 나온 실행을 정상처럼 적지 않는다(전부 실패해도 파일은 쓴다).
        "state": "ok" if written == len(analyzed) else ("partial" if written else "failed"),
        "group_counts": counts,
        "named_limit": named_limit,
        "min_volume": min_volume,
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
