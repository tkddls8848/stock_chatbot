"""폴리마켓 event 자연어 검색의 점수. 검색하는 순간에는 LLM을 부르지 않는다.

질문을 낱말로 쪼개고, 낱말마다 event의 어느 칸에 들어 있는지로 점수를 매긴다.
한국어 질문이 영어 제목에 닿는 다리는 `annotate.py`가 미리 달아 둔 키워드다 —
"연준 금리 인하"의 "연준"은 제목(Fed)에는 없고 키워드에만 있다.

정렬은 **맞은 낱말 수 → 칸 가중 점수 → 24시간 거래량** 순이다. 거래량은 동점을
가를 때만 쓴다. 관련 없는 대형 event가 위로 올라오면 검색이 아니라 순위표가 된다.

계획서: `docs/polymarket-nl-search.md` 5절
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# 칸별 가중. 키워드가 가장 무겁다 — 한국어 질문이 닿는 자리가 거기다.
WEIGHT_KEYWORDS = 4
WEIGHT_TITLE = 3
WEIGHT_SUBTOPIC = 2
WEIGHT_SUMMARY = 2
WEIGHT_TAGS = 1

# 검색 의도는 담지만 어느 event도 가르지 못하는 말. 이 화면의 모든 event가
# "예측"이고 "배팅"이라, 남겨 두면 맞은 낱말 수만 부풀린다.
_STOP = set(
    "배팅 베팅 확률 예측 예상 전망 가능성 마켓 폴리마켓 polymarket 질문 관련 대한 대해 "
    "영향 어떻게 어떤 무슨 누가 언제 어디 찾아 찾아줘 찾아주세요 알려줘 알려주세요 "
    "보여줘 보여주세요 검색 검색해줘 있어 있나 있나요 있는 있을까 좀 모든 전체 결과 "
    "것 거 뭐 및 그리고 또는 이번 올해 내년 과연 정말".split()
)
# 조사·어미. 떼고 남은 말이 두 글자 이상일 때만 뗀다("미국은" → "미국").
_SUFFIX = re.compile(
    r"(?:에서는|에서|으로|에게|에는|하고|이랑|처럼|까지|부터|보다|이나|인가요|인가|인지|"
    r"일까|할까|될까|할지|될지|했나|하나|은|는|이|가|을|를|에|의|과|와|도|만|로)$"
)
_TOKEN = re.compile(r"[a-z0-9가-힣一-龥ぁ-んァ-ヶ]+")
_ASCII = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def query_tokens(query: str) -> list[str]:
    """질문을 검색 낱말로 쪼갠다.

    전부 불용어라 남는 것이 없으면("폴리마켓 배팅") 불용어를 되살린다 —
    무엇을 찾는지 모르는 질문에 빈 결과를 돌려주는 것보다 그 말 그대로 찾는
    편이 낫다.
    """
    raw = [token for token in _TOKEN.findall(normalize(query)) if token]
    tokens: list[str] = []
    for token in raw:
        if token in _STOP:
            continue
        stripped = _SUFFIX.sub("", token)
        token = stripped if len(stripped) >= 2 else token
        if token in _STOP or len(token) < 2 and not _ASCII.fullmatch(token):
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens or list(dict.fromkeys(raw))


def minimum_matches(tokens: list[str]) -> int:
    """결과로 남기려면 맞아야 하는 낱말 수. 절반 이상이다.

    전부 맞기를 요구하면 "트럼프 관세 한국 자동차"처럼 긴 질문이 0건이 되고,
    하나만 맞아도 되면 "한국" 하나로 수백 건이 걸린다.
    """
    return max(1, (len(tokens) + 1) // 2)


def compile_tokens(tokens: list[str]) -> list[tuple[str, str, re.Pattern[str] | None]]:
    """낱말마다 (원형, 공백 뺀 형태, 영문이면 단어 경계 패턴)."""
    compiled = []
    for token in tokens:
        pattern = None
        if _ASCII.fullmatch(token):
            # 영문은 단어 앞머리부터 맞춘다. 입력 중인 "tru"도 Trump에 닿게
            # 하되 "ai"가 "said"에 걸리지 않게 한다. 두 글자 이하는 단어 전체만.
            tail = r"(?![a-z0-9])" if len(token) <= 2 else ""
            pattern = re.compile(r"(?<![a-z0-9])" + re.escape(token) + tail)
        compiled.append((token, token.replace(" ", ""), pattern))
    return compiled


def prepare_annotation(annotation: dict[str, Any]) -> tuple[str, str, str]:
    """색인 한 행을 검색용 문자열 셋으로 줄인다: (키워드, 세부 주제, 요약).

    목록을 문자열 하나로 합쳐 두는 것은 메모리 때문이다. 웹 프로세스는
    `MemoryMax=256M`이고 21,872건의 키워드 목록을 객체째 들면 몇 배로 불어난다.
    """
    keywords = " | ".join(normalize(value) for value in annotation.get("keywords") or [])
    return keywords, str(annotation.get("subtopic") or ""), str(annotation.get("summary") or "")


def _hit(field: str, token: str, compact: str, pattern: re.Pattern[str] | None) -> bool:
    if not field:
        return False
    if pattern is not None:
        return pattern.search(field) is not None
    # 한국어는 띄어쓰기가 흔들린다("금리인하" ↔ "금리 인하"). 둘 다 본다.
    return token in field or compact in field.replace(" ", "")


def score(
    compiled: list[tuple[str, str, re.Pattern[str] | None]],
    event: dict[str, Any],
    annotation: tuple[str, str, str] | None,
) -> tuple[int, int]:
    """(맞은 낱말 수, 칸 가중 점수). 한 낱말은 가장 무거운 칸 하나로만 센다."""
    title = normalize(event.get("title"))
    tags = normalize(" ".join(str(tag) for tag in event.get("tags") or []))
    keywords, subtopic, summary = annotation or ("", "", "")
    subtopic, summary = normalize(subtopic), normalize(summary)
    fields = (
        (WEIGHT_KEYWORDS, keywords),
        (WEIGHT_TITLE, title),
        (WEIGHT_SUBTOPIC, subtopic),
        (WEIGHT_SUMMARY, summary),
        (WEIGHT_TAGS, tags),
    )
    matched = total = 0
    for token, compact, pattern in compiled:
        best = max((weight for weight, field in fields if _hit(field, token, compact, pattern)), default=0)
        if best:
            matched += 1
            total += best
    return matched, total
