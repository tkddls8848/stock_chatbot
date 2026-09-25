"""현재 event/market 확률, 유형, 품질 상태 정규화."""

from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any

from services.web.polymarket.dashboard.taxonomy import classify, extract_tags

PRICE_SUM_TOLERANCE = 0.05

# 예/아니오로 읽는 라벨. 나머지 이름은 전부 **원문 그대로의 두 선택지**다.
_YES_LABELS = frozenset({"yes", "예"})
_NO_LABELS = frozenset({"no", "아니오"})


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
    """결과가 둘인 시장의 가격을 읽는다. **라벨이 Yes/No가 아니어도 읽는다.**

    원문은 두 결과의 이름을 시장마다 다르게 준다 — `Up`/`Down`(5분 가격),
    팀 이름(`Team A`/`Team B`), `Over`/`Under`. 예전에는 이름이 Yes/No가
    아니면 가격을 통째로 버렸고, 그래서 가격이 멀쩡히 붙어 있는 시장이
    "확률 읽기 불가"로 떨어졌다(2026-09-25 실측: 열린 event 19,054건 중
    13,522건이 unavailable이고 그중 binary 4,206건의 대부분이 이 경우다).

    `yes_probability`는 **첫 번째 결과**의 확률이고 `no_probability`는 두 번째
    결과의 확률이다. 이름이 Yes/No일 때만 순서가 뒤집혀 와도 이름으로 맞춘다 —
    필드 이름과 뜻(shorts·트렌드가 HTTP로 읽는 계약)은 그대로 두고, 그 확률이
    어느 쪽 이름인지를 `yes_label`·`no_label`이 함께 알려 준다.
    """
    outcomes = _array(market.get("outcomes"))
    prices = _array(market.get("outcomePrices"))
    result: dict[str, Any] = {
        "valid": False,
        "yes_probability": None,
        "no_probability": None,
        "yes_label": None,
        "no_label": None,
        "raw_price_sum": None,
        "warning": None,
    }
    if outcomes is None or prices is None or len(outcomes) != 2 or len(prices) != 2:
        result["warning"] = "missing_binary_prices"
        return result
    labels = [str(value).strip() for value in outcomes]
    lowered = [label.lower() for label in labels]
    if not all(labels) or lowered[0] == lowered[1]:
        # 두 쪽을 구분할 이름이 없으면 어느 확률이 어느 쪽인지 적을 수 없다.
        result["warning"] = "unusable_outcome_labels"
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
    yes_side = next((index for index, label in enumerate(lowered) if label in _YES_LABELS), None)
    no_side = next((index for index, label in enumerate(lowered) if label in _NO_LABELS), None)
    if yes_side is not None and no_side is not None:
        # 예/아니오 시장은 순서가 뒤집혀 와도 이름으로 맞추고, 화면·API가 오래
        # 써 온 "Yes"/"No" 표기를 그대로 돌려준다.
        first, yes_label, no_label = yes_side, "Yes", "No"
    else:
        first, yes_label, no_label = 0, labels[0], labels[1]
    result.update(
        {
            "valid": True,
            "yes_probability": round(normalized[first], 8),
            "no_probability": round(normalized[1 - first], 8),
            "yes_label": yes_label,
            "no_label": no_label,
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
        # 화면이 "예 52% · 아니오 48%"를 쓸지 "Up 52% · Down 48%"를 쓸지 정하는 값.
        "yes_label": prices["yes_label"],
        "no_label": prices["no_label"],
        "raw_price_sum": prices["raw_price_sum"],
        "price_warning": prices["warning"],
        "price_valid": prices["valid"],
    }


def _open_child(market: dict[str, Any]) -> bool:
    """컨센서스 계산에 넣을 자식인지.

    **닫힌 자식의 가격은 예측이 아니라 결과(0 또는 1)다.** 다지선다 event는
    자식이 하나씩 끝나도 event 자체는 열려 있어서, 끝난 자식을 그대로 섞으면
    남은 후보들의 구성비가 뜻을 잃는다. `_liquidity`와 같은 기준으로 본다.
    """
    return market.get("closed") is not True and market.get("active") is not False


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
    """event 하나의 컨센서스. **가격을 읽을 수 없을 때만 unavailable이다.**

    다지선다는 자식 하나가 닫혔거나 가격이 없다고 해서 event를 통째로 버리지
    않는다. 그 자식을 빼고 남은 활성 자식으로 계산하고, 무엇을 뺐는지
    `warnings`에 적는다. 활성 자식에 가격이 하나도 없을 때만 unavailable이다.
    배타적 다지선다에서 남은 구성비가 1에서 크게 벗어나면 100% 구성비를 만들지
    않는 기존 정책은 그대로다 — 그 합은 후보가 빠졌다는 뜻이다.
    """
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
        # leader는 **원문 라벨 그대로**다. 예/아니오 시장이면 "Yes"/"No",
        # 두 선택지에 이름이 있으면 그 이름("Up"·팀 이름)이다.
        leader = market["yes_label"] if yes >= no else market["no_label"]
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

    active = [market for market in markets if _open_child(market)]
    priced = [market for market in active if market["price_valid"]]
    notes: list[str] = []
    if len(active) != len(markets):
        notes.append("closed_children_excluded")
    if len(priced) != len(active):
        notes.append("partial_child_prices")

    if event_type == "exclusive_multi":
        if not priced:
            result["warnings"] = [*notes, "missing_child_price"]
            return result
        raw_sum = sum(float(market["yes_probability"]) for market in priced)
        result["raw_yes_sum"] = round(raw_sum, 8)
        if abs(raw_sum - 1.0) > PRICE_SUM_TOLERANCE or raw_sum <= 0:
            result["warnings"] = [*notes, "exclusive_price_sum_invalid"]
            return result
        ranked = sorted(
            (
                (market["outcome_label"] or market["question"] or market["id"],
                 float(market["yes_probability"]) / raw_sum)
                for market in priced
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
                "warnings": notes,
            }
        )
        return result

    if event_type == "independent_multi":
        if not priced:
            result["warnings"] = [*notes, "missing_child_price"]
            return result
        result["price_status"] = "ok"
        result["warnings"] = notes
        return result
    result["warnings"] = ["unknown_multi_type"]
    return result


def title_probability(event: dict[str, Any]) -> float | None:
    """binary event에서 **제목이 사실로 판명될 확률**을 돌려준다.

    `leader_probability`는 항상 우세한 쪽의 값이라 부호가 없다. leader가
    "No"인 0.82는 "일어날 확률 0.82"가 아니라 "일어나지 않을 확률 0.82"다.

    모델에게 이 조합을 맡기면 셋 중 둘꼴로 방향을 뒤집어 쓴다(실측
    2026-09-01). 힌트를 더 줘도 제목 표현에 앵커링해서 "제재 완화 가능성
    74%"라고 쓴다 — 74%는 완화되지 **않을** 확률인데도.

    그래서 숫자를 읽는 방향에 맞춰 정규화한다. 줄글 브리프는 이 값을 모델에게
    보내고, 트렌드 one-shot은 이 값으로 주기 간 이동을 잰다 — **부호가 안정된
    값이라야 뺄셈이 뜻을 가진다.** leader가 Yes에서 No로 넘어간 주기에
    `leader_probability`를 그대로 빼면 이동이 0에 가깝게 나오는데, 실제로는
    그 event가 반대편으로 넘어간 순간이다.

    **두 선택지에 이름이 붙은 binary event는 제목이 명제가 아니라서 None이다.**
    "Bitcoin Up or Down"·"Team A vs. Team B"의 제목은 참·거짓을 물은 것이
    아니므로 "제목이 사실일 확률"이라는 값 자체가 없다. 이런 event는 다지선다와
    같은 모양(`leader`는 앞선 선택지 이름, `leader_probability`는 그 확률)이므로
    호출자는 그 둘을 함께 읽는다. 여기서 `leader_probability`를 그냥 돌려주면
    1위가 `Up`에서 `Down`으로 넘어간 주기에 뺄셈이 0에 가깝게 나온다 — 바로 위
    문단이 경계하는 그 오류다.

    이 함수는 숫자만 만지므로 `services.web.llm`을 부르지 않는 호출자도 쓸 수 있도록
    여기(정규화 계층)에 둔다.
    """
    probability = _number(event.get("leader_probability"))
    if probability is None:
        return None
    leader = str(event.get("leader") or "").strip().lower()
    if leader in _NO_LABELS:
        return round(1.0 - probability, 4)
    if leader not in _YES_LABELS and event.get("event_type") == "binary":
        return None
    return probability


def ends_before(event: dict[str, Any], horizon: datetime) -> bool:
    """정규화된 event의 마감이 `horizon` 이전이면 True.

    **결과 확정을 향한 수렴은 컨센서스의 이동이 아니다.** 5분짜리 가격 방향,
    오늘 밤 경기, 오늘 기온처럼 곧 끝나는 시장의 확률은 0·1로 빨려 들어가는
    것이 당연하다. 그래서 "가장 굳은·팽팽한" 확률 순위(`repository.summary`)와
    그날 이동 추적(`trending`)이 **같은 기준으로** 이 event들을 뺀다. 마감을
    읽지 못하면 걸러낼 근거가 없어 False다.

    시각은 호출자가 `horizon`으로 넘긴다 — 이 계층은 숫자만 만지고 `clock`을
    부르지 않는다(`title_probability`와 같은 이유).
    """
    raw = event.get("end_date")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        end = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if end.tzinfo is None:
        return False
    return end < horizon


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
