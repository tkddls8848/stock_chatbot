"""자연어 질문에 맞는 예측 질문을 후보 중에서 고르고 숫자 없는 해설을 받는다(`/api/forecast/ask`).

모델은 입력 번호로만 가리킨다 — 질문은 `n`, 선택지는 `m`. event id와 확률은 받지 않는다. 확률 대신
줄글 브리프의 등급(`outlook`)만 받고, 화면의 숫자는 서버(`polymarket/ask.py`)가 detail에서 붙인다.
줄글 브리프가 같은 이유로 그렇게 바뀌었다 — 숫자를 받은 모델이 다른 질문의 확률을 옮겨 붙였다
(2026-10-03). 해설의 숫자·출처 이름 검사도 줄글 브리프의 것(`has_model_probability`·`FORBIDDEN_COPY`)을
그대로 쓴다 — 공개 문구 규칙은 한 곳에서 바뀌어야 한다.

호출은 질문당 한 번이고, 반려되면 한 번만 다시 부른다. 두 번째도 해설만 틀렸으면 고른 질문은 살리고
해설 없이 낸다 — 사실 줄은 서버가 쓰므로 해설이 없어도 답이 된다.
계획서: `services/web/docs/polymarket-consensus-qa.md`
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.web.llm.backends import LLMBackend, LLMBackendError, TokenUsage
from services.web.llm.polymarket_brief import FORBIDDEN_COPY, has_model_probability
from services.web.llm.terminology import read_prompt

logger = logging.getLogger(__name__)

COMMENTARY_MIN_CHARS = 15
COMMENTARY_MAX_CHARS = 400
# "가까운 질문"으로 함께 보일 수. 많이 보이면 없는 답을 있는 것처럼 읽고, 있는 답도 목록에 묻힌다.
RELATED_MAX = 2

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "direct": {"type": "boolean"},
                    "focus": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["n", "direct", "focus"],
            },
        },
        "commentary": {"type": "string"},
    },
    "required": ["answerable", "picks", "commentary"],
}
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "forecast_answer", "schema": RESPONSE_SCHEMA}}


class PolymarketAnswerError(RuntimeError):
    """답을 만들지 못했다 — 호출이 실패했거나 두 번 다 고른 질문을 쓸 수 없었다."""


class AnswerRejected(ValueError):
    """응답 반려. `structural`이면 고른 질문부터 못 쓰고, 아니면 해설만 못 쓴다."""

    def __init__(self, reason: str, *, structural: bool):
        super().__init__(reason)
        self.structural = structural


@dataclass
class Pick:
    n: int
    direct: bool
    focus: list[int]


@dataclass
class Answer:
    answerable: bool
    picks: list[Pick]
    commentary: str | None
    usages: list[TokenUsage | None] = field(default_factory=list)


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_answer(raw: str, options: dict[int, set[int]], *, max_picks: int) -> Answer:
    """고른 질문을 검사한다. 후보 밖 번호·중복·없는 선택지는 그 칸만 버린다."""
    text = raw.strip()
    # /no_think를 붙여도 빈 thinking 블록이 앞에 붙어 오는 응답이 있다.
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise AnswerRejected(f"JSON이 아닙니다: {error}", structural=True) from error
    if not isinstance(payload, dict) or not isinstance(payload.get("answerable"), bool):
        raise AnswerRejected("answerable이 참·거짓이 아닙니다", structural=True)
    answerable = payload["answerable"]
    picks: list[Pick] = []
    for row in payload.get("picks") if isinstance(payload.get("picks"), list) else []:
        number = row.get("n") if isinstance(row, dict) else None
        if not _int(number) or number not in options or any(pick.n == number for pick in picks):
            continue
        focus = [m for m in dict.fromkeys(row.get("focus") or []) if _int(m) and m in options[number]]
        picks.append(Pick(number, bool(row.get("direct")) and answerable, focus))
    if answerable and not any(pick.direct for pick in picks):
        raise AnswerRejected("직접 맞는다고 한 질문이 없습니다", structural=True)
    # 직접 답은 `max_picks`개까지, 가까운 질문은 직접 답이 있든 없든 RELATED_MAX개까지다(실측: 직접 하나에
    # 가까운 넷이 붙어 답이 목록처럼 길어졌다).
    direct = [pick for pick in picks if pick.direct][:max_picks]
    related = [pick for pick in picks if not pick.direct][:RELATED_MAX]
    kept = {id(pick) for pick in direct + related}
    picks = [pick for pick in picks if id(pick) in kept][:max_picks]
    return Answer(answerable, picks, " ".join(str(payload.get("commentary") or "").split()))


def check_commentary(text: str | None) -> None:
    """해설은 숫자 없는 두세 문장이다. 숫자·출처 이름·투자 권유 표현이 있으면 반려한다."""
    text = text or ""
    if not COMMENTARY_MIN_CHARS <= len(text) <= COMMENTARY_MAX_CHARS:
        raise AnswerRejected(f"해설 길이가 {COMMENTARY_MIN_CHARS}~{COMMENTARY_MAX_CHARS}자를 벗어났습니다",
                             structural=False)
    if text.startswith(("{", "[")):
        raise AnswerRejected("해설이 문장이 아닙니다", structural=False)
    if has_model_probability(text):
        raise AnswerRejected("확률 숫자: 해설에 확률을 숫자로 쓰지 마십시오. 숫자는 서버가 붙입니다",
                             structural=False)
    if FORBIDDEN_COPY.search(text):
        raise AnswerRejected("금지 표현: 출처 서비스 이름·베팅·매수·매도 같은 말을 쓰지 마십시오",
                             structural=False)


class PolymarketAnswerer:
    def __init__(self, backend: LLMBackend, prompt_file: Path, num_predict: int, max_picks: int):
        self._backend = backend
        self._num_predict = num_predict
        self._max_picks = max_picks
        self._prompt = read_prompt(prompt_file)

    @property
    def backend(self) -> LLMBackend:
        return self._backend

    def answer(self, question: str, candidates: list[dict[str, Any]]) -> Answer:
        """`candidates`는 `{n, title, summary?, type, ends, participation, options[{m, label, outlook}]}`(블로킹)."""
        options = {int(row["n"]): {int(option["m"]) for option in row["options"]} for row in candidates}
        payload: dict[str, Any] = {"question": question, "candidates": candidates}
        usages: list[TokenUsage | None] = []
        fallback: Answer | None = None
        for attempt in range(2):
            try:
                raw = self._backend.generate(
                    system_prompt=self._prompt,
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                    max_tokens=self._num_predict,
                    # 교정 때는 조금 올린다. 같은 온도에서는 같은 답이 다시 온다(줄글 브리프와 같다).
                    temperature=0.2 if attempt == 0 else 0.4,
                    response_format=RESPONSE_FORMAT,
                )
            except LLMBackendError as error:
                raise PolymarketAnswerError(str(error)) from error
            finally:
                usages.append(getattr(self._backend, "last_usage", None))
            try:
                answer = parse_answer(raw, options, max_picks=self._max_picks)
            except AnswerRejected as rejected:
                if attempt or fallback is not None:
                    break
                logger.warning("[FORECAST_ASK] 응답 반려로 1회 교정: %s", rejected)
                payload["revision"] = _revision(rejected)
                continue
            try:
                check_commentary(answer.commentary)
            except AnswerRejected as rejected:
                fallback = Answer(answer.answerable, answer.picks, None)
                if attempt:
                    break
                logger.warning("[FORECAST_ASK] 해설 반려로 1회 교정: %s", rejected)
                payload["revision"] = _revision(rejected)
                continue
            answer.usages = usages
            return answer
        if fallback is None:
            raise PolymarketAnswerError("answer correction exhausted")
        fallback.usages = usages
        return fallback


def _revision(rejected: AnswerRejected) -> dict[str, str]:
    """교정 지시. 직전 응답은 돌려주지 않는다 — 숫자가 들었으면 그 숫자를 다른 질문에 옮겨 붙인다."""
    return {
        "reason": re.sub(r"\d+", "", str(rejected)),
        "instruction": "직전 응답은 위 사유로 반려됐습니다. 원래 입력만 보고 응답 전체를 새로 쓰십시오. "
        "확률은 숫자로 쓰지 말고 등급의 말(크게 기울어 있다·우세하다·엇갈린다·낮게 본다)로만 쓰십시오.",
    }
