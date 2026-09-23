"""종목명 다중 패턴 매칭.

리서치 후보가 쓰는 이름 정제 규칙은 재사용하되, 기사마다 종목별 정규식을
1.7만 번 돌리지 않도록 모든 용어를 하나의 Aho-Corasick automaton에 넣는다.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass

from services.telegram_bot.stocks.naming import (
    build_name_token_frequency,
    entry_match_terms,
)


@dataclass(frozen=True)
class EntityMatches:
    codes: frozenset[str]
    terms: frozenset[str]


class StockEntityMatcher:
    """전체 종목명 용어를 한 번에 검색하는 compact automaton."""

    def __init__(self, stock_entries: list[dict[str, str]]):
        token_frequency = build_name_token_frequency(stock_entries)
        term_codes: dict[str, set[str]] = defaultdict(set)
        ascii_requirements: dict[str, int] = {}

        for entry in stock_entries:
            code = str(entry.get("code") or "").strip()
            if not code:
                continue
            terms = entry_match_terms(entry, token_frequency)
            ascii_terms = {term.lower() for term in terms if term.isascii()}
            if ascii_terms:
                ascii_requirements[code] = min(2, len(ascii_terms))
            for term in terms:
                normalized = term.lower() if term.isascii() else term
                if len(normalized.strip()) < 2:
                    continue
                term_codes[normalized].add(code)

        self._terms = tuple(term_codes)
        self._term_codes = {
            term: frozenset(codes) for term, codes in term_codes.items()
        }
        self._ascii_requirements = ascii_requirements
        self._goto: list[dict[str, int]] = [{}]
        self._failure: list[int] = [0]
        self._outputs: list[list[int]] = [[]]
        self._build_automaton()

    @property
    def pattern_count(self) -> int:
        return len(self._terms)

    def _build_automaton(self) -> None:
        for term_index, term in enumerate(self._terms):
            state = 0
            for char in term:
                next_state = self._goto[state].get(char)
                if next_state is None:
                    next_state = len(self._goto)
                    self._goto[state][char] = next_state
                    self._goto.append({})
                    self._failure.append(0)
                    self._outputs.append([])
                state = next_state
            self._outputs[state].append(term_index)

        queue: deque[int] = deque()
        queue.extend(self._goto[0].values())
        while queue:
            state = queue.popleft()
            for char, next_state in self._goto[state].items():
                queue.append(next_state)
                fallback = self._failure[state]
                while fallback and char not in self._goto[fallback]:
                    fallback = self._failure[fallback]
                self._failure[next_state] = self._goto[fallback].get(char, 0)
                inherited = self._outputs[self._failure[next_state]]
                if inherited:
                    self._outputs[next_state].extend(inherited)

    @staticmethod
    def _valid_boundary(text: str, start: int, end: int, term: str) -> bool:
        if term.isascii():
            before = text[start - 1] if start else ""
            after = text[end] if end < len(text) else ""
            return not (before.isalnum() or before == "_") and not (
                after.isalnum() or after == "_"
            )
        if any("가" <= char <= "힣" for char in term):
            return start == 0 or not ("가" <= text[start - 1] <= "힣")
        return True

    def match(self, text: str) -> EntityMatches:
        lowered = str(text or "").lower()
        state = 0
        matched_terms: set[str] = set()
        ascii_hits: Counter[str] = Counter()
        cjk_codes: set[str] = set()

        for position, char in enumerate(lowered):
            while state and char not in self._goto[state]:
                state = self._failure[state]
            state = self._goto[state].get(char, 0)
            for term_index in self._outputs[state]:
                term = self._terms[term_index]
                start = position - len(term) + 1
                end = position + 1
                if term in matched_terms or not self._valid_boundary(
                    lowered, start, end, term
                ):
                    continue
                matched_terms.add(term)
                codes = self._term_codes[term]
                if term.isascii():
                    for code in codes:
                        ascii_hits[code] += 1
                else:
                    cjk_codes.update(codes)

        codes = set(cjk_codes)
        codes.update(
            code
            for code, hits in ascii_hits.items()
            if hits >= self._ascii_requirements.get(code, 1)
        )
        return EntityMatches(frozenset(codes), frozenset(matched_terms))

