"""개별 이벤트의 수치 선별과 원고에 사용할 베팅 사실. 외부 모델을 호출하지 않는다."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
import math
import re
from typing import Any
from urllib.parse import quote

from .client import Snapshot, SourceError


SECTORS = {
    "composite": "경제·지정학", "macro": "거시·통화", "equities": "주식·시장",
    "geopolitics": "지정학", "general": "기타 경제·금융",
}
_EQUITY = {"equities", "stocks", "pre-market"}
_MACRO = {"macro-indicators", "fed", "fed-rates", "interest-rates", "inflation"}
_GENERAL = {"economy", "finance"}
_GEO = {"geopolitics", "foreign-policy"}


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def percent(value: float) -> str:
    text = format(Decimal(str(value)) * 100, "f")
    return (text.rstrip("0").rstrip(".") if "." in text else text) + "%"


def sector(event: dict[str, Any]) -> str | None:
    tags = set(event.get("tags") or [])
    geo = bool(tags & _GEO)
    if geo and tags & (_EQUITY | _MACRO | _GENERAL):
        return "composite"
    if geo:
        return "geopolitics"
    if tags & _EQUITY:
        return "equities"
    if tags & _MACRO:
        return "macro"
    if tags & _GENERAL:
        return "general"
    return None


def _topic(title: str) -> str:
    # Threshold/date variants must not occupy all ten slots in a sector.
    words = re.findall(r"[a-z]+", title.lower())
    stop = set("will the a an by in on of at to be before after above below over under than "
               "january february march april may june july august september october november december".split())
    return " ".join(word for word in words if word not in stop)


def shortlist(snapshot: Snapshot) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reference = datetime.fromisoformat(snapshot.summary["generated_at"])
    moves = {
        str(row["id"]): row for key in ("spotlight", "volume_movers", "new_entries")
        for row in snapshot.trending.get(key, []) if isinstance(row, dict) and row.get("id")
    }
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in SECTORS}
    excluded: Counter[str] = Counter()
    for event in snapshot.events:
        key = sector(event)
        if not key:
            excluded["outside_sectors"] += 1
            continue
        volume, liquidity = number(event.get("volume24hr")), number(event.get("liquidity"))
        if (event.get("data_status") != "ok" or volume is None or volume < 2000
                or liquidity is None or liquidity < 1000
                or event.get("event_type") not in {"binary", "exclusive_multi", "independent_multi"}):
            excluded["quality"] += 1
            continue
        try:
            deadline = datetime.fromisoformat(str(event["end_date"]).replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= reference:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            excluded["expired_or_unknown_deadline"] += 1
            continue
        move = moves.get(str(event["id"]), {})
        change = number(move.get("basis_change")) if not move.get("leader_changed") else None
        groups[key].append({
            "id": str(event["id"]), "title": str(event["title"])[:300],
            "sector": key, "sector_label": SECTORS[key], "event_type": event["event_type"],
            "volume24hr": volume, "liquidity": liquidity, "end_date": event["end_date"],
            "leader": event.get("leader"), "leader_probability": number(event.get("leader_probability")),
            "change": change, "change_basis_at": snapshot.trending.get("basis_at") if move else None,
            "leader_changed": bool(move.get("leader_changed")),
            "topic_key": _topic(str(event["title"])),
            "days_to_end": max(0, (deadline - reference).total_seconds() / 86400),
        })
    candidates = []
    eligible = {key: len(rows) for key, rows in groups.items()}
    for rows in groups.values():
        if not rows:
            continue
        max_volume = max(math.log1p(row["volume24hr"]) for row in rows)
        max_liquidity = max(math.log1p(row["liquidity"]) for row in rows)
        for row in rows:
            row["score"] = round(
                .5 * math.log1p(row["volume24hr"]) / max_volume
                + .2 * math.log1p(row["liquidity"]) / max_liquidity
                + .2 * min(1, abs(row["change"] or 0) / .1)
                + .1 / (1 + row["days_to_end"] / 7), 6,
            )
        counts: Counter[str] = Counter()
        selected = []
        for row in sorted(rows, key=lambda item: (-item["score"], item["id"])):
            if counts[row["topic_key"]] >= 2:
                continue
            counts[row["topic_key"]] += 1
            selected.append(row)
            if len(selected) == 10:
                break
        candidates.extend(selected)
    return candidates, {
        "scanned": len(snapshot.events), "excluded": dict(excluded),
        "eligible_by_sector": eligible, "shortlisted": len(candidates),
        "movement_coverage": "공개 trending에 있는 동일 세대 이벤트만; 미관측은 변동 없음이 아님",
        "interest_proxy": "24시간 거래량·유동성; 고유 참여자 수나 검색량을 뜻하지 않음",
    }


def prepare_issue(candidate: dict[str, Any], detail: dict[str, Any], news: list[dict[str, Any]]) -> dict[str, Any]:
    if (detail.get("active") is not True or detail.get("closed") is not False
            or not str(detail.get("description") or "").strip() or not detail.get("slug")):
        raise SourceError(f"이벤트 {candidate['id']}의 진행 상태·설명이 불완전합니다")
    markets = []
    seen = set()
    for row in detail.get("markets", []):
        yes, no = number(row.get("yes_probability")), number(row.get("no_probability"))
        volume, liquidity = number(row.get("volume24hr")), number(row.get("liquidity"))
        if (row.get("active") is not True or row.get("closed") is not False
                or row.get("price_valid") is not True or row.get("price_warning")
                or yes is None or no is None or not 0 < yes < 1 or not 0 < no < 1
                or abs(yes + no - 1) > .02 or volume is None or volume <= 0
                or liquidity is None or liquidity <= 0 or not row.get("id") or not row.get("question")):
            continue
        market_id = str(row["id"])
        if market_id in seen:
            raise SourceError("중복된 개별 베팅 ID입니다")
        seen.add(market_id)
        markets.append({
            "id": market_id, "question": row["question"], "outcome_label": row.get("outcome_label"),
            "yes_probability": yes, "no_probability": no,
            "yes": percent(yes), "no": percent(no), "volume24hr": volume, "liquidity": liquidity,
        })
    if not markets:
        raise SourceError(f"이벤트 {candidate['id']}에 검증 가능한 개별 베팅이 없습니다")
    markets.sort(key=lambda row: (-row["volume24hr"], row["id"]))
    return {
        **candidate, "generation_id": detail["generation_id"],
        "description": str(detail["description"])[:12000],
        "markets": markets[:2], "valid_market_count": len(markets), "news": news,
        "source_url": "https://polymarket.com/event/" + quote(str(detail["slug"]), safe=""),
    }
