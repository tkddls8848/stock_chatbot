"""현재 event/market 확률, 유형, 품질 상태 정규화."""

from __future__ import annotations

import json
import math
from typing import Any

from web.polymarket.dashboard.taxonomy import classify, extract_tags

PRICE_SUM_TOLERANCE = 0.05


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _array(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _binary_prices(market: dict[str, Any]) -> dict[str, Any]:
    outcomes = _array(market.get("outcomes"))
    prices = _array(market.get("outcomePrices"))
    result: dict[str, Any] = {
        "valid": False,
        "yes_probability": None,
        "no_probability": None,
        "raw_price_sum": None,
        "warning": None,
    }
    if outcomes is None or prices is None or len(outcomes) != 2 or len(prices) != 2:
        result["warning"] = "missing_binary_prices"
        return result
    labels = [str(value).strip().lower() for value in outcomes]
    if set(labels) != {"yes", "no"}:
        result["warning"] = "non_binary_outcomes"
        return result
    values = [_number(value) for value in prices]
    if any(value is None or not 0 <= value <= 1 for value in values):
        result["warning"] = "price_out_of_range"
        return result
    raw_sum = sum(value for value in values if value is not None)
    result["raw_price_sum"] = round(raw_sum, 8)
    if abs(raw_sum - 1.0) > PRICE_SUM_TOLERANCE or raw_sum <= 0:
        result["warning"] = "price_sum_invalid"
        return result
    normalized = [float(value) / raw_sum for value in values]
    by_label = dict(zip(labels, normalized, strict=True))
    result.update(
        {
            "valid": True,
            "yes_probability": round(by_label["yes"], 8),
            "no_probability": round(by_label["no"], 8),
        }
    )
    return result


def _market_detail(market: dict[str, Any]) -> dict[str, Any]:
    prices = _binary_prices(market)
    return {
        "id": str(market.get("id") or market.get("conditionId") or ""),
        "question": str(market.get("question") or market.get("groupItemTitle") or ""),
        "outcome_label": str(market.get("groupItemTitle") or market.get("question") or ""),
        "slug": str(market.get("slug") or ""),
        "active": market.get("active"),
        "closed": market.get("closed"),
        "liquidity": _number(market.get("liquidityNum", market.get("liquidity"))),
        "volume24hr": _number(market.get("volume24hr")),
        "yes_probability": prices["yes_probability"],
        "no_probability": prices["no_probability"],
        "raw_price_sum": prices["raw_price_sum"],
        "price_warning": prices["warning"],
        "price_valid": prices["valid"],
    }


def _event_type(event: dict[str, Any], market_count: int) -> str:
    if market_count == 1:
        return "binary"
    neg_risk = event.get("negRisk")
    if neg_risk is True:
        return "exclusive_multi"
    if neg_risk is False:
        return "independent_multi"
    return "unknown_multi"


def _liquidity(event: dict[str, Any], markets: list[dict[str, Any]]) -> tuple[float | None, str]:
    event_value = _number(event.get("liquidity"))
    if event_value is not None and event_value >= 0:
        return event_value, "event"
    child_values = [market["liquidity"] for market in markets if market.get("active") is not False]
    if child_values and all(value is not None and value >= 0 for value in child_values):
        return sum(float(value) for value in child_values), "markets_sum"
    return None, "missing"


def _liquidity_status(value: float | None, low_liquidity: float) -> str:
    if value is None:
        return "missing"
    if value == 0:
        return "zero"
    if value < low_liquidity:
        return "low"
    return "ok"


def _consensus(event_type: str, markets: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "price_status": "unavailable",
        "leader": None,
        "leader_probability": None,
        "runner_up_probability": None,
        "leader_margin": None,
        "raw_yes_sum": None,
        "warnings": [],
    }
    if event_type == "binary":
        if len(markets) != 1 or not markets[0]["price_valid"]:
            result["warnings"] = [markets[0]["price_warning"]] if markets else ["missing_market"]
            return result
        market = markets[0]
        yes = market["yes_probability"]
        no = market["no_probability"]
        leader = "Yes" if yes >= no else "No"
        result.update(
            {
                "price_status": "ok",
                "leader": leader,
                "leader_probability": max(yes, no),
                "runner_up_probability": min(yes, no),
                "leader_margin": abs(yes - no),
            }
        )
        return result

    valid = [market for market in markets if market["price_valid"]]
    if event_type == "exclusive_multi":
        if len(valid) != len(markets) or not markets:
            result["warnings"] = ["missing_child_price"]
            return result
        raw_sum = sum(float(market["yes_probability"]) for market in markets)
        result["raw_yes_sum"] = round(raw_sum, 8)
        if abs(raw_sum - 1.0) > PRICE_SUM_TOLERANCE or raw_sum <= 0:
            result["warnings"] = ["exclusive_price_sum_invalid"]
            return result
        ranked = sorted(
            (
                (market["outcome_label"] or market["question"] or market["id"],
                 float(market["yes_probability"]) / raw_sum)
                for market in markets
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        result.update(
            {
                "price_status": "ok",
                "leader": ranked[0][0],
                "leader_probability": round(ranked[0][1], 8),
                "runner_up_probability": round(ranked[1][1], 8) if len(ranked) > 1 else None,
                "leader_margin": round(ranked[0][1] - ranked[1][1], 8)
                if len(ranked) > 1 else None,
            }
        )
        return result

    if event_type == "independent_multi" and valid:
        result["price_status"] = "ok"
        if len(valid) != len(markets):
            result["warnings"] = ["partial_child_prices"]
        return result
    result["warnings"] = ["unknown_multi_type" if event_type == "unknown_multi" else "missing_child_price"]
    return result


def normalize_event(
    event: dict[str, Any],
    *,
    identity: str,
    low_liquidity: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_markets = event.get("markets") or []
    markets = [_market_detail(item) for item in raw_markets if isinstance(item, dict)]
    event_type = _event_type(event, len(markets))
    consensus = _consensus(event_type, markets)
    liquidity, liquidity_source = _liquidity(event, markets)
    liquidity_status = _liquidity_status(liquidity, low_liquidity)
    if consensus["price_status"] != "ok":
        data_status = "unavailable"
    elif liquidity_status == "missing":
        data_status = "liquidity_missing"
    elif liquidity_status == "zero":
        data_status = "no_liquidity"
    elif liquidity_status == "low":
        data_status = "low_liquidity"
    else:
        data_status = "ok"
    taxonomy = classify(extract_tags(event))
    # compact는 event 전부가 current.json 한 파일에 들어가므로 **한 필드의 크기가
    # event 수만큼 곱해진다.** 실측 21,877건에서 필드 하나가 20 B면 0.42 MiB다.
    # 그래서 여기에는 목록·순위·필터·정렬이 실제로 읽는 것만 둔다. 나머지는
    # detail로 내린다 — detail은 byte-addressed라 한 행만 seek해서 읽는다.
    # 필드를 여기 추가하기 전에 tests/polymarket_manifest_size_probe.py로
    # 16 MiB 상한(docs/polymarket-dashboard.md 7-4)에 여유가 있는지 먼저 잰다.
    common = {
        "id": identity,
        "title": str(event.get("title") or event.get("question") or "제목 없음"),
        "category": taxonomy["category"],
        "category_label": taxonomy["category_label"],
        "tags": taxonomy["tags"],
        "regions": taxonomy["regions"],
        "event_type": event_type,
        "data_status": data_status,
        "price_status": consensus["price_status"],
        "liquidity_status": liquidity_status,
        "liquidity": liquidity,
        "volume24hr": _number(event.get("volume24hr")),
        "end_date": event.get("endDate"),
        "leader": consensus["leader"],
        "leader_probability": consensus["leader_probability"],
        "leader_margin": consensus["leader_margin"],
    }
    compact = dict(common)
    # detail 전용. 어느 것도 목록 화면이 읽지 않는다 — slug는 상세의 Polymarket
    # 링크(eventUrl)가, 나머지는 상세 본문과 수집 집계가 쓴다.
    detail = {
        **common,
        "slug": str(event.get("slug") or ""),
        "category_reason": taxonomy["category_reason"],
        "system_tags": taxonomy["system_tags"],
        "liquidity_source": liquidity_source,
        "volume": _number(event.get("volume")),
        "market_count": len(markets),
        "runner_up_probability": consensus["runner_up_probability"],
        "description": str(event.get("description") or ""),
        "image": str(event.get("image") or event.get("icon") or ""),
        "restricted": event.get("restricted"),
        "active": event.get("active"),
        "closed": event.get("closed"),
        "raw_yes_sum": consensus["raw_yes_sum"],
        "warnings": consensus["warnings"],
        "markets": markets,
    }
    return compact, detail
