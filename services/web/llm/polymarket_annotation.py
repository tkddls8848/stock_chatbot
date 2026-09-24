"""예측 질문 여러 건에 한국어 요약·검색 키워드·세부 주제를 붙인다.

자연어 검색의 재료다. 검색하는 순간에는 LLM을 부르지 않으므로, 질문 낱말과
여기서 만든 낱말이 맞물려야 찾힌다. 그래서 요약 한 줄만이 아니라 한국어·영어
표기와 동의어를 키워드로 따로 받는다.

**확률은 받지 않는다.** 요약은 "무엇에 거는 질문인가"만 담아야 event가 닫힐
때까지 다시 쓸 수 있다. 확률은 수집 주기(4시간)마다 바뀌고, 모델은 확률의 방향을 뒤집어
쓴 전례가 있다(`dashboard/models.py`의 `title_probability`).

모델은 입력 번호(`n`)로만 질문을 가리킨다. event id를 받아 적게 하지 않는다.
계획서: `services/web/docs/polymarket-nl-search.md`
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.web.llm.backends import LLMBackend, LLMBackendError

SUMMARY_MIN_CHARS = 8
SUMMARY_MAX_CHARS = 80
KEYWORD_MAX_CHARS = 30
KEYWORD_MIN_COUNT = 3
KEYWORD_MAX_COUNT = 12
SUBTOPIC_MAX_CHARS = 20

# 요약에 확률·전망이 섞이면 버린다. 숫자 자체는 막지 않는다 — "2026년",
# "12월"처럼 질문의 내용인 숫자가 있다.
_PROBABILITY = re.compile(r"%|퍼센트|확률|가능성|우세|유력")

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "summary": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "subtopic": {"type": "string"},
                },
                "required": ["n", "summary", "keywords", "subtopic"],
            },
        }
    },
    "required": ["events"],
}
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "polymarket_annotation", "schema": RESPONSE_SCHEMA},
}


class PolymarketAnnotationError(RuntimeError):
    """배치 하나를 통째로 쓰지 못했을 때.

    `stop`이면 이번 실행의 나머지 배치도 부르지 않는다 — 할당량 소진이나 회로
    차단은 다음 배치에서도 똑같이 실패하고, 부를수록 예산 기록만 늘어난다.
    """

    def __init__(self, message: str, *, stop: bool = False):
        super().__init__(message)
        self.stop = stop


@dataclass
class AnnotationBatch:
    rows: dict[int, dict[str, Any]]
    dropped: int = 0
    reasons: list[str] = field(default_factory=list)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _parse_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("row is not an object")
    summary = _clean(row.get("summary"))
    if not SUMMARY_MIN_CHARS <= len(summary) <= SUMMARY_MAX_CHARS:
        raise ValueError(f"summary length {len(summary)}")
    if _PROBABILITY.search(summary):
        raise ValueError("summary mentions probability")
    raw_keywords = row.get("keywords")
    if not isinstance(raw_keywords, list):
        raise ValueError("keywords is not a list")
    keywords: list[str] = []
    for value in raw_keywords:
        keyword = _clean(value)
        if keyword and len(keyword) <= KEYWORD_MAX_CHARS and keyword.casefold() not in {
            item.casefold() for item in keywords
        }:
            keywords.append(keyword)
    if len(keywords) < KEYWORD_MIN_COUNT:
        raise ValueError(f"only {len(keywords)} usable keywords")
    subtopic = _clean(row.get("subtopic"))
    if not subtopic or len(subtopic) > SUBTOPIC_MAX_CHARS:
        raise ValueError(f"subtopic length {len(subtopic)}")
    return {"summary": summary, "keywords": keywords[:KEYWORD_MAX_COUNT], "subtopic": subtopic}


def parse_response(raw: str, valid_numbers: set[int]) -> AnnotationBatch:
    """응답을 검사한다. 형식이 틀린 **행**은 버리고 나머지를 살린다.

    배치 전체를 버리지 않는 것은 시장상황 보고서와 같은 판단이다. 25건 중 한 건의
    키워드가 모자라다고 나머지 24건의 호출 비용까지 버릴 이유가 없다. 버린
    질문은 주석이 없는 채로 남아 다음 주기의 대상이 된다.
    """
    text = raw.strip()
    # /no_think를 붙여도 빈 thinking 블록이 앞에 붙어 오는 응답이 있다.
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise PolymarketAnnotationError(f"response is not JSON: {error}") from error
    rows = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise PolymarketAnnotationError("response has no events list")

    batch = AnnotationBatch(rows={})
    for row in rows:
        number = row.get("n") if isinstance(row, dict) else None
        if not isinstance(number, int) or number not in valid_numbers:
            batch.dropped += 1
            batch.reasons.append(f"unknown n={number!r}")
            continue
        if number in batch.rows:
            # 같은 번호가 두 번 오면 어느 쪽이 맞는지 알 수 없다. 둘 다 버린다.
            del batch.rows[number]
            batch.dropped += 2
            batch.reasons.append(f"duplicate n={number}")
            valid_numbers = valid_numbers - {number}
            continue
        try:
            batch.rows[number] = _parse_row(row)
        except ValueError as error:
            batch.dropped += 1
            batch.reasons.append(f"n={number}: {error}")
    return batch


class PolymarketAnnotator:
    def __init__(self, backend: LLMBackend, prompt_file: Path, num_predict: int):
        self._backend = backend
        self._num_predict = num_predict
        self._prompt = prompt_file.read_text(encoding="utf-8")

    @property
    def backend(self) -> LLMBackend:
        return self._backend

    def annotate(self, events: list[dict[str, Any]]) -> AnnotationBatch:
        """`events`는 `{n, title, tags, description}` 목록이다(블로킹).

        재시도하지 않는다. 전송 계층 재시도는 `ResilientBackend`가 하고,
        형식이 틀린 배치는 다음 주기에 다시 대상이 된다 — 같은 실행 안에서
        다시 부르면 Neurons만 두 배로 나간다.
        """
        if not events:
            return AnnotationBatch(rows={})
        try:
            raw = self._backend.generate(
                system_prompt=self._prompt,
                user_prompt=json.dumps({"events": events}, ensure_ascii=False),
                max_tokens=self._num_predict,
                temperature=0.2,
                response_format=RESPONSE_FORMAT,
            )
        except LLMBackendError as error:
            stop = error.quota_exhausted or error.fatal or error.reason == "circuit_open"
            raise PolymarketAnnotationError(str(error), stop=stop) from error
        return parse_response(raw, {int(event["n"]) for event in events})
