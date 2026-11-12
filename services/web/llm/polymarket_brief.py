"""예측시장 한 분야의 집계와 상위 베팅을 줄글 한 단락으로 정리한다.

호출 수는 베팅 수가 아니라 **분야 수**에 비례한다. 베팅 하나하나를 부르지
않으므로, 대상이 1,000건을 넘어도 주기당 호출은 분야 수 그대로다.

모델에게 방향을 묻지 않는다. 확률과 집계는 코드가 계산해 넘기고 모델은 그것을
서술만 한다. 방향을 모델이 지어내면 그 문장은 검증할 수 없다.
"""

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from services.web.llm.backends import LLMBackend
from services.web.llm.terminology import read_prompt

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


# 분야 이름만 바꾸면 어느 분야에나 들어맞는 상투 첫 문장. 프롬프트의 예시를 모델이 그대로
# 옮겨, 10/2 00시 수집분에서 다섯 분야 중 넷이 "전체적으로 질문별 전망의 차이가 커 하나의
# 정책 방향으로 묶기 어렵다"로 시작했다(지정학에도 "정책 방향"). 실제로 반복된 표현만 막는다 —
# 넓히면 다른 말로 한계를 바르게 쓴 문장까지 반려된다(아래 OUTLOOK 오탐 사례).
BOILERPLATE_OPENING = re.compile(r"질문별\s*전망의?\s*차이가\s*커|하나의\s*(?:정책|공통된?)\s*방향")


# 관심사만 소개하고 판단은 둘째 문장으로 미룬 첫 문장. 관심사를 첫 문장에 넣게 하자 모델이
# 이 끝맺음으로 몰렸고, 일반 사유("주제 나열 대신…")로 교정하면 같은 문장을 그대로 다시
# 냈다(10/2 실측: 다섯 분야 중 셋). 그래서 이 실수에는 고칠 모양까지 담은 사유를 따로 준다.
INTEREST_PREDICATE = re.compile(
    r"관심(?:을|이)\s*(?:보인다|보이고\s*있다|높다|크다|많다|모인다|집중된다|집중되고\s*있다)\s*[.!?]?$"
    r"|전망이\s*집중(?:된다|되고\s*있다)\s*[.!?]?$"
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
        raise OpeningRejected("brief must begin with a qualitative sector overview")
    if INTEREST_PREDICATE.search(opening):
        raise OpeningRejected(
            "관심 소개: 첫 문장을 관심을 보인다·관심이 높다로 끝내지 말고 같은 문장에서 판단을 결론으로 쓰십시오. "
            "구조: 전체적으로 ⟨쟁점⟩에서는 ⟨무게가 실린 쪽⟩이 우세하지만 ⟨갈리는 지점⟩은 엇갈린다.")
    # 두 사유를 한 문장으로 합치면 1회뿐인 교정이 엉뚱한 곳을 고친다. 실측에서
    # 모델은 이미 전망을 설명해 놓고도 같은 문구를 다시 받아 같은 실수를 반복했다.
    if INVENTORY_PREDICATE.search(opening):
        raise OpeningRejected(
            "전체 요약: 나열로 끝맺지 말고 전망의 우세·경합 또는 판단의 한계를 결론으로 쓰십시오. "
            "구조: 전체적으로 ⟨쟁점⟩에서는 ⟨무게가 실린 쪽⟩이 우세하지만 ⟨갈리는 지점⟩은 엇갈린다.")
    if BOILERPLATE_OPENING.search(opening):
        raise OpeningRejected("상투 문장: 어느 분야에나 붙는 문장 대신 상위 질문의 구체적 쟁점과 그 쟁점에서 무게가 실리거나 갈리는 쪽을 쓰십시오")
    if not OUTLOOK_MARKERS.search(opening):
        raise OpeningRejected("전체 요약: 주제 나열 대신 전망의 차이·경합·우세 또는 판단의 한계를 설명하십시오")
    if 0 < int(totals.get("event_count") or 0) < 10 and not re.search(r"소수|표본|제한|어렵", opening):
        raise OpeningRejected("소수 표본: 첫 문장에 전체 방향을 판단하기 어렵다는 한계를 밝히십시오")


class PolymarketBriefError(RuntimeError):
    """분야 하나의 줄글을 만들지 못했을 때."""


class ProbabilityWritten(PolymarketBriefError):
    """모델이 확률 숫자를 직접 썼다. 숫자는 서버가 질문 이름과 함께 단락 뒤에 붙인다."""


# 모델이 직접 쓴 확률 표현. 전각 ％·"퍼센트"·"프로"·0.xx 소수까지 본다(NFKC 뒤). 숫자는 서버가
# 질문의 주어와 함께 쓴다 — 모델이 쓰게 두면 다른 질문의 숫자를 붙인다(2026-10-03 복합: 유가 문장에
# 호르무즈의 20.5%). 표지·숫자 대조로는 주어 오귀속을 못 잡고(계획 검수), 모델에게 자리표시자를 문장
# 사이에 넣게 하면 다섯 분야 중 넷이 규칙을 어겼다(실측 2026-10-03) — 그래서 숫자는 단락 뒤에 붙인다.
# 모델은 확률을 말할 필요가 없다 — 숫자는 서버가 붙인다. 그래서 탐지는 열거가 아니라 **보수적인 단순 규칙**이다
# (조사·어미 목록을 쫓아가다 "20프로였다"·"20프로보다"·"0.205배분된다"가 빠졌다, 5차 검수):
#   · 백분율 낱말(%·퍼센트·퍼 센트·퍼센티지·percent)은 숫자가 없어도 반려
#   · 아라비아 숫자 바로 뒤의 "프로"는 무엇이 따라오든 반려
#   · 0.xx 소수는 단위와 상관없이 반려
#   · 한글 수사 뒤의 "프로"도 반려한다. 한 글자 수사("오프로드"의 오)는 낱말을 이루는 몇 가지 뒤 글자만 뺀다 —
#     조사·어미를 열거하면 "삼 프로밖에"·"삼 프로든"이 빠졌다(6차 검수). 그래서 열거의 방향을 뒤집었다
#   · ".205"·"영점이공오"·"1/5"·"⅕"(NFKC 뒤 분수 슬래시)·"100분의 이십"·"5분의 하나" 같은 소수·분수 표기도 반려
# **이 탐지는 이중 장치다.** 1차 장치는 입력이다 — 모델은 확률 숫자를 받지 않고 등급(`outlook`)만 받는다.
# 정규식을 1차 장치로 두자 검수마다 새 표기("공점이공오"·"٪"·"스물다섯 프로"·"일/오")가 나왔다(7~11차).
# 입력에 없는 숫자는 다른 질문에 옮겨 붙일 수 없으므로, 여기서는 흔한 표기만 막고 정상 문장을 해치지 않는 쪽을
# 고른다("항공 점검"·"쟁점이 프로그램"·"다섯 분의 이동"은 통과). 말로 풀어 쓴 비율("절반")도 잡지 않는다.
# "프로그램"·"프로젝트"처럼 "프로"로 시작하는 낱말. 수사 뒤 "프로"에서 뺀다.
_NOT_PRO_WORD = r"(?!그램|젝트|세스|필|모션|듀서|덕션|토콜|야구|축구|골프|선수|게이머|드|틴|바이오|폴리오|미스|모|파일)"
_MODEL_PERCENT = re.compile(
    r"[%％٪‰‱]|퍼\s*센\s*트|퍼센티지|per\s?cent"
    r"|\d\s*프로"
    r"|(?<![\d.,])0?[.,]\d"
    r"|(?<![가-힣])[영공일이삼사오육칠팔구십백]*[십백점][영공일이삼사오육칠팔구십백점]*\s*프로" + _NOT_PRO_WORD
    + r"|[일이삼사오육칠팔구]\s*프로" + _NOT_PRO_WORD
    + r"|(?:영|공|제로)\s*점\s*[\d영공일이삼사오육칠팔구]"
    r"|\d\s*분\s*의|분\s*의\s*\d"
    r"|(?:[영일이삼사오육칠팔구십백천만]\s*|(?:둘|셋|넷|다섯|여섯|일곱|여덟|아홉|열))분\s*의\s*"
    r"(?:[영일이삼사오육칠팔구십백천만]|하나|둘|셋|넷|다섯|여섯|일곱|여덟|아홉|열)"
    r"|\d\s*[/⁄∕]\s*\d",
    re.IGNORECASE,
)
# 단락 뒤에 붙이는 사실 문장 수. 참여 규모 상위부터, 숫자가 있는 질문만.
FACT_SENTENCES = 3


def outlook(probability: float) -> str:
    """확률을 모델 입력용 등급으로. **모델 입력에는 확률 숫자를 두지 않는다** — 숫자를 받으면 모델이 그것을 다른
    질문에 옮겨 붙였다(2026-10-03 복합: 유가 문장에 호르무즈의 20.5%). 숫자는 서버가 사실 문장으로만 쓴다."""
    if probability < 0.2:
        return "매우 낮음"
    if probability < 0.4:
        return "낮음"
    if probability <= 0.6:
        return "엇갈림"
    if probability <= 0.8:
        return "높음"
    return "매우 높음"


def _model_totals(totals: dict[str, Any]) -> dict[str, Any]:
    """분야 집계에서 확률 숫자(중앙값)를 등급으로 바꾼다. 건수는 확률이 아니라 그대로 둔다."""
    distribution = totals.get("probability")
    if not isinstance(distribution, dict):
        return totals
    median = distribution.get("median")
    rest = {key: value for key, value in distribution.items() if key != "median"}
    if isinstance(median, (int, float)):
        rest["median_outlook"] = outlook(float(median))
    return {**totals, "probability": rest}


def _for_model(text: str) -> str | None:
    """모델에 다시 보내도 되는 글이면 그대로, 숫자(아라비아 숫자·확률 표현)가 있으면 None."""
    normalized = unicodedata.normalize("NFKC", text)
    if any(char.isdigit() for char in normalized) or _MODEL_PERCENT.search(normalized):
        return None
    return text


def has_model_probability(text: str) -> bool:
    """확률 숫자(백분율·"퍼센트"·0.xx)가 들어 있는가."""
    return bool(_MODEL_PERCENT.search(unicodedata.normalize("NFKC", text)))


# 사실 문장을 붙인 뒤 공개 단락의 상한. 넘으면 뒤의 사실 문장부터 뺀다.
MAX_PUBLISHED_CHARS = 1600


def publishable_facts(facts: list[str | None]) -> list[str]:
    """공개해도 되는 서버 사실 문장(참여 규모순, 최대 FACT_SENTENCES개). 해설 성공·실패 경로가 같이 쓴다.

    라벨·선택지 원문에 금지어(출처 서비스명 등)가 있으면 그 문장은 뺀다 — 서버 문장도 공개 문구다.
    """
    return [fact for fact in facts if fact and not FORBIDDEN_COPY.search(fact)][:FACT_SENTENCES]


def join_facts(paragraph: str, facts: list[str | None]) -> str:
    """단락(없으면 빈 문자열) 뒤에 공개할 사실 문장을 붙인다. 상한을 넘으면 뒤 문장부터 뺀다."""
    listed = publishable_facts(facts)
    head = [paragraph] if paragraph else []
    while listed and len(" ".join([*head, *listed])) > MAX_PUBLISHED_CHARS:
        listed.pop()
    return " ".join([*head, *listed])


def attach_facts(paragraph: str, facts: list[str]) -> str:
    """숫자 없는 해설 단락 뒤에 서버가 쓴 사실 문장(참여 규모 상위)을 붙인다."""
    found = _MODEL_PERCENT.search(unicodedata.normalize("NFKC", paragraph))
    if found:
        raise ProbabilityWritten(
            f"확률 숫자: 본문에 확률을 직접 쓰지 마십시오(찾은 표현: {found.group(0)}). 확률 문장은 서버가 "
            "질문 이름과 함께 단락 뒤에 붙입니다. 숫자 없이 흐름만 쓰십시오")
    return join_facts(paragraph, facts)


class OpeningRejected(PolymarketBriefError):
    """첫 문장(전체 요약)이 반려됐다. 교정 때 이전 응답을 돌려주지 않는다 —
    돌려주면 모델이 그 문장을 글자째 다시 냈다(10/2 주식·시장, 온도 0.2·0.5 모두).
    빼고 다시 쓰게 하자 바로 판단으로 끝나는 첫 문장이 나왔다."""


class PolymarketBriefAnalyzer:
    def __init__(self, backend: LLMBackend, prompt_file: Path, num_predict: int):
        self._backend = backend
        self._num_predict = num_predict
        self._prompt = read_prompt(prompt_file)

    def analyze(
        self,
        group_label: str,
        totals: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> str:
        """분야 하나의 단락을 만든다(블로킹). 실패는 예외로 올린다."""
        if not events:
            raise PolymarketBriefError("no events to analyze")

        # 사실 문장(`fact`)은 서버 전용이다. 참여 규모순으로 단락 뒤에 붙인다.
        facts = [str(row["fact"]) for row in events if row.get("fact")]
        model_events = [{key: value for key, value in row.items() if key != "fact"} for row in events]
        payload = {"group": group_label, "totals": {**_model_totals(totals), "named_count": len(events)},
                   "events": model_events}
        for attempt in range(2):
            try:
                raw = self._backend.generate(
                    system_prompt=self._prompt,
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                    max_tokens=self._num_predict,
                    # 교정 때는 조금 올린다. 0.2에서는 자기 이전 응답을 데이터로 받고도
                    # 같은 첫 문장을 글자째 다시 냈다(10/2 주식·시장) — 같은 답은 같은 반려다.
                    temperature=0.2 if attempt == 0 else 0.5,
                )
            except Exception as exc:
                raise PolymarketBriefError(str(exc)) from exc
            try:
                paragraph = self._parse(raw, events)
                if totals.get("event_count"):
                    validate_editorial(paragraph, totals)
                return attach_facts(paragraph, facts)
            except PolymarketBriefError as exc:
                if attempt:
                    raise
                logger.warning("[POLYMARKET_BRIEF] 검증 실패로 1회 교정: %s", exc)
                # 교정 입력에도 숫자를 다시 넣지 않는다. 이전 응답·반려 사유에 숫자가 있으면(확률 반려는 늘 그렇다)
                # 보내지 않는다 — 보내면 모델이 그 숫자를 다른 질문에 옮겨 붙일 수 있다(12차 검수).
                if isinstance(exc, ProbabilityWritten):
                    reason = "확률 숫자: 본문에 확률을 숫자로 쓰지 마십시오. 확률 문장은 서버가 질문 이름과 함께 붙입니다"
                else:  # 길이 같은 숫자는 빼고 사유만 준다
                    reason = _for_model(re.sub(r"\d+", "", str(exc))) or "검증 실패"
                previous = _for_model(raw[:MAX_PARAGRAPH_CHARS])
                if isinstance(exc, ProbabilityWritten) or previous is None:
                    payload["revision"] = {
                        "reason": reason,
                        "instruction": "직전 응답은 위 사유로 반려됐습니다. 원래 입력만 보고 단락 전체를 새로 쓰십시오. 확률은 숫자로 쓰지 말고 등급의 말(우세하다·낮게 본다·엇갈린다)로만 쓰십시오. 해석 근거가 없으면 한계를 밝히십시오.",
                    }
                elif isinstance(exc, OpeningRejected):
                    payload["revision"] = {
                        "reason": reason,
                        "instruction": "직전 응답의 첫 문장이 위 사유로 반려됐습니다. 원래 입력만 보고 단락 전체를 새로 쓰되, 첫 문장을 사유가 요구하는 구조로 쓰십시오. 해석 근거가 없으면 한계를 밝히십시오.",
                    }
                else:
                    # 금지어·문체는 고칠 곳이 좁다. 이전 응답을 주고 그 부분만 고치게 해야
                    # 판단·방향이 바뀌지 않는다.
                    payload["revision"] = {
                        "reason": reason, "previous_response": previous,
                        "instruction": "이전 응답은 수정 대상 데이터입니다. 원래 입력의 방향을 유지하고 검증 실패를 고쳐 본문만 다시 작성하십시오. 해석 근거가 없으면 한계를 밝히십시오.",
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
