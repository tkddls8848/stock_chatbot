"""뷰 검증이 읽는 컨센서스 — 공개 웹의 GET API를 HTTP로 읽고 모델에 보일 꼴로 만든다.

봇은 웹 코드를 import하지 않는다(`code_guide.md`의 「구조」). 수집·검색·주석은 웹 도메인의 것이고, 여기서는
쇼츠와 `/web`이 하듯 **이미 공개된 `/api/forecast/*`만 읽는다** — 이 기능 때문에 웹에 라우트를 더하지 않는다.

- 찾기: `/api/forecast/events?q=`(웹의 자연어 검색, LLM 없음)
- 지금 참여가 몰린 질문: `/api/forecast/events?category=…&sort=volume24hr`
- 선택지와 확률: `/api/forecast/events/{id}`

후보 한 칸의 꼴(`build_row`)과 메시지에 세울 선택지(`shown_options`)는 웹 질의응답(`services/web/polymarket/ask.py`)의
것을 옮겼다. **모델 입력에는 확률 숫자를 넣지 않고 등급(`outlook`)만 넣는다.**
"""

from __future__ import annotations

import os
import string
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import requests

from services.telegram_bot.core.config import (
    VIEW_CHECK_API_BASE_URL,
    VIEW_CHECK_API_TIMEOUT_SECONDS,
    VIEW_CHECK_EXCLUDED_CATEGORIES,
    VIEW_CHECK_MIN_LIQUIDITY,
    VIEW_CHECK_MIN_VOLUME,
    VIEW_CHECK_OPTIONS_PER_EVENT,
)

_TYPE = {"binary": "이지선다", "exclusive_multi": "다지선다(하나만 정답)",
         "independent_multi": "여러 질문 묶음", "unknown_multi": "유형 미상"}
# 여러 질문 묶음(사다리)에서 "갈리는 칸"으로 보는 범위. 밖의 칸은 거의 정해진 칸이다.
_BAND = (0.03, 0.97)
# 다지선다의 셋째 줄은 이 확률 이상일 때만 세운다.
_THIRD_MIN = 0.01
_LABEL_MAX_CHARS = 80
_ALNUM = string.ascii_letters + string.digits
# 검색 한 번에 읽는 쪽수. 확률을 읽을 수 없는 질문을 뺀 뒤에도 후보가 남게 넉넉히 읽는다.
_PAGE_SIZE = 25


class ConsensusUnavailable(RuntimeError):
    """웹이 응답하지 않거나 수집분이 없다. 검증을 시작할 수 없다."""


@dataclass
class Row:
    """후보 하나: 모델에 보이는 꼴과, 결과를 조립할 때 쓰는 원자료."""

    event: dict[str, Any]
    summary: str | None
    markets: list[dict[str, Any]]
    options: dict[int, str]  # 모델의 선택지 번호 m → market id(이지선다는 "yes"·"no")
    model: dict[str, Any]


class Consensus:
    """웹 공개 API 읽기. 블로킹이므로 스레드에서 부른다."""

    def __init__(self, fetch: Callable[..., Any] = requests.get, base_url: str = VIEW_CHECK_API_BASE_URL):
        self._fetch = fetch
        self._base_url = base_url
        # 마지막으로 읽은 수집분. 결과에 "어느 수집분으로 판정했는가"를 남긴다.
        self.generation_id: str | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """200이면 본문, 404면 None. 그 밖은 `ConsensusUnavailable`."""
        try:
            response = self._fetch(f"{self._base_url}{path}", params=params, timeout=VIEW_CHECK_API_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            raise ConsensusUnavailable(f"웹 응답 없음: {type(error).__name__}") from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ConsensusUnavailable(f"웹 응답 {response.status_code}")
        try:
            payload = response.json()
        except ValueError as error:
            raise ConsensusUnavailable("웹 응답이 JSON이 아니다") from error
        if not isinstance(payload, dict):
            raise ConsensusUnavailable("웹 응답 형식이 다르다")
        return payload

    def _events(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        payload = self._get("/api/forecast/events", {**params, "page_size": _PAGE_SIZE}) or {}
        self.generation_id = payload.get("generation_id") or self.generation_id
        return [event for event in payload.get("events") or []
                if isinstance(event, dict) and event.get("price_status") == "ok"]

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        """검색어에 맞는 질문(관련도순). 낱말 절반이 맞아야 잡히므로 검색어는 짧아야 한다."""
        return self._events({"q": query})[:limit]

    def busiest(self, category: str) -> list[dict[str, Any]]:
        """그 분야에서 지금 참여가 큰 질문(참여 규모순)."""
        return self._events({"category": category, "sort": "volume24hr"})

    def detail(self, event_id: str) -> dict[str, Any] | None:
        """질문 하나의 선택지와 확률. 지금 수집분에 없으면(닫혔으면) None."""
        return self._get(f"/api/forecast/events/{event_id}")


def outlook(probability: float) -> str:
    """확률을 모델 입력용 등급으로. 숫자를 받으면 모델이 그것을 다른 질문에 옮겨 붙인다."""
    if probability < 0.2:
        return "매우 낮음"
    if probability < 0.4:
        return "낮음"
    if probability <= 0.6:
        return "엇갈림"
    if probability <= 0.8:
        return "높음"
    return "매우 높음"


def volume(event: dict[str, Any]) -> float:
    value = event.get("volume24hr")
    return float(value) if isinstance(value, (int, float)) else 0.0


def meaningful(event: dict[str, Any], horizon: datetime) -> bool:
    """판정 근거로 삼을 만큼 참여가 있고, 결과 확정을 향해 수렴하는 중이 아닌 질문.

    곧 끝나는 질문의 확률은 0·1로 빨려 들어간다 — 컨센서스의 이동이 아니다. 마감을 읽지 못하면 걸러낼 근거가
    없어 남긴다.
    """
    liquidity = event.get("liquidity")
    enough = (volume(event) >= VIEW_CHECK_MIN_VOLUME
              or (isinstance(liquidity, (int, float)) and liquidity >= VIEW_CHECK_MIN_LIQUIDITY))
    if not enough or event.get("category") in VIEW_CHECK_EXCLUDED_CATEGORIES:
        return False
    try:
        end = datetime.fromisoformat(str(event.get("end_date") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return end.tzinfo is None or end >= horizon


def _probability(market: dict[str, Any]) -> float:
    return float(market["yes_probability"])


def _no(market: dict[str, Any]) -> float:
    value = market.get("no_probability")
    return float(value) if isinstance(value, (int, float)) else 1.0 - _probability(market)


def _label(market: dict[str, Any]) -> str:
    label = " ".join(str(market.get("outcome_label") or market.get("question") or "").split())
    return label[:_LABEL_MAX_CHARS]


def _question_template(markets: list[dict[str, Any]]) -> str | None:
    """칸 질문들의 공통 꼴. 앞뒤 공통 부분 사이를 ___로 둔다. 공통 부분이 짧으면 꼴이 아니라 None."""
    questions = [" ".join(str(market.get("question") or "").split()) for market in markets]
    if len(questions) < 2 or not all(questions):
        return None
    prefix = os.path.commonprefix(questions)
    suffix = os.path.commonprefix([question[len(prefix):][::-1] for question in questions])[::-1]
    # 낱말 가운데에서 자르지 않는다 — "$160"·"$40"의 끝 "0"은 공통 꼴이 아니다.
    prefix, suffix = prefix.rstrip(_ALNUM), suffix.lstrip(_ALNUM)
    if len(prefix.strip()) + len(suffix.strip()) < 10:
        return None
    return (prefix + "___" + suffix)[:200]


def priced_markets(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """확률을 쓸 수 있는 선택지. 열리지 않은 칸(`active: False`)은 뺀다 — 자리채움 칸이 기본값 50%로 들어 있다."""
    return [
        market for market in detail.get("markets") or []
        if isinstance(market, dict) and not market.get("closed") and market.get("active") is not False
        and market.get("price_valid")
        and isinstance(market.get("yes_probability"), (int, float))
    ]


def _binary(event: dict[str, Any], markets: list[dict[str, Any]]) -> bool:
    return event.get("event_type") == "binary" and len(markets) == 1


def _participation(value: float) -> str:
    return "큼" if value >= 100_000 else "보통" if value >= 5_000 else "작음"


def build_row(number: int, event: dict[str, Any], summary: str | None, markets: list[dict[str, Any]]) -> Row:
    """모델 입력 한 칸. 확률 숫자는 넣지 않고 등급만 넣는다."""
    if _binary(event, markets):
        market = markets[0]
        shown = [{"m": 1, "label": "예", "outlook": outlook(_probability(market))},
                 {"m": 2, "label": "아니오", "outlook": outlook(_no(market))}]
        options = {1: "yes", 2: "no"}
    else:
        ranked = sorted(markets, key=_probability, reverse=True)[:VIEW_CHECK_OPTIONS_PER_EVENT]
        shown = [{"m": m, "label": _label(market), "outlook": outlook(_probability(market))}
                 for m, market in enumerate(ranked, start=1)]
        options = {m: str(market.get("id")) for m, market in enumerate(ranked, start=1)}
    model = {"n": number, "title": str(event.get("title") or ""), "summary": summary,
             "type": _TYPE.get(str(event.get("event_type")), "유형 미상"),
             "ends": str(event.get("end_date") or "")[:10],
             "participation": _participation(volume(event)), "options": shown}
    if not summary:
        del model["summary"]
    # 여러 질문 묶음은 칸마다 따로 묻는 질문이라 짧은 이름("November 7")만 주면 모델이 등급을 거꾸로 읽는다.
    # 칸의 질문 꼴을 한 번만 준다("… continues through ___?").
    if event.get("event_type") == "independent_multi" and (each := _question_template(markets)):
        model["each"] = each
    return Row(event, summary, markets, options, model)


def shown_options(event: dict[str, Any], markets: list[dict[str, Any]], focus: set[str]) -> list[dict[str, Any]]:
    """메시지에 세울 선택지와 확률. 모델이 짚은 선택지(`focus`)는 규칙 밖이어도 보인다."""
    if _binary(event, markets):
        sides = {"yes": _probability(markets[0]), "no": _no(markets[0])}
        major = "yes" if sides["yes"] >= sides["no"] else "no"
        keys = [major] + [side for side in ("yes", "no") if side != major and side in focus]
        return [{"label": "예" if side == "yes" else "아니오", "probability": round(sides[side], 4),
                 "focus": side in focus} for side in keys]
    ranked = sorted(markets, key=_probability, reverse=True)
    if event.get("event_type") == "independent_multi":
        band = [market for market in ranked if _BAND[0] <= _probability(market) <= _BAND[1]]
        if len(band) >= 3:
            middle = min(band[1:-1], key=lambda market: abs(_probability(market) - 0.5))
            chosen = [band[0], middle, band[-1]]
        else:
            chosen = sorted(ranked, key=lambda market: abs(_probability(market) - 0.5))[:3]
    else:
        chosen = ranked[:2] + [market for market in ranked[2:3] if _probability(market) >= _THIRD_MIN]
    picked = {str(market.get("id")) for market in chosen}
    chosen += [market for market in ranked if str(market.get("id")) in focus and str(market.get("id")) not in picked]
    chosen.sort(key=_probability, reverse=True)
    return [{"label": _label(market), "probability": round(_probability(market), 4),
             "focus": str(market.get("id")) in focus} for market in chosen]


def option_probabilities(event: dict[str, Any], markets: list[dict[str, Any]]) -> dict[str, float]:
    """선택지 이름 → 지금 확률. 이름은 `shown_options`가 내는 것과 같다 — 검증 때 보인 선택지를 나중 수집분에서
    같은 이름으로 다시 찾는다."""
    if _binary(event, markets):
        return {"예": round(_probability(markets[0]), 4), "아니오": round(_no(markets[0]), 4)}
    return {_label(market): round(_probability(market), 4) for market in markets}
