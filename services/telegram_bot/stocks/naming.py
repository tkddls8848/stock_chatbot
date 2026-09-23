"""종목명을 뉴스 본문 매칭용 용어로 쪼갠다.

종목 이름에 대한 지식이라 종목 계층이 소유한다. 리서치 후보 발굴과 뉴스
사전선별이 **각각** 이 함수를 부른다 — 예전에는 사전선별이 리서치의 비공개
함수를 직접 가져다 써서, 리서치를 고치면 사전선별이 같이 흔들렸다.
"""

import re
from collections import Counter

from services.telegram_bot.core.config import STOCK_NAME_TOKEN_MAX_FREQUENCY

_NAME_TOKEN_STOPWORDS = {
    "inc", "corp", "corporation", "incorporated", "company", "ltd", "limited",
    "plc", "group", "holdings", "holding", "class", "common", "stock", "stocks",
    "share", "shares", "ordinary", "preferred", "series", "trust", "fund",
    "depositary", "depository", "adr", "ads", "notes", "warrant", "warrants",
    "unit", "units", "right", "rights", "the", "and", "of", "new", "each",
    "per", "value", "beneficial", "interest", "interests", "capital",
    "international", "acquisition", "representing",
}


def display_name(entry: dict[str, str]) -> str:
    return str(entry.get("display_name") or entry.get("ko_name") or "").strip()


def english_name_tokens(value: str) -> list[str]:
    """영문 종목명 → 매칭 후보 토큰(4자 이상, 법인 형태 등 일반 단어 제외)."""
    return [
        token
        for token in re.findall(r"[A-Za-z]{4,}", value)
        if token.lower() not in _NAME_TOKEN_STOPWORDS
    ]


def build_name_token_frequency(stock_entries: list[dict[str, str]]) -> Counter:
    """토큰별로 그 토큰을 이름에 가진 종목 수를 센다.

    'TECH'·'ENERGY'처럼 수십~수백 종목이 공유하는 토큰은 뉴스에 한 번 나오면
    무관한 종목을 무더기로 끌어온다("Big Tech earnings" → 이름에 TECH가 든
    모든 종목). 어느 단어가 흔한지는 시장마다 다르므로 목록을 손으로 관리하지
    않고 종목 DB에서 직접 센다.
    """
    frequency: Counter = Counter()
    for entry in stock_entries:
        tokens: set[str] = set()
        for value in (
            str(entry.get("cn_name") or "").strip(),
            display_name(entry),
        ):
            if value and value.isascii():
                tokens.update(token.lower() for token in english_name_tokens(value))
        frequency.update(tokens)
    return frequency


def entry_match_terms(
    entry: dict[str, str],
    token_frequency: Counter | None = None,
    max_token_frequency: int = STOCK_NAME_TOKEN_MAX_FREQUENCY,
) -> list[str]:
    """뉴스 본문 매칭용 종목명 용어.

    영문명은 일반 단어를 제외한 토큰으로 나누고, 여러 종목이 공유하는 흔한
    토큰은 버린다. 남는 토큰이 없으면 이 종목은 이름 매칭 대상에서 빠진다.
    중국어·한국어 이름은 통째로 쓰므로 이 필터를 거치지 않는다.
    """
    terms: list[str] = []
    for value in (
        str(entry.get("cn_name") or "").strip(),
        display_name(entry),
    ):
        if not value:
            continue
        if value.isascii():
            for token in english_name_tokens(value):
                if (
                    token_frequency is not None
                    and token_frequency.get(token.lower(), 0) > max_token_frequency
                ):
                    continue
                terms.append(token)
        else:
            terms.append(value)
    return list(dict.fromkeys(terms))
