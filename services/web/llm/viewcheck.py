"""뷰 검증의 모델 호출 셋: 뷰를 전제로 나누기 → 전제를 컨센서스에 대조하기 → 포지션 아이디어.

**운영자 개인용이다**(`core/config.py`의 `VIEW_CHECK_ACCOUNT_KEYS`). 질의응답(`llm/polymarket_answer.py`)과 같은 원칙이다. 모델은 입력 번호로만 가리키고(전제 `c`, 후보 `n`,
선택지 `m`, 상품 `code`) 확률은 등급으로만 본다. 화면의 숫자와 상품 이름은 서버(`viewcheck/service.py`)가
붙인다. 호출마다 반려되면 한 번만 다시 부른다.

검사가 질의응답과 다른 점은 둘이다.

- **출처 이름만 막고 "매수"·"매도"라는 낱말은 받는다.** 여기서는 사용자의 뷰를 다시 쓴다 — "외국인
  매수세가 이어진다"는 뷰를 낱말 때문에 반려할 수 없다. 대신 지시형 권유("사세요")와 보장 표현을 막는다.
- **입력에 적힌 백분율은 다시 써도 된다.** 뷰의 "10년물 금리 5%"나 선택지 이름의 "4.76%"(국채 수익률 칸)는
  확률이 아니라 기준값이다. 입력에 없는 백분율은 모델이 만든 확률로 보고 반려한다.

계획서: `services/web/docs/consensus-view-advisor.md`
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from services.web.llm.backends import LLMBackend, LLMBackendError, TokenUsage
from services.web.llm.polymarket_brief import SOURCE_NAME, has_model_probability
from services.web.llm.terminology import read_prompt

logger = logging.getLogger(__name__)

VERDICTS = ("supported", "contradicted", "mixed", "unpriced")
STANCES = ("long", "reduce", "hedge", "wait")
MAX_CLAIMS = 5
MAX_QUERIES = 3
MAX_PICKS = 3
MAX_FOCUS = 2
MAX_BLIND_SPOTS = 3
MAX_POSITIONS = 6
# 글 길이(자). 스키마의 maxLength는 넉넉히 두고 여기서 화면에 낼 범위를 정한다.
CLAIM_CHARS = (8, 90)
QUERY_CHARS = (2, 30)
NOTE_CHARS = (15, 220)
REFINED_CHARS = (40, 450)
REASON_CHARS = (30, 280)
INVALIDATION_CHARS = (15, 180)

# 지시형 권유와 보장. 포지션은 "검토할 아이디어"이지 주문이 아니다(`/terms`).
SOLICITATION = re.compile(
    r"사세요|파세요|사십시오|파십시오|매수\s*하(?:세요|십시오)|매도\s*하(?:세요|십시오)|가입\s*하세요"
    r"|반드시\s*오릅|반드시\s*내립|무조건|수익\s*보장|원금\s*보장|지금이\s*기회"
    # 방향은 stance가 말한다. 이유 문장이 "줄여야 한다"로 끝나면 아이디어가 주문처럼 읽힌다(2026-11-11 실측).
    r"|(?:늘려|줄여|사|팔아|담아|매수해|매도해|확대해|축소해|감소시켜|증가시켜)야\s*(?:한다|합니다|할)|바람직(?:하다|합니다)")
_PERCENT = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%p|%|퍼센트|프로)")


def _string(max_length: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": max_length}


def _schema(name: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "json_schema", "json_schema": {"name": name, "schema": {
        "type": "object", "additionalProperties": False, "properties": properties,
        "required": list(properties)}}}


def _items(properties: dict[str, Any], max_items: int) -> dict[str, Any]:
    return {"type": "array", "maxItems": max_items, "items": {
        "type": "object", "additionalProperties": False, "properties": properties,
        "required": list(properties)}}


_INTEGERS = {"type": "array", "maxItems": MAX_CLAIMS, "items": {"type": "integer"}}
CLAIMS_FORMAT = _schema("view_claims", {"claims": _items({
    "claim": _string(120),
    "queries": {"type": "array", "maxItems": MAX_QUERIES, "items": _string(40)},
}, MAX_CLAIMS)})
JUDGE_FORMAT = _schema("view_verdicts", {
    "claims": _items({
        "c": {"type": "integer"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "picks": _items({"n": {"type": "integer"},
                         "focus": {"type": "array", "maxItems": MAX_FOCUS, "items": {"type": "integer"}}}, MAX_PICKS),
        "note": _string(260),
    }, MAX_CLAIMS),
    "refined_view": _string(500),
    "blind_spots": _items({"n": {"type": "integer"}, "note": _string(200)}, MAX_BLIND_SPOTS),
})
POSITIONS_FORMAT = _schema("view_positions", {"positions": _items({
    "code": _string(40),
    "stance": {"type": "string", "enum": list(STANCES)},
    "claims": _INTEGERS,
    "reason": _string(320),
    "invalidation": _string(220),
}, MAX_POSITIONS)})


class ViewCheckError(RuntimeError):
    """그 단계의 결과를 쓸 수 없다 — 호출이 실패했거나(`rejected=False`) 두 번 다 반려됐다(`rejected=True`)."""

    def __init__(self, message: str, *, stage: str, rejected: bool):
        super().__init__(message)
        self.stage = stage
        self.rejected = rejected


class Rejected(ValueError):
    """응답 반려. 사유는 교정 지시로 모델에 돌아간다."""


@dataclass
class Claim:
    text: str
    queries: list[str]


@dataclass
class Pick:
    n: int
    focus: list[int]


@dataclass
class Verdict:
    c: int
    verdict: str
    picks: list[Pick]
    note: str | None


@dataclass
class Judgement:
    verdicts: list[Verdict]
    refined_view: str | None
    blind_spots: list[tuple[int, str]]
    # 글 하나가 검사에 걸려 비웠으면 그 사유. 한 번 다시 부를지 정하는 데만 쓴다.
    dropped: list[str] = field(default_factory=list)


@dataclass
class Position:
    code: str
    stance: str
    claims: list[int]
    reason: str
    invalidation: str | None


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _load(raw: str) -> dict[str, Any]:
    text = raw.strip()
    # /no_think를 붙여도 빈 thinking 블록이 앞에 붙어 오는 응답이 있다.
    if text.startswith("<think>") and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.startswith("```"):
        text = "\n".join(line for line in text.splitlines() if not line.startswith("```")).strip()
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise Rejected(f"JSON이 아닙니다: {error}") from error
    if not isinstance(payload, dict):
        raise Rejected("JSON 객체가 아닙니다")
    return payload


def text_problem(text: str, limits: tuple[int, int], *, known: str = "") -> str | None:
    """화면에 낼 수 없는 글이면 그 사유. `known`(뷰와 후보의 제목·선택지 이름)에 적힌 백분율은 확률로 보지 않는다."""
    if not limits[0] <= len(text) <= limits[1]:
        return f"길이가 {limits[0]}~{limits[1]}자를 벗어났습니다"
    if text.startswith(("{", "[")):
        return "문장이 아닙니다"
    if SOURCE_NAME.search(text):
        return "출처 서비스 이름·베팅 같은 말을 쓰지 마십시오"
    if SOLICITATION.search(text):
        return "사라·팔라는 지시나 보장 표현을 쓰지 마십시오"
    listed = {re.sub(r"[\s,]", "", token) for token in _PERCENT.findall(known)}
    rest = _PERCENT.sub(lambda match: "" if re.sub(r"[\s,]", "", match.group()) in listed else match.group(), text)
    if has_model_probability(rest):
        return "확률을 숫자로 쓰지 마십시오. 숫자는 서버가 붙입니다"
    return None


def parse_claims(raw: str) -> list[Claim]:
    claims: list[Claim] = []
    rows = _load(raw).get("claims")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        text = _text(row.get("claim"))
        queries = [query for query in dict.fromkeys(_text(value) for value in row.get("queries") or [])
                   if QUERY_CHARS[0] <= len(query) <= QUERY_CHARS[1]][:MAX_QUERIES]
        if (not CLAIM_CHARS[0] <= len(text) <= CLAIM_CHARS[1] or SOURCE_NAME.search(text) or not queries
                or any(claim.text == text for claim in claims)):
            continue
        claims.append(Claim(text, queries))
    if not claims:
        raise Rejected("검증할 전제가 하나도 없습니다. 뷰에 든 예상을 한 문장씩, 검색어와 함께 적으십시오")
    return claims[:MAX_CLAIMS]


def parse_judgement(raw: str, claim_ids: list[int], options: dict[int, set[int]], market: set[int],
                    *, known: str) -> Judgement:
    """판정을 검사한다. 없는 번호는 그 칸만 버리고, 근거 없는 판정은 "확인 못 함"으로 내린다.

    `options`는 후보 번호 → 그 후보의 선택지 번호들, `market`은 뷰와 무관하게 준 후보의 번호다.
    """
    payload = _load(raw)
    rows = payload.get("claims")
    if not isinstance(rows, list):
        raise Rejected("claims가 목록이 아닙니다")
    dropped: list[str] = []
    by_claim: dict[int, Verdict] = {}
    used: set[int] = set()
    for row in rows:
        c = row.get("c") if isinstance(row, dict) else None
        if not _int(c) or c not in claim_ids or c in by_claim:
            continue
        verdict = row.get("verdict") if row.get("verdict") in VERDICTS else "unpriced"
        picks: list[Pick] = []
        for pick in row.get("picks") if isinstance(row.get("picks"), list) else []:
            n = pick.get("n") if isinstance(pick, dict) else None
            if not _int(n) or n not in options or any(kept.n == n for kept in picks):
                continue
            focus = [m for m in dict.fromkeys(pick.get("focus") or []) if _int(m) and m in options[n]]
            picks.append(Pick(n, focus[:MAX_FOCUS]))
        picks = picks[:MAX_PICKS]
        # 근거로 든 질문이 없으면 판정이 아니다. 거꾸로 "확인 못 함"이면 근거를 보이지 않는다.
        if not picks:
            verdict = "unpriced"
        elif verdict == "unpriced":
            picks = []
        note = _text(row.get("note")) or None
        if note and (problem := text_problem(note, NOTE_CHARS, known=known)):
            dropped.append(f"note: {problem}")
            note = None
        used.update(pick.n for pick in picks)
        by_claim[c] = Verdict(c, verdict, picks, note)
    verdicts = [by_claim.get(c) or Verdict(c, "unpriced", [], None) for c in claim_ids]
    refined = _text(payload.get("refined_view")) or None
    if refined and (problem := text_problem(refined, REFINED_CHARS, known=known)):
        dropped.append(f"refined_view: {problem}")
        refined = None
    blind: list[tuple[int, str]] = []
    for row in payload.get("blind_spots") if isinstance(payload.get("blind_spots"), list) else []:
        n = row.get("n") if isinstance(row, dict) else None
        note = _text(row.get("note")) if isinstance(row, dict) else ""
        # 뷰 밖의 신호만 받는다. 전제의 근거로 이미 쓴 질문을 "놓친 신호"로 다시 내지 않는다.
        if not _int(n) or n not in market or n in used or any(n == kept for kept, _ in blind):
            continue
        if problem := text_problem(note, NOTE_CHARS, known=known):
            dropped.append(f"blind_spots: {problem}")
            continue
        blind.append((n, note))
    return Judgement(verdicts, refined, blind[:MAX_BLIND_SPOTS], dropped)


def parse_positions(raw: str, codes: set[str], claim_ids: list[int], *, known: str) -> tuple[list[Position], list[str]]:
    """포지션을 검사한다. 표에 없는 상품·근거 전제가 없는 줄·검사에 걸린 글은 그 줄만 버린다."""
    rows = _load(raw).get("positions")
    if not isinstance(rows, list):
        raise Rejected("positions가 목록이 아닙니다")
    index = {code.upper(): code for code in codes}
    positions: list[Position] = []
    dropped: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = index.get(_text(row.get("code")).upper())
        claims = [c for c in dict.fromkeys(row.get("claims") or []) if _int(c) and c in claim_ids]
        if (code is None or row.get("stance") not in STANCES or not claims
                or any(kept.code == code for kept in positions)):
            dropped.append("입력에 없는 code이거나 stance·claims가 맞지 않습니다")
            continue
        reason = _text(row.get("reason"))
        if problem := text_problem(reason, REASON_CHARS, known=known):
            dropped.append(f"reason: {problem}")
            continue
        invalidation = _text(row.get("invalidation")) or None
        if invalidation and (problem := text_problem(invalidation, INVALIDATION_CHARS, known=known)):
            dropped.append(f"invalidation: {problem}")
            invalidation = None
        positions.append(Position(code, row["stance"], claims, reason, invalidation))
    return positions[:MAX_POSITIONS], dropped


class ViewChecker:
    """세 단계를 같은 백엔드로 부른다. 한 번의 검증에 하나를 만들어 쓰고 `usages`로 비용을 모은다."""

    def __init__(self, backend: LLMBackend, *, claims_prompt: Path, judge_prompt: Path, positions_prompt: Path,
                 claims_tokens: int, judge_tokens: int, positions_tokens: int):
        self._backend = backend
        self._prompts = {"claims": read_prompt(claims_prompt), "judge": read_prompt(judge_prompt),
                         "positions": read_prompt(positions_prompt)}
        self._tokens = {"claims": claims_tokens, "judge": judge_tokens, "positions": positions_tokens}
        self.usages: list[TokenUsage | None] = []

    def _call(self, stage: str, payload: dict[str, Any], response_format: dict[str, Any],
              parse: Callable[[str], tuple[Any, list[str]]]) -> Any:
        """한 번 부르고, 구조가 틀렸거나 글이 검사에 걸렸으면 사유를 붙여 한 번만 다시 부른다.

        두 번째에도 글만 걸렸으면 첫 결과보다 못하지 않은 쪽(걸린 글이 적은 쪽)을 쓴다 — 사실 줄은 서버가
        쓰므로 해설이 비어도 결과가 된다.
        """
        best: tuple[Any, list[str]] | None = None
        for attempt in range(2):
            try:
                raw = self._backend.generate(
                    system_prompt=self._prompts[stage],
                    user_prompt=json.dumps(payload, ensure_ascii=False),
                    max_tokens=self._tokens[stage],
                    # 교정 때는 조금 올린다. 같은 온도에서는 같은 답이 다시 온다(질의응답과 같다).
                    temperature=0.2 if attempt == 0 else 0.4,
                    response_format=response_format,
                )
            except LLMBackendError as error:
                # 상한에서 끊긴 응답은 같은 문장을 되풀이한 것이다(2026-11-12 실측). 온도를 올려 한 번 다시 부른다.
                # 전송 실패의 재시도는 백엔드 래퍼가 이미 했으므로 여기서 또 하지 않는다.
                if attempt == 0 and error.reason == "truncated":
                    logger.warning("[VIEW_CHECK] %s 응답이 상한에서 끊겨 1회 재시도", stage)
                    continue
                if best is not None:
                    break
                raise ViewCheckError(str(error), stage=stage, rejected=False) from error
            finally:
                self.usages.append(getattr(self._backend, "last_usage", None))
            try:
                result = parse(raw)
            except Rejected as rejected:
                reason = str(rejected)
            else:
                if best is None or len(result[1]) < len(best[1]):
                    best = result
                if not result[1]:
                    break
                reason = "; ".join(dict.fromkeys(result[1]))
            if attempt == 0:
                logger.warning("[VIEW_CHECK] %s 응답 반려로 1회 교정: %s", stage, reason)
                # 직전 응답은 돌려주지 않는다 — 숫자가 들었으면 그 숫자를 다른 자리에 옮겨 붙인다.
                payload = {**payload, "revision": {
                    "reason": re.sub(r"\d+", "", reason),
                    "instruction": "직전 응답은 위 사유로 반려됐습니다. 원래 입력만 보고 응답 전체를 새로 쓰십시오."}}
        if best is None:
            raise ViewCheckError(f"{stage} correction exhausted", stage=stage, rejected=True)
        return best[0]

    def claims(self, view: str) -> list[Claim]:
        return self._call("claims", {"view": view}, CLAIMS_FORMAT, lambda raw: (parse_claims(raw), []))

    def judge(self, view: str, claims: list[dict[str, Any]], market: list[dict[str, Any]]) -> Judgement:
        """`claims`는 `[{c, claim, candidates: [후보]}]`, `market`은 뷰와 무관하게 참여가 몰린 후보다.
        후보의 꼴은 질의응답과 같다(`polymarket/ask.py`의 `build_row`)."""
        rows = [row for claim in claims for row in claim["candidates"]] + market
        options = {int(row["n"]): {int(option["m"]) for option in row["options"]} for row in rows}
        ids = [int(claim["c"]) for claim in claims]
        outside = {int(row["n"]) for row in market}
        payload = {"view": view, "claims": claims, "market": market}
        known = json.dumps(payload, ensure_ascii=False)

        def parse(raw: str) -> tuple[Judgement, list[str]]:
            judgement = parse_judgement(raw, ids, options, outside, known=known)
            return judgement, judgement.dropped

        return self._call("judge", payload, JUDGE_FORMAT, parse)

    def positions(self, payload: dict[str, Any], codes: set[str], claim_ids: list[int]) -> list[Position]:
        """`payload`는 `viewcheck/service.py`의 `positions_payload`. `codes` 밖의 상품은 버린다."""
        known = json.dumps(payload, ensure_ascii=False)
        return self._call("positions", payload, POSITIONS_FORMAT,
                          lambda raw: parse_positions(raw, codes, claim_ids, known=known))
