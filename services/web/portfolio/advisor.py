"""규칙 진단을 한 편의 조언으로 풀어 쓴다(LLM 호출 한 번, 교정 한 번).

**숫자는 진단이 만들고 모델은 해석만 한다.** 응답에 나온 숫자가 입력에 없으면
모델이 지어낸 것으로 보고 버린다(`_foreign_numbers`). 교정 뒤에도 실패하면 조언
본문 없이 진단만 저장한다 — 틀린 숫자가 든 조언보다 빈 칸이 낫다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from services.web.llm.backends import LLMBackend

logger = logging.getLogger(__name__)

MIN_CHARS = 200
MAX_CHARS = 1500
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 출처 서비스 이름·거래 권유. 조언은 참고 정보이지 특정 상품의 매매·가입 권유가 아니다.
FORBIDDEN = re.compile(
    r"(?i)polymarket|폴리마켓|예측\s*시장|베팅|배팅|매수\s*하세요|매도\s*하세요|사세요|파세요|"
    r"가입\s*하세요|반드시\s*오릅|수익\s*보장|원금\s*보장"
)


class AdviceError(RuntimeError):
    pass


def _numbers(text: str) -> set[float]:
    values = set()
    for token in _NUMBER.findall(text):
        try:
            values.add(round(float(token.replace(",", "")), 2))
        except ValueError:
            continue
    return values


def _foreign_numbers(paragraph: str, source: str) -> list[str]:
    """입력에 없는 숫자. 10 이하의 정수(순서·개수 표현)는 허용한다."""
    allowed = _numbers(source)
    foreign = []
    for token in _NUMBER.findall(paragraph):
        value = round(float(token.replace(",", "")), 2)
        if value in allowed or (value == int(value) and value <= 10):
            continue
        foreign.append(token)
    return foreign


def _clean(raw: str) -> str:
    text = raw.strip()
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    # 문단 구분(빈 줄)만 남기고 줄 안의 공백을 정리한다.
    paragraphs = [" ".join(block.split()) for block in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paragraphs if p)


def validate(text: str, source: str) -> None:
    if len(text) < MIN_CHARS:
        raise AdviceError(f"조언이 너무 짧다({len(text)}자)")
    if len(text) > MAX_CHARS:
        raise AdviceError(f"조언이 너무 길다({len(text)}자)")
    if text.lstrip().startswith(("{", "[")):
        raise AdviceError("조언이 문장이 아니다")
    if FORBIDDEN.search(text):
        raise AdviceError("금지어: 출처 서비스 이름·매매·가입 권유·보장 표현을 빼십시오")
    foreign = _foreign_numbers(text, source)
    if foreign:
        raise AdviceError("입력에 없는 숫자: " + ", ".join(foreign[:5]) + " — 진단의 숫자만 쓰십시오")


class PortfolioAdvisor:
    def __init__(self, backend: LLMBackend, prompt: str, num_predict: int):
        self._backend = backend
        self._prompt = prompt
        self._num_predict = num_predict

    def advise(self, diagnosis: dict[str, Any], context: dict[str, Any]) -> str:
        """블로킹. 실패는 `AdviceError`."""
        payload: dict[str, Any] = {"diagnosis": diagnosis, "market_context": context}
        source = json.dumps(payload, ensure_ascii=False)
        for attempt in range(2):
            try:
                raw = self._backend.generate(
                    system_prompt=self._prompt,
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                    max_tokens=self._num_predict,
                    temperature=0.2,
                )
            except Exception as error:
                raise AdviceError(str(error)) from error
            text = _clean(raw)
            try:
                validate(text, source)
                return text
            except AdviceError as error:
                if attempt:
                    raise
                logger.warning("[PORTFOLIO] 조언 검증 실패로 1회 교정: %s", error)
                payload["revision"] = {
                    "reason": str(error),
                    "previous_response": text[:MAX_CHARS],
                    "instruction": "이전 응답은 고칠 대상 데이터다. 진단에 있는 숫자만 쓰고 실패 사유를 고쳐 본문만 다시 쓰십시오.",
                }
        raise AdviceError("교정 소진")
