import logging
import re
from collections import Counter
from typing import Any

from shared.core.config import (
    RESEARCH_NAME_TOKEN_MAX_FREQUENCY,
)
from telegram_bot.stocks import StockDatabase

logger = logging.getLogger(__name__)

# 영문 종목명 토큰 매칭에서 제외할 일반 단어(법인 형태·증권 유형 등).
_NAME_TOKEN_STOPWORDS = {
    "inc", "corp", "corporation", "incorporated", "company", "ltd", "limited",
    "plc", "group", "holdings", "holding", "class", "common", "stock", "stocks",
    "share", "shares", "ordinary", "preferred", "series", "trust", "fund",
    "depositary", "depository", "adr", "ads", "notes", "warrant", "warrants",
    "unit", "units", "right", "rights", "the", "and", "of", "new", "each",
    "per", "value", "beneficial", "interest", "interests", "capital",
    "international", "acquisition", "representing",
}


def _name_is_matchable(name: str) -> bool:
    return len(name.strip()) >= 3


def _entry_display_name(entry: dict[str, str]) -> str:
    return str(entry.get("display_name") or entry.get("ko_name") or "").strip()


def _build_pattern_matcher(patterns: list[str], whole_word: bool = False):
    """패턴 목록 → 텍스트 매칭 함수.

    기본(종목명 매칭용): 중국어·한글은 부분 문자열, 영어는 단어 경계 기준
    (2자 이하는 완전 일치, 3자 이상은 접두 일치).
    whole_word=True(뉴스 본문 매칭용): 영어는 완전 단어 일치, 한글은 앞글자
    경계를 요구해 "이닉스"가 "하이닉스"에 걸리는 오탐을 막는다(조사는 허용).
    """
    substring: list[str] = []
    regex_parts: list[str] = []
    for pattern in dict.fromkeys(p for p in patterns if p):
        escaped = re.escape(pattern)
        if pattern.isascii():
            if whole_word or len(pattern) <= 2:
                regex_parts.append(rf"\b{escaped}\b")
            else:
                regex_parts.append(rf"\b{escaped}")
        elif whole_word and re.search(r"[가-힣]", pattern):
            regex_parts.append(rf"(?<![가-힣]){escaped}")
        else:
            substring.append(pattern)
    regex = re.compile("|".join(regex_parts), re.IGNORECASE) if regex_parts else None

    def matches(text: str) -> bool:
        if any(p in text for p in substring):
            return True
        return regex is not None and regex.search(text) is not None

    return matches


def _english_name_tokens(value: str) -> list[str]:
    """영문 종목명 → 매칭 후보 토큰(4자 이상, 법인 형태 등 일반 단어 제외)."""
    return [
        token
        for token in re.findall(r"[A-Za-z]{4,}", value)
        if token.lower() not in _NAME_TOKEN_STOPWORDS
    ]


def _build_name_token_frequency(stock_entries: list[dict[str, str]]) -> Counter:
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
            _entry_display_name(entry),
        ):
            if value and value.isascii():
                tokens.update(token.lower() for token in _english_name_tokens(value))
        frequency.update(tokens)
    return frequency


def _entry_match_terms(
    entry: dict[str, str],
    token_frequency: Counter | None = None,
    max_token_frequency: int = RESEARCH_NAME_TOKEN_MAX_FREQUENCY,
) -> list[str]:
    """뉴스 본문 매칭용 종목명 용어.

    영문명은 일반 단어를 제외한 토큰으로 나누고, 여러 종목이 공유하는 흔한
    토큰은 버린다. 남는 토큰이 없으면 이 종목은 이름 매칭 대상에서 빠진다.
    중국어·한국어 이름은 통째로 쓰므로 이 필터를 거치지 않는다.
    """
    terms: list[str] = []
    for value in (
        str(entry.get("cn_name") or "").strip(),
        _entry_display_name(entry),
    ):
        if not value:
            continue
        if value.isascii():
            for token in _english_name_tokens(value):
                if (
                    token_frequency is not None
                    and token_frequency.get(token.lower(), 0) > max_token_frequency
                ):
                    continue
                terms.append(token)
        else:
            terms.append(value)
    return list(dict.fromkeys(terms))


def _build_name_matcher(match_terms: list[str]):
    """뉴스 본문에서 이 종목을 가리키는지 판정한다.

    영문명은 **서로 다른 토큰 2개 이상**이 같은 기사에 나와야 한다. 종목 DB에서는
    드물지만 영어 기사에서는 흔한 단어(Daily, Home, Better...)가 한 개만 걸려도
    무관한 종목이 후보로 올라오기 때문이다("Daily Journal" ← 기사 속 daily).
    이름이 한 단어면 그 자체가 고유하므로 1개로 충분하다.
    중국어·한국어 이름은 통째로 쓰며 기존 경계 규칙을 그대로 따른다.
    """
    cjk_terms = [term for term in match_terms if not term.isascii()]
    tokens = [term for term in match_terms if term.isascii()]
    cjk_matcher = _build_pattern_matcher(cjk_terms, whole_word=True) if cjk_terms else None
    token_patterns = [
        re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE) for token in tokens
    ]
    required = min(2, len(token_patterns))

    def matches(text: str) -> bool:
        if cjk_matcher is not None and cjk_matcher(text):
            return True
        hits = 0
        for pattern in token_patterns:
            if pattern.search(text):
                hits += 1
                if hits >= required:
                    return True
        return False

    return matches


def build_research_candidate_universe(
    stock_db: StockDatabase,
    watchlist: dict[str, str],
    news_items: list[dict[str, Any]],
    max_candidates: int = 30,
    name_token_max_frequency: int = RESEARCH_NAME_TOKEN_MAX_FREQUENCY,
) -> list[dict[str, Any]]:
    """후보 universe를 근거 강도 순으로 만든다.

    관심종목과 원문 뉴스에서 이름이 직접 확인된 종목만 포함한다.
    """
    candidates: dict[str, dict[str, Any]] = {}
    haystacks = [
        f"{item.get('title', '')}\n{item.get('content', '')}"
        for item in news_items
    ]
    all_text = "\n".join(haystacks)
    all_text_lower = all_text.lower()
    stock_entries = stock_db.get_candidate_universe()
    for code, name in watchlist.items():
        candidates[code] = {
            "code": code,
            "name": name,
            "market": "",
            "in_watchlist": True,
            "matched_news": [],
        }

    token_frequency = _build_name_token_frequency(stock_entries)
    for entry in stock_entries:
        code = str(entry.get("code") or "")
        name = _entry_display_name(entry)
        if not code or not name or not _name_is_matchable(name):
            continue
        match_terms = _entry_match_terms(entry, token_frequency, name_token_max_frequency)
        if not match_terms:
            continue
        # 값싼 부분 문자열 사전 필터: 전체 뉴스에 안 나오는 이름은 정규식 생략
        if not any(
            (term.lower() in all_text_lower) if term.isascii() else (term in all_text)
            for term in match_terms
        ):
            continue
        name_matcher = _build_name_matcher(match_terms)

        matched_news = []
        for item, haystack in zip(news_items, haystacks):
            if name_matcher(haystack):
                matched_news.append(
                    {
                        "title": item.get("title", ""),
                        "source": item.get("source", ""),
                        "published_at": item.get("published_at", ""),
                        "url": item.get("url", ""),
                    }
                )
                item.setdefault("matched_candidates", [])
                item["matched_candidates"].append(
                    {
                        "code": code,
                        "name": name,
                        "market": entry.get("market", ""),
                    }
                )

        if not matched_news:
            continue

        candidates[code] = {
            "code": code,
            "name": name,
            "market": entry.get("market", ""),
            "in_watchlist": code in watchlist,
            "matched_news": matched_news[:3],
        }

    return _prioritize_candidates(list(candidates.values()), max_candidates)


def _prioritize_candidates(
    values: list[dict[str, Any]], max_candidates: int
) -> list[dict[str, Any]]:
    """상한 적용 순서: 관심종목 → 뉴스 근거 보유 후보."""
    watch = [c for c in values if c.get("in_watchlist")]
    evidenced = [c for c in values if not c.get("in_watchlist") and c.get("matched_news")]
    return (watch + evidenced)[:max_candidates]
