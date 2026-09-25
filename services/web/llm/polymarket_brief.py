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

from services.web.llm.backends import LLMBackend

logger = logging.getLogger(__name__)

# 프롬프트가 350~450자를 지시한다. 그보다 크게 벗어난 응답은 지시를 무시한
# 것이므로 표시하지 않는다. 상한은 넉넉히 두되 무한정 받지는 않는다.
MAX_PARAGRAPH_CHARS = 1200
MIN_PARAGRAPH_CHARS = 60

# 출처 서비스 이름은 화면에 쓰지 않는다(`code_guide.md`). 모델이 입력 밖에서 끌어올 수 있어 막는다.
FORBIDDEN_COPY = re.compile(r"(?i)polymarket|폴리마켓|예측\s*시장|베팅|배팅|돈을\s*걸|수익\s*(?:기회|보장)|이득|매수|매도|가입\s*하세요")
# 첫 문장이 "전망이 어디서 갈리는가 / 무엇에 무게가 실리는가 / 왜 판단하기 어려운가"
# 중 하나를 말했는지 보는 단서다. 프롬프트가 요구하는 세 갈래를 그대로 덮는다 —
# 목록이 좁으면 같은 뜻을 다른 낱말로 쓴 정상 문장이 반려된다(실측: "…판단이
# 한쪽으로 쏠려 있으나 방향은 뚜렷하지 않다"가 단서 없음으로 빠졌다).
OUTLOOK_MARKERS = re.compile(
    "판단|갈리|갈린|갈림|엇갈|우세|우위|압도|불확실|단정|어렵|제한|확신|한쪽|차이|"
    "분산|신중|경합|혼재|무게|기대|쏠|기울|뚜렷|팽팽|맞서|상충|상반|대립|뒤섞|나뉘|제각"
)
# 주제 나열은 한국어에서 **문장 끝의 서술어**가 "무엇으로 이루어져 있다"일 때다.
# 낱말만 보면 종속절에 쓰인 같은 낱말까지 걸려("경합 구간을 포함해 …", "한 방향으로
# 구성하기 어렵다") 전망을 제대로 설명한 문장이 반려됐다 — 서버 실측에서 매 주기
# 5그룹 중 1~3그룹이 이 검사로 화면에서 빠진 원인이다. 그래서 끝맺음만 본다.
INVENTORY_PREDICATE = re.compile(
    r"(?:주를\s*이(?:룬다|뤘다|루었다|루고\s*있다|룹니다)"
    r"|(?:구성|포함|집중)(?:된다|됐다|되고\s*있다|돼\s*있다|되어\s*있다|됩니다))"
    r"\s*[.!?]?$"
)


def _is_plain_declarative(sentence: str) -> bool:
    """해라체 평서문으로 끝났는가.

    존댓말 종결 `-ㅂ니다`는 앞 음절 받침이 ㅂ이다(입니다·합니다·습니다). `니다`만
    보고 막으면 `아니다`처럼 멀쩡한 해라체까지 반려한다 — 프롬프트가 "사실 확정이
    아니다"라고 쓰라고 지시하는 자리라 실제로 걸린다.
    """
    if not sentence.endswith("다."):
        return False
    stem = sentence[:-2]
    if len(stem) >= 2 and stem.endswith("니"):
        previous = stem[-2]
        if "가" <= previous <= "힣" and (ord(previous) - 0xAC00) % 28 == 17:
            return False
    return True


def validate_editorial(paragraph: str, totals: dict[str, Any]) -> None:
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    if FORBIDDEN_COPY.search(paragraph):
        raise PolymarketBriefError("금지어: 출처 서비스 이름·예측시장·베팅·배팅·수익·참여 유도 표현을 제거하십시오")
    if any(not _is_plain_declarative(sentence) for sentence in sentences):
        raise PolymarketBriefError("문체: 모든 문장을 ~이다/~한다/~있다/~이룬다의 해라체 평서문으로 끝내십시오")
    opening = sentences[0]
    if not opening.startswith("전체적으로") or re.search(r"\d|%|퍼센트", opening):
        raise PolymarketBriefError("brief must begin with a qualitative sector overview")
    # 두 사유를 한 문장으로 합치면 1회뿐인 교정이 엉뚱한 곳을 고친다. 실측에서
    # 모델은 이미 전망을 설명해 놓고도 같은 문구를 다시 받아 같은 실수를 반복했다.
    if INVENTORY_PREDICATE.search(opening):
        raise PolymarketBriefError("전체 요약: 나열로 끝맺지 말고 전망의 우세·경합 또는 판단의 한계를 결론으로 쓰십시오")
    if not OUTLOOK_MARKERS.search(opening):
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
