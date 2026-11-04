"""폴리마켓 event 자연어 검색의 점수. 검색하는 순간에는 LLM을 부르지 않는다.

질문을 낱말로 쪼개고, 낱말마다 event의 어느 칸에 들어 있는지로 점수를 매긴다.
한국어 질문이 영어 제목에 닿는 다리는 `annotate.py`가 미리 달아 둔 키워드다 —
"연준 금리 인하"의 "연준"은 제목(Fed)에는 없고 키워드에만 있다.

키워드만으로 못 닿는 질문이 있어 세 가지를 더 본다(2026-11-04 예비 측정의 실패 갈래,
`docs/polymarket-consensus-qa.md` 2절).

- **용어집**: 낱말마다 같은 뜻의 다른 표기를 함께 찾는다. "또 올릴까"는 키워드의 "인상"에,
  "금값"은 제목의 Gold에 닿아야 한다. 형태소 분석기 대신 용어집이다.
- **기관**: 질문이 일본은행을 말하면 연준 질문은 뒤로 보낸다. "금리 인상" 두 낱말이 맞는다고
  다른 중앙은행의 질문이 1위로 올라오던 것이다.
- **기간**: "12월"·"연말" 같은 말은 낱말로 찾지 않고 마감일 범위로 바꾼다. 같은 비트코인 질문이라도
  연말에 끝나는 쪽이 오늘 끝나는 쪽보다 앞선다.

정렬은 **다른 기관이 아닌가 → 맞은 낱말 수 → 질문의 기간에 마감하는가 → 칸 가중 점수 →
24시간 참여 규모** 순이다. 참여 규모는 동점을 가를 때만 쓴다. 관련 없는 대형 event가 위로
올라오면 검색이 아니라 순위표가 된다.

계획서: `docs/polymarket-nl-search.md` 5절, `docs/polymarket-consensus-qa.md`
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import re
import unicodedata
from typing import Any, NamedTuple

# 칸별 가중. 키워드가 가장 무겁다 — 한국어 질문이 닿는 자리가 거기다.
WEIGHT_KEYWORDS = 4
WEIGHT_TITLE = 3
WEIGHT_SUBTOPIC = 2
WEIGHT_SUMMARY = 2
WEIGHT_LABELS = 2
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
# 기간을 말하는 표현. 낱말로 찾지 않고 마감일 범위(`query_window`)로 쓴다.
_TIME = re.compile(
    r"\d{1,2}\s*월(?:\s*\d{1,2}\s*일)?|20\d\d\s*년|연말|연내|올해\s*(?:말|안에?)?|금년|내년|"
    r"이번\s*(?:달|주)|이달|다음\s*달"
)
# 한 글자라도 버리지 않는 낱말. "금 가격"의 "금", "또 올릴까"의 "또"다.
_ONE_CHAR = {"금", "또"}

# 같은 뜻의 다른 표기. 첫 칸은 사용자의 낱말 그대로 찾고, 여기 적은 말은 낱말 전체로만 찾는다
# ("cut"이 "cute"에 걸리지 않게 — 그래서 복수형을 따로 적는다). 열쇠는 조사를 뗀 형태다.
_HIKE = ("인상", "hike", "hikes", "increase", "raise")
_CUT = ("인하", "cut", "cuts", "decrease")
_RISE = ("상승", "hit", "above", "reach", "high", "rise")
_FALL = ("하락", "hit", "below", "low", "fall", "drop")
_HIGH = ("all-time high", "record high", "ath", "hit", "high", "record")
_FED = ("연준", "fed", "fomc", "federal reserve")
GLOSSARY: dict[str, tuple[str, ...]] = {
    # 통화정책·거시
    "금리": ("rate", "rates", "기준금리"),
    "기준금리": ("금리", "rate", "rates"),
    "인상": _HIKE, "인하": _CUT,
    "동결": ("no change", "pause", "hold"),
    "연준": _FED, "연방준비제도": _FED, "fomc": _FED,
    "일본은행": ("bank of japan", "boj"), "일은": ("일본은행", "bank of japan", "boj"),
    "한국은행": ("bank of korea",), "한은": ("한국은행", "bank of korea"),
    "유럽중앙은행": ("ecb", "european central bank"), "영란은행": ("bank of england", "boe"),
    "인민은행": ("pboc", "people's bank of china"),
    "경기침체": ("recession", "침체"), "침체": ("recession", "경기침체"), "불황": ("recession", "경기침체"),
    "실업률": ("unemployment", "실업"), "실업": ("unemployment", "실업률"),
    "물가": ("inflation", "cpi", "인플레이션"), "인플레이션": ("inflation", "cpi", "물가"),
    "인플레": ("inflation", "cpi", "물가"), "cpi": ("inflation", "물가"),
    "국채": ("treasury", "yield", "yields"), "성장률": ("gdp", "growth"), "gdp": ("성장률",),
    "셧다운": ("shutdown",), "관세": ("tariff", "tariffs"),
    "또": ("another", "again"), "다시": ("another", "again"), "추가": ("another", "additional"),
    # 자산·지수
    "금값": ("gold", "금 가격"), "금": ("gold", "금값", "금 가격"), "은값": ("silver",),
    "유가": ("oil", "crude", "wti", "brent"), "원유": ("oil", "crude", "wti", "brent", "유가"),
    "가격": ("price",), "주가": ("price", "stock"),
    "시가총액": ("market cap", "largest company"), "시총": ("market cap", "largest company", "시가총액"),
    "환율": ("exchange rate", "usd/krw", "krw"), "원달러": ("krw", "korean won"),
    "엔화": ("yen", "jpy"), "달러": ("dollar", "usd"),
    "코스피": ("kospi",), "코스닥": ("kosdaq",), "나스닥": ("nasdaq",), "다우": ("dow",),
    "닛케이": ("nikkei",), "니케이": ("nikkei",), "에스앤피": ("s&p", "s&p 500", "spx"),
    "비트코인": ("bitcoin", "btc"), "비트": ("bitcoin", "btc"), "이더리움": ("ethereum",),
    "이더": ("ethereum",), "솔라나": ("solana",), "리플": ("xrp", "ripple"), "도지": ("dogecoin", "doge"),
    "상장": ("ipo", "listing", "public"), "최고치": _HIGH, "신고가": _HIGH, "사상": ("all-time", "record"),
    "최저치": ("low", "lowest", "hit"),
    # 기업
    "엔비디아": ("nvidia", "nvda"), "애플": ("apple", "aapl"), "마이크로소프트": ("microsoft", "msft"),
    "테슬라": ("tesla", "tsla"), "구글": ("google", "alphabet"), "알파벳": ("alphabet", "google"),
    "아마존": ("amazon", "amzn"), "메타": ("meta",), "넷플릭스": ("netflix",), "삼성": ("samsung",),
    "삼성전자": ("samsung",), "앤스로픽": ("anthropic",), "앤트로픽": ("anthropic",),
    "엔트로픽": ("anthropic",), "오픈ai": ("openai",), "오픈에이아이": ("openai",),
    "스페이스x": ("spacex",), "인공지능": ("ai",),
    # 인물·국가·사건
    "트럼프": ("trump",), "파월": ("powell",), "밴스": ("vance",), "시진핑": ("xi jinping", "xi"),
    "푸틴": ("putin",), "젤렌스키": ("zelensky", "zelenskyy"), "네타냐후": ("netanyahu",),
    "머스크": ("musk", "elon"), "이란": ("iran",), "이스라엘": ("israel",), "우크라이나": ("ukraine",),
    "러시아": ("russia",), "중국": ("china",), "대만": ("taiwan",), "북한": ("north korea",),
    "일본": ("japan",), "한국": ("south korea", "korea"), "가자": ("gaza",), "하마스": ("hamas",),
    "호르무즈": ("hormuz",), "베네수엘라": ("venezuela",),
    "휴전": ("ceasefire",), "전쟁": ("war",), "침공": ("invade", "invasion"),
    "대법원": ("supreme court", "scotus"), "탄핵": ("impeach", "impeachment"),
    "선거": ("election",), "대선": ("presidential election", "president"), "총선": ("election",),
    "당선": ("win", "winner"), "우승": ("win", "winner", "champion"), "승리": ("win", "winner"),
}
# 활용형이 많은 서술어는 줄기로 찾는다("올릴까"·"올릴지"·"올리나"). (줄기들, 같은 뜻)
_STEMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("올릴", "올리", "올려"), _HIKE),
    (("내릴", "내리", "내려"), _CUT),
    (("오를", "오르", "올라"), _RISE),
    (("떨어", "하락"), _FALL),
    (("동결",), GLOSSARY["동결"]),
)

# 중앙은행 이름. 질문과 event가 서로 다른 기관을 가리키면 event를 뒤로 보낸다. 줄임말 한 글자
# 어긋남이 낱말 안에 숨기 쉬운 한국어 줄임말("한은"·"일은")은 event 쪽에서 찾지 않는다.
INSTITUTIONS: dict[str, re.Pattern[str]] = {
    key: re.compile(pattern)
    for key, pattern in {
        "fed": r"(?<![a-z])(?:fed|fomc|federal reserve)(?![a-z])|연준|연방준비",
        "boj": r"bank of japan|(?<![a-z])boj(?![a-z])|일본은행",
        "bok": r"bank of korea|한국은행",
        "ecb": r"(?<![a-z])ecb(?![a-z])|european central bank|유럽중앙은행",
        "boe": r"bank of england|(?<![a-z])boe(?![a-z])|영란은행",
        "pboc": r"(?<![a-z])pboc(?![a-z])|people'?s bank of china|인민은행",
        "boc": r"bank of canada|캐나다 ?중앙은행",
        "rba": r"reserve bank of australia|(?<![a-z])rba(?![a-z])|호주 ?중앙은행",
        "rbi": r"reserve bank of india|(?<![a-z])rbi(?![a-z])|인도 ?중앙은행",
        "banxico": r"bank of mexico|banxico|멕시코 ?중앙은행",
        "snb": r"swiss national bank|(?<![a-z])snb(?![a-z])|스위스 ?중앙은행",
    }.items()
}
# 위 표에 없는 중앙은행("Central Bank of Colombia")도 서로 다른 기관으로 본다. 나라 이름이 열쇠다 —
# "Bank of Japan"은 boj와 함께 "japan"으로도 잡혀 같은 질문끼리는 겹친다.
_ANY_CENTRAL_BANK = re.compile(r"(?:central bank|reserve bank|national bank|bank) of ([a-z]+)")


def _named_banks(text: str) -> set[str]:
    named = {key for key, pattern in INSTITUTIONS.items() if pattern.search(text)}
    return named | set(_ANY_CENTRAL_BANK.findall(text))


class Rank(NamedTuple):
    """event 하나의 순위 재료. `order()`가 정렬 열쇠로 바꾼다."""

    matched: int  # 맞은 낱말 수
    total: int  # 칸 가중 점수(한 낱말은 가장 무거운 칸 하나로만 센다)
    fit: int  # 질문의 기간 안에 마감하면 1
    clash: bool  # 질문과 다른 중앙은행을 가리키면 True


def order(rank: Rank) -> tuple[bool, int, int, int]:
    return (not rank.clash, rank.matched, rank.fit, rank.total)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def query_tokens(query: str) -> list[str]:
    """질문을 검색 낱말로 쪼갠다. 기간 표현은 빼고(`query_window`가 쓴다) 조사를 뗀다.

    전부 불용어라 남는 것이 없으면("폴리마켓 배팅") 불용어를 되살린다 —
    무엇을 찾는지 모르는 질문에 빈 결과를 돌려주는 것보다 그 말 그대로 찾는
    편이 낫다.
    """
    raw = [token for token in _TOKEN.findall(_TIME.sub(" ", normalize(query))) if token]
    tokens: list[str] = []
    for token in raw:
        if token in _STOP:
            continue
        stripped = _SUFFIX.sub("", token)
        token = stripped if len(stripped) >= 2 or stripped in _ONE_CHAR else token
        if token in _STOP or len(token) < 2 and not _ASCII.fullmatch(token) and token not in _ONE_CHAR:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens or list(dict.fromkeys(raw))


def expand(tokens: list[str]) -> list[list[str]]:
    """낱말마다 [사용자의 낱말, 같은 뜻의 다른 표기…]. 다른 표기 중 하나만 맞아도 그 낱말이 맞은 것이다."""
    groups = []
    for token in tokens:
        alternatives = list(GLOSSARY.get(token, ()))
        for stems, meanings in _STEMS:
            if token.startswith(stems):
                alternatives += meanings
        groups.append([token, *dict.fromkeys(a for a in alternatives if a != token)])
    return groups


def minimum_matches(groups: list[Any]) -> int:
    """결과로 남기려면 맞아야 하는 낱말 수. 절반 이상이다.

    전부 맞기를 요구하면 "트럼프 관세 한국 자동차"처럼 긴 질문이 0건이 되고,
    하나만 맞아도 되면 "한국" 하나로 수백 건이 걸린다.
    """
    return max(1, (len(groups) + 1) // 2)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def query_window(query: str, today: date) -> tuple[date, date] | None:
    """질문이 말하는 기간을 마감일 범위로. 기간을 말하지 않으면 None.

    범위 끝에 여유를 둔다 — "12월 연준 결정"은 12월 10일에, "11월 유가 최고치"는 12월 1일
    새벽(UTC)에 마감한다.
    """
    text = normalize(query)
    years = re.search(r"(20\d\d)\s*년", text)
    year = int(years.group(1)) if years else None
    month = re.search(r"(\d{1,2})\s*월", text)
    if month and 1 <= int(month.group(1)) <= 12:
        number = int(month.group(1))
        target = year or (today.year if number >= today.month else today.year + 1)
        return date(target, number, 1), _month_end(target, number) + timedelta(days=7)
    if re.search(r"연말|연내|올해\s*(?:말|안)", text):
        target = year or today.year
        return date(target, 12, 1), date(target + 1, 1, 15)
    if year:
        return date(year, 1, 1), date(year + 1, 1, 15)
    if re.search(r"올해|금년", text):
        return today, date(today.year + 1, 1, 15)
    if "내년" in text:
        return date(today.year + 1, 1, 1), date(today.year + 2, 1, 15)
    if re.search(r"이번\s*달|이달", text):
        return today, _month_end(today.year, today.month) + timedelta(days=7)
    if re.search(r"다음\s*달", text):
        first = _month_end(today.year, today.month) + timedelta(days=1)
        return first, _month_end(first.year, first.month) + timedelta(days=7)
    if re.search(r"이번\s*주", text):
        return today, today + timedelta(days=8)
    return None


def institutions(groups: list[list[str]]) -> frozenset[str]:
    """질문(용어집으로 넓힌 낱말들)이 가리키는 중앙은행."""
    return frozenset(_named_banks(" ".join(normalize(token) for group in groups for token in group)))


class Term(NamedTuple):
    """검색 낱말 하나. 영문 표기는 정규식 하나로 묶고, 나머지는 글자열로 찾는다."""

    pattern: re.Pattern[str] | None
    texts: tuple[tuple[str, str], ...]  # (원형, 공백 뺀 형태)


def compile_tokens(groups: list[list[str]]) -> list[Term]:
    """낱말마다 영문 표기 전부를 정규식 하나로 묶는다.

    표기마다 따로 찾으면 event 21,872건 × 표기 수만큼 정규식을 돌려 검색 한 번이 0.5초를 넘는다.
    """
    terms = []
    for group in groups:
        parts, texts = [], []
        for position, token in enumerate(group):
            if _ASCII.fullmatch(token):
                # 사용자의 영문 낱말은 앞머리부터 맞춘다. 입력 중인 "tru"도 Trump에 닿게
                # 하되 "ai"가 "said"에 걸리지 않게 한다. 두 글자 이하는 단어 전체만.
                # 용어집 표기(둘째 칸부터)는 완성된 낱말이라 언제나 단어 전체로만 찾는다.
                parts.append(re.escape(token) + (r"(?![a-z0-9])" if position or len(token) <= 2 else ""))
            else:
                texts.append((token, token.replace(" ", "")))
        pattern = re.compile(r"(?<![a-z0-9])(?:" + "|".join(parts) + ")") if parts else None
        terms.append(Term(pattern, tuple(texts)))
    return terms


def prepare_annotation(annotation: dict[str, Any]) -> tuple[str, str, str, str]:
    """색인 한 행을 검색용 문자열 넷으로 줄인다: (키워드, 세부 주제, 요약, 선택지 이름).

    목록을 문자열 하나로 합쳐 두는 것은 메모리 때문이다. 웹 프로세스는
    `MemoryMax=256M`이고 21,872건의 키워드 목록을 객체째 들면 몇 배로 불어난다.
    """
    keywords = " | ".join(normalize(value) for value in annotation.get("keywords") or [])
    labels = " | ".join(normalize(value) for value in annotation.get("labels") or [])
    return keywords, str(annotation.get("subtopic") or ""), str(annotation.get("summary") or ""), labels


def _hit(field: str, term: Term, squeezed: str | None = None) -> bool:
    if not field:
        return False
    if term.pattern is not None and term.pattern.search(field) is not None:
        return True
    if not term.texts:
        return False
    # 한국어는 띄어쓰기가 흔들린다("금리인하" ↔ "금리 인하"). 둘 다 본다.
    squeezed = field.replace(" ", "") if squeezed is None else squeezed
    return any(token in field or compact in squeezed for token, compact in term.texts)


def mentions(text: str, terms: list[Term]) -> bool:
    """글에 검색 낱말 중 하나라도 들었는가(용어집 표기 포함). 질의응답이 물은 선택지를 찾을 때 쓴다."""
    field = normalize(text)
    return any(_hit(field, term) for term in terms)


def _end_date(event: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(str(event.get("end_date"))).date()
    except (TypeError, ValueError):
        return None


def score(
    compiled: list[Term],
    event: dict[str, Any],
    annotation: tuple[str, str, str, str] | None,
    *,
    needed: int = 1,
    window: tuple[date, date] | None = None,
    wanted: frozenset[str] = frozenset(),
) -> Rank | None:
    """맞은 낱말이 `needed`개에 못 미치면 None. 아니면 순위 재료.

    먼저 모든 칸을 이은 문자열에서 낱말이 있는지만 본다. 대부분의 event가 여기서 떨어지고,
    남은 것만 칸별로 다시 나눠 가중을 매긴다 — 21,872건마다 칸을 따로 정규화하고 낱말마다
    칸 여섯을 다 보면 검색 한 번이 느려진다.
    """
    keywords, subtopic, summary, labels = annotation or ("", "", "", "")
    tag_text = " ".join(str(tag) for tag in event.get("tags") or [])
    joined = " | ".join((normalize(f"{event.get('title') or ''} | {subtopic} | {summary} | {tag_text}"), keywords, labels))
    squeezed = joined.replace(" ", "")
    present = [term for term in compiled if _hit(joined, term, squeezed)]
    if len(present) < needed:
        return None
    title = normalize(event.get("title"))
    tags = normalize(tag_text)
    subtopic, summary = normalize(subtopic), normalize(summary)
    fields = (
        (WEIGHT_KEYWORDS, keywords),
        (WEIGHT_TITLE, title),
        (WEIGHT_SUBTOPIC, subtopic),
        (WEIGHT_SUMMARY, summary),
        (WEIGHT_LABELS, labels),
        (WEIGHT_TAGS, tags),
    )
    total = sum(max((weight for weight, field in fields if _hit(field, term)), default=0) for term in present)
    end = _end_date(event)
    fit = int(window is not None and end is not None and window[0] <= end <= window[1])
    clash = False
    if wanted:
        named = _named_banks(" ".join((title, keywords, summary)))
        clash = bool(named) and not named & wanted
    return Rank(len(present), total, fit, clash)
