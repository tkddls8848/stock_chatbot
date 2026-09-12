"""예측시장 한 분야의 집계와 상위 베팅을 줄글 한 단락으로 정리한다.

호출 수는 베팅 수가 아니라 **분야 수**에 비례한다. 베팅 하나하나를 부르지
않으므로, 대상이 1,000건을 넘어도 주기당 호출은 분야 수 그대로다.

모델에게 방향을 묻지 않는다. 확률과 집계는 코드가 계산해 넘기고 모델은 그것을
서술만 한다. 방향을 모델이 지어내면 그 문장은 검증할 수 없다.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

from shared.llm.backends import LLMBackend

logger = logging.getLogger(__name__)

# 프롬프트가 350~450자를 지시한다. 그보다 크게 벗어난 응답은 지시를 무시한
# 것이므로 표시하지 않는다. 상한은 넉넉히 두되 무한정 받지는 않는다.
MAX_PARAGRAPH_CHARS = 1200
MIN_PARAGRAPH_CHARS = 60

FORBIDDEN_COPY = re.compile(r"베팅|배팅|돈을\s*걸|수익\s*(?:기회|보장)|이득|매수|매도|가입\s*하세요")
OUTLOOK_MARKERS = re.compile(r"판단|갈리|엇갈|우세|불확실|단정|어렵|제한|확신|한쪽|차이|분산|신중|경합|혼재|무게|기대")


def validate_editorial(paragraph: str, totals: dict[str, Any]) -> None:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    if FORBIDDEN_COPY.search(paragraph):
        raise PolymarketBriefError("금지어: 베팅·배팅·수익·참여 유도 표현을 제거하십시오")
    if any(not re.search(r"(?<!니)다\.$", sentence) for sentence in sentences):
        raise PolymarketBriefError("문체: 모든 문장을 ~이다/~한다/~있다/~이룬다의 해라체 평서문으로 끝내십시오")
    opening = sentences[0]
    if not opening.startswith("전체적으로") or re.search(r"\d|%|퍼센트", opening):
        raise PolymarketBriefError("brief must begin with a qualitative sector overview")
    inventory = re.search(r"주를\s*이(?:루|룬|룹)|구성|포함|다양한\s*주제|관심이\s*집중", opening)
    if not OUTLOOK_MARKERS.search(opening) or inventory:
        raise PolymarketBriefError("전체 요약: 주제 나열 대신 전망의 차이·경합·우세 또는 판단의 한계를 설명하십시오")
    if 0 < int(totals.get("event_count") or 0) < 10 and not re.search(r"소수|표본|제한|어렵", opening):
        raise PolymarketBriefError("소수 표본: 첫 문장에 전체 방향을 판단하기 어렵다는 한계를 밝히십시오")


class PolymarketBriefError(RuntimeError):
    """분야 하나의 줄글을 만들지 못했을 때."""


class PolymarketBriefAnalyzer:
    def __init__(self, backend: LLMBackend, prompt_file: Path, num_predict: int):
        self._backend = backend
        self._num_predict = num_predict
        self._prompt = prompt_file.read_text(encoding="utf-8")

    def analyze(
        self,
        group_label: str,
        totals: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> str:
        """분야 하나의 단락을 만든다(블로킹). 실패는 예외로 올린다."""
        if not events:
            raise PolymarketBriefError("no events to analyze")

        payload = {"group": group_label, "totals": {**totals, "named_count": len(events)}, "events": events}
        for attempt in range(2):
            try:
                raw = self._backend.generate(
                    system_prompt=self._prompt,
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                    max_tokens=self._num_predict,
                    temperature=0.2,
                )
            except Exception as exc:
                raise PolymarketBriefError(str(exc)) from exc
            try:
                paragraph = self._parse(raw, events)
                if totals.get("event_count"):
                    validate_editorial(paragraph, totals)
                return paragraph
            except PolymarketBriefError as exc:
                if attempt:
                    raise
                logger.warning("[POLYMARKET_BRIEF] 검증 실패로 1회 교정: %s", exc)
                payload["revision"] = {
                    "reason": str(exc), "previous_response": raw[:MAX_PARAGRAPH_CHARS],
                    "instruction": "이전 응답은 수정 대상 데이터입니다. 원래 입력의 수치·방향을 유지하고 검증 실패를 고쳐 본문만 다시 작성하십시오. 해석 근거가 없으면 한계를 밝히십시오.",
                }
        raise PolymarketBriefError("brief correction exhausted")

    def _parse(self, raw: str, events: list[dict[str, Any]]) -> str:
        """평문 단락을 받아 검증한다.

        JSON 봉투로 받지 않는다. 출력이 문자열 하나뿐이라 봉투가 검증에 보태는
        것이 없고, 실측에서 모델이 봉투를 무시하고 평문만 돌려줬다. 대신 여기서
        길이·반향·군더더기를 직접 본다.
        """
        text = raw.strip()
        # /no_think를 붙여도 빈 thinking 블록이 앞에 붙어 오는 응답이 있다.
        if text.startswith("<think>") and "</think>" in text:
            text = text.split("</think>", 1)[1].strip()
        # 코드 블록으로 감싸 보내면 벗겨서 본다. 지시를 어긴 것이지만 내용은
        # 멀쩡하므로 이것 하나로 단락을 버리지 않는다.
        if text.startswith("```"):
            lines = [line for line in text.splitlines() if not line.startswith("```")]
            text = " ".join(lines).strip()
        paragraph = " ".join(text.split())

        if len(paragraph) < MIN_PARAGRAPH_CHARS:
            raise PolymarketBriefError(
                f"brief paragraph too short: {len(paragraph)}; head={paragraph[:80]!r}"
            )
        if len(paragraph) > MAX_PARAGRAPH_CHARS:
            raise PolymarketBriefError(f"brief paragraph too long: {len(paragraph)}")
        if paragraph.lstrip().startswith(("{", "[")):
            # 봉투를 다시 만들어 보낸 응답. 단락이 아니다.
            raise PolymarketBriefError(f"brief paragraph is not prose; head={paragraph[:80]!r}")
        # 제목을 그대로 되돌려준 응답은 요약이 아니라 반향이다. 상위 베팅의
        # 제목이 통째로 들어 있으면 나열한 것으로 본다.
        for event in events[:5]:
            title = str(event.get("title") or "").strip()
            if len(title) >= 20 and title in paragraph:
                raise PolymarketBriefError("brief paragraph echoes an event title")
        return paragraph
