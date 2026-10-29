"""개별 이벤트의 수치 선별과 원고에 사용할 베팅 사실. 외부 모델을 호출하지 않는다."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import math
import re
from typing import Any

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
        # 영상은 제작 뒤 검수를 거쳐 올라가고 며칠 동안 보인다. 이틀 안에 판정되는 질문("S&P 500이 오늘 750달러
        # 위로 마감할까")은 보는 사람에게 이미 끝난 일이고, 98% 이상으로 거의 정해진 질문은 숫자가 아무것도
        # 알려 주지 않는다(독립 검토 2026-10-08: 당일 마감 99.95%·99.9% 질문이 한 장면을 차지했다).
        if (deadline - reference).total_seconds() < 2 * 86400:
            excluded["closes_within_two_days"] += 1
            continue
        if (number(event.get("leader_probability")) or 0) >= .98:
            excluded["near_certain"] += 1
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
        "interest_proxy": "24시간 참여 규모·유동성; 고유 참여자 수나 검색량을 뜻하지 않음",
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
    return {
        **candidate, "generation_id": detail["generation_id"],
        "description": str(detail["description"])[:12000],
        "markets": shown_markets(candidate["event_type"], markets, _reference_year(candidate)),
        "valid_market_count": len(markets), "news": news,
    }


# 화면에 세우는 선택지 수의 상한. 목록 화면은 이슈 넷까지 세 줄이 들어간다(`render._list_layout`).
MAX_SHOWN = 3
# 셋째 줄은 이 확률 이상일 때만 세운다. "연준 2026년 인하 횟수"의 셋째(2회 0.15%)처럼 숫자가 아무것도
# 말하지 않는 줄로 자리를 채우지 않는다.
_THIRD_MIN = .01
# 나머지를 묶은 선택지. 무엇을 가리키는지 이름으로 말할 수 없어 셋째로 세우지 않는다.
_CATCH_ALL = {"other", "others"}


def _by_probability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 참여 규모 순으로 고르면 다지선다에서 거래가 몰리는 희박한 선택지가 먼저 뽑혀(실측 2026-09-25 연준 10월:
    # "50bp 인하 0.25%"·"25bp 인하 0.45%"만 나오고 유력한 동결이 빠졌다) 핵심이 사라진다.
    return sorted(rows, key=lambda row: (-row["yes_probability"], -row["volume24hr"], row["id"]))


def shown_markets(event_type: str, markets: list[dict[str, Any]], year: int | None = None) -> list[dict[str, Any]]:
    """화면에 세울 선택지. 질문 유형마다 고르는 법이 다르다(운영자 결정 2026-10-29, `docs/option-display-plan.md`).

    - 이지선다: 예·아니오 중 확률이 높은 답 하나. 아니오가 높으면 `side`가 "no"다(실측: 호르무즈 통행 정상화
      '예' 11.5%가 화면을 차지해, 많은 사람이 고른 답이 보이지 않았다).
    - 다지선다: 확률순 상위 셋. 셋째가 1% 미만이거나 묶음 선택지면 둘.
    - 중복선택: 기한·가격 사다리면 3~97% 칸에서 가장 높은 칸·50%에 가장 가까운 칸·가장 낮은 칸을 사다리
      순서로. 확률순 상위 둘은 거의 확정된 칸 둘이 되어(이스라엘·이란 휴전 99.6%·95.5%) 기한에 따라 갈리는
      모습이 빠졌다. 사다리로 읽히지 않으면 다지선다처럼 고른다.
    """
    if event_type == "binary":
        row = _by_probability(markets)[0]
        return [{**row, "side": "no" if row["no_probability"] > row["yes_probability"] else "yes"}]
    if event_type == "independent_multi":
        ladder = _ladder(markets, year)
        if ladder is not None:
            return ladder
    ranked = _by_probability(markets)
    head = ranked[:2]
    third = next((row for row in ranked[2:] if str(row.get("outcome_label") or "").strip().casefold()
                  not in _CATCH_ALL), None)
    if third and third["yes_probability"] >= _THIRD_MIN:
        head.append(third)
    return head


_MONTH_NUMBER = {name: number for number, name in enumerate(
    ("january february march april may june july august september october november december").split(), 1)}
_DATE = re.compile(r"\b(" + "|".join(_MONTH_NUMBER) + r")\s+(\d{1,2})(?:,?\s+(\d{4}))?", re.IGNORECASE)
_PRICE = re.compile(r"([↑↓]|above|below|over|under)\s*\$?\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def _rung(label: str, year: int | None) -> tuple[str, float] | None:
    """사다리 칸의 (묶음, 순서 값). 기한은 날짜, 가격은 방향별 금액이다. 못 읽으면 None."""
    date = _DATE.search(label)
    if date:
        month, day = _MONTH_NUMBER[date[1].lower()], int(date[2])
        return "date", (int(date[3]) if date[3] else (year or 0)) * 10000 + month * 100 + day
    price = _PRICE.search(label)
    if price:
        direction = "up" if price[1].lower() in {"↑", "above", "over"} else "down"
        return direction, float(price[2].replace(",", ""))
    return None


def _monotone(rows: list[dict[str, Any]], keys: dict[str, tuple[str, float]]) -> bool:
    """같은 묶음 안에서 순서대로 확률이 한 방향으로만 움직이는가."""
    for group in {keys[row["id"]][0] for row in rows}:
        values = [row["yes_probability"] for row in sorted(rows, key=lambda row: keys[row["id"]][1])
                  if keys[row["id"]][0] == group]
        pairs = list(zip(values, values[1:]))
        if not (all(a >= b for a, b in pairs) or all(a <= b for a, b in pairs)):
            return False
    return True


def _ladder(markets: list[dict[str, Any]], year: int | None) -> list[dict[str, Any]] | None:
    keys = {}
    for row in markets:
        key = _rung(str(row.get("outcome_label") or row["question"]), year)
        if key is None:
            return None
        keys[row["id"]] = key
    if len(markets) <= MAX_SHOWN:
        pool = list(markets)
    else:
        pool = [row for row in markets if .03 <= row["yes_probability"] <= .97] or _by_probability(markets)[:1]
    while True:
        if len(pool) <= MAX_SHOWN:
            picked = pool
        else:
            ranked = _by_probability(pool)
            middle = min(pool, key=lambda row: (abs(row["yes_probability"] - .5), -row["volume24hr"], row["id"]))
            picked = list({row["id"]: row for row in (ranked[0], middle, ranked[-1])}.values())
        # 순서상 확률이 뒤집힌 칸(휴전 10/30 50.25% < 10/31 95.5%)은 사다리를 거꾸로 보이게 한다.
        # 고른 칸 중 참여 규모가 가장 작은 칸을 버리고 다시 고른다.
        if len(picked) <= 2 or _monotone(picked, keys):
            break
        thinnest = min(picked, key=lambda row: (row["volume24hr"], row["id"]))
        pool = [row for row in pool if row["id"] != thinnest["id"]]
    # 사다리 순서로 세운다: 기한은 이른 날부터, 가격은 높은 값부터.
    return sorted(picked, key=lambda row: (keys[row["id"]][0] != "date", keys[row["id"]][1] * (
        1 if keys[row["id"]][0] == "date" else -1)))


def _reference_year(candidate: dict[str, Any]) -> int | None:
    """연도 없는 기한("October 31")을 읽을 기준 연도. 제작 시점 = 종료 예정 − 남은 날."""
    try:
        end = datetime.fromisoformat(str(candidate["end_date"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None
    return (end - timedelta(days=candidate.get("days_to_end") or 0)).year
