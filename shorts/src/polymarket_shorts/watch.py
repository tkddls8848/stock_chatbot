"""오래 지켜볼 큰 판을 고르고 그 흐름을 보여 주기 위한 일일 기록(`storage/shorts/watch/ledger/<날짜>.json`).

매일 새 주제만 고르던 영상에 "몇 주·몇 달 동안 지켜볼 큰 판"의 흐름을 더한다(운영자 결정 2026-11-09).
웹은 generation을 두 벌만 남기고 트렌드 기준선도 그날 첫 주기뿐이라 어제·지난달 확률을 내주지 않는다
(`services/web/polymarket/trending.py`). 그래서 쇼츠가 제작 때 읽은 목록을 자기 기록으로 남긴다.

- **목록 층:** 경기·문화·날씨를 뺀, 이틀 안에 끝나지 않는 정상 이벤트를 누적 참여 규모순으로
  `LEDGER_EVENTS`개 남긴다. 목록에 이미 있는 값이라 조회가 늘지 않는다.
- **상세 층:** 그중 종료까지 `LONG_HORIZON_DAYS`일 이상 남은 상위 `client.MAX_LEDGER_DETAILS`개는 개별 질문의
  '예' 확률까지 남긴다. 오래 지켜볼 판은 대개 "…by ___?" 같은 기한·기준별 질문 묶음이라 목록에 1위
  확률이 없다(2026-11-09 실측: 연준 인하·러-우 휴전·이란 지도부 교체가 모두 그렇다).

기록은 영상 제작을 막지 않는다. 실패는 `selection.json`의 `watch_ledger`에 남기고 제작을 잇는다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .client import MAX_LEDGER_DETAILS, Snapshot, SourceError
from .config import Settings
from .core.storage import write_json
from .markets import _topic, number, sector

SCHEMA_VERSION = 1
LEDGER_EVENTS = 500
# 추적 주제의 자격 하한(최소 유지 30일 + 여유)과 같다 — 후보가 될 판만 개별 질문까지 남긴다.
LONG_HORIZON_DAYS = 45
# 지난 기록으로 30일 추이와 선정 점수(최근 7일)를 계산한다. 넉넉히 넉 달을 둔다.
LEDGER_RETENTION_DAYS = 120
# 시장과 무관한 판. 웹 트렌드가 빼는 분야와 같은 취지다.
_EXCLUDED_CATEGORIES = {"sports", "culture", "weather_climate"}


def ledger_dir(settings: Settings) -> Path:
    return settings.output_dir / "watch" / "ledger"


def _deadline(event: dict[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(event["end_date"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return value if value.tzinfo else None


def ledger_events(snapshot: Snapshot) -> list[dict[str, Any]]:
    """기록할 이벤트를 누적 참여 규모순으로 고른다. 누적 값이 없으면 참여 잔액으로 줄 세운다."""
    reference = datetime.fromisoformat(snapshot.summary["generated_at"])
    rows = []
    for event in snapshot.events:
        deadline = _deadline(event)
        if (event.get("data_status") != "ok" or event.get("category") in _EXCLUDED_CATEGORIES
                or deadline is None or deadline - reference < timedelta(days=2)):
            continue
        rows.append({
            "id": str(event["id"]), "title": str(event.get("title") or "")[:300],
            "topic_key": _topic(str(event.get("title") or "")),
            "category": event.get("category"), "sector": sector(event), "event_type": event.get("event_type"),
            "end_date": event["end_date"], "days_to_end": round((deadline - reference).total_seconds() / 86400, 1),
            "leader": event.get("leader"), "leader_probability": number(event.get("leader_probability")),
            "volume": number(event.get("volume")), "volume24hr": number(event.get("volume24hr")),
            "liquidity": number(event.get("liquidity")),
        })
    rows.sort(key=lambda row: (-(row["volume"] or 0), -(row["liquidity"] or 0), row["id"]))
    return rows[:LEDGER_EVENTS]


def _markets(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """개별 질문의 '예' 확률. 판정이 끝났거나 가격이 비정상인 질문은 남기지 않는다."""
    result = []
    for row in detail.get("markets") or []:
        yes = number(row.get("yes_probability"))
        if (row.get("active") is not True or row.get("closed") is not False or row.get("price_valid") is not True
                or yes is None or not row.get("id")):
            continue
        result.append({"id": str(row["id"]), "label": str(row.get("outcome_label") or row.get("question") or "")[:200],
                       "yes": yes})
    return result


def record_ledger(settings: Settings, today: date, snapshot: Snapshot, client: Any) -> dict[str, Any]:
    """오늘 기록을 쓰고 보관 기간이 지난 기록을 지운다. 감사 기록에 남길 요약을 돌려준다.

    같은 날 다시 제작하면(`--force`) 그날 기록을 새 값으로 덮는다. 상세 하나를 못 읽어도 나머지는 남긴다.
    """
    events = ledger_events(snapshot)
    details: dict[str, list[dict[str, Any]]] = {}
    failures = []
    for row in [row for row in events if row["days_to_end"] >= LONG_HORIZON_DAYS][:MAX_LEDGER_DETAILS]:
        try:
            details[row["id"]] = _markets(client.detail(row["id"], snapshot.generation_id, ledger=True))
        except SourceError as exc:
            failures.append({"id": row["id"], "reason": str(exc)})
    folder = ledger_dir(settings)
    path = folder / f"{today.isoformat()}.json"
    write_json(path, {
        "schema_version": SCHEMA_VERSION, "date": today.isoformat(),
        "generation_id": snapshot.generation_id, "generated_at": snapshot.summary["generated_at"],
        "events": events, "details": details, "detail_failures": failures,
    })
    cutoff = today - timedelta(days=LEDGER_RETENTION_DAYS)
    for old in folder.glob("*.json"):
        try:
            stale = date.fromisoformat(old.stem) < cutoff
        except ValueError:
            continue
        if stale:
            old.unlink(missing_ok=True)
    return {"path": str(path), "events": len(events), "details": len(details), "detail_failures": len(failures)}
