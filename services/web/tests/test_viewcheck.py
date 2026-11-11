"""뷰 검증(`/api/research/view`): 상품 표, 응답 검사, 후보 찾기, 포지션 조립, 검증 뒤 이동, API.

이 파일이 지키는 규칙은 일곱이다.

**숫자는 서버가 쓴다.** 모델 입력에 확률 숫자가 없고, 모델이 쓴 확률은 반려한다. 입력에 적힌 기준값은 받는다.

**모델은 입력 밖을 가리키지 못한다.** 없는 후보 번호·표에 없는 상품·근거 전제가 없는 포지션은 그 줄만 버린다.

**얇거나 곧 끝나는 질문으로 뷰를 판정하지 않는다.**

**컨센서스와 어긋난 전제에만 기댄 확대·축소는 지켜보기로 내린다.**

**운영자 개인용이다.** 목록에 없는 계정에는 화면에도 API에도 드러나지 않는다.

**동의한 계정만, 하루 상한 안에서. 실패한 실행도 센다.**

**포지션 단계가 실패해도 판정은 남는다.**
"""

from datetime import timedelta
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from services.web import server
from services.web.accounts import COOKIE, Accounts
from services.web.core import config
from services.web.core.clock import now
from services.web.llm.backends import LLMBackendError
from services.web.llm.viewcheck import (
    Claim,
    Judgement,
    Pick,
    Position,
    Rejected,
    Verdict,
    ViewCheckError,
    ViewChecker,
    parse_claims,
    parse_judgement,
    parse_positions,
    text_problem,
)
from services.web.polymarket.repository import PolymarketRepository
from services.web.portfolio.store import CANONICAL_CODE
from services.web.viewcheck import instruments, service
from services.web.viewcheck.routes import build_view_router

GENERATION = "20261111T160000000000+0900"
VIEW = "연준이 금리를 올려 미국 국채금리가 오르고, 비트코인은 연말에 15만 달러를 넘는다."


def _market(market_id, label, yes):
    return {"id": market_id, "outcome_label": label, "question": label, "yes_probability": yes,
            "no_probability": round(1 - yes, 6), "price_valid": True, "closed": False}


def _event(event_id, title, event_type, markets, *, tags, category="economy_finance", volume=50000.0,
           liquidity=1000.0, days=30):
    end = (now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    compact = {"id": event_id, "title": title, "category": category, "category_label": "경제·금융",
               "event_type": event_type, "tags": tags, "regions": [], "volume24hr": volume, "liquidity": liquidity,
               "end_date": end, "data_status": "ok", "price_status": "ok", "leader": None,
               "leader_probability": None}
    return compact, {**compact, "markets": markets}


def _events():
    return [
        _event("fed", "Fed Decision in December?", "exclusive_multi", [
            _market("f1", "25 bps increase", 0.855), _market("f2", "No change", 0.03),
            _market("f3", "50+ bps increase", 0.1065)], tags=["fed", "economy"], volume=2_000_000.0),
        _event("hike", "Another Fed rate hike in 2026?", "binary", [_market("h1", "Yes", 0.965)],
               tags=["fed", "economy"], volume=24000.0),
        _event("yield", "How high will 2-year Treasury yield go in November?", "independent_multi", [
            _market("y1", "4.76%", 0.815), _market("y2", "4.80%", 0.635), _market("y3", "4.97%", 0.15)],
            tags=["economy", "finance"], volume=9000.0),
        _event("btc", "What price will Bitcoin hit in 2026?", "independent_multi", [
            _market("b1", "↑ $150,000", 0.03), _market("b2", "↑ $100,000", 0.12), _market("b3", "↓ $60,000", 0.4)],
            tags=["crypto"], category="crypto", volume=300000.0),
        # 참여가 얇다(하루 규모·잔액 모두 하한 아래).
        _event("thin", "Fed emergency rate hike before 2027?", "binary", [_market("t1", "Yes", 0.9)],
               tags=["fed", "economy"], volume=10.0, liquidity=10.0),
        # 내일 끝난다 — 결과 확정을 향한 수렴이다.
        _event("soon", "Fed rate hike this week?", "binary", [_market("s1", "Yes", 0.99)],
               tags=["fed", "economy"], volume=90000.0, days=1),
        _event("war", "Israel x Iran ceasefire continues through...?", "independent_multi", [
            _market("w1", "December 15", 0.785), _market("w2", "December 31", 0.695)],
            tags=["geopolitics"], category="geopolitics", volume=150000.0),
        _event("match", "Premier League Winner", "exclusive_multi", [
            _market("m1", "Arsenal", 0.4), _market("m2", "Liverpool", 0.3)], tags=["sports"], category="sports",
            volume=900000.0),
    ]


INDEX = {
    "fed": {"h": "x", "summary": "연준이 12월 회의에서 금리를 어떻게 정할지", "keywords": ["연준", "금리", "인상"],
            "subtopic": "미국 통화정책"},
    "hike": {"h": "x", "summary": "연준이 올해 금리를 한 번 더 올릴지", "keywords": ["연준", "금리", "인상"],
             "subtopic": "미국 통화정책"},
    "yield": {"h": "x", "summary": "미국 2년물 국채 금리가 11월에 어디까지 오를지",
              "keywords": ["미국", "국채", "금리"], "subtopic": "미국 국채"},
    "btc": {"h": "x", "summary": "비트코인이 올해 얼마에 닿을지", "keywords": ["비트코인", "가격"],
            "subtopic": "가상자산"},
    "thin": {"h": "x", "summary": "연준이 긴급 인상을 할지", "keywords": ["연준", "금리", "인상"],
             "subtopic": "미국 통화정책"},
    "soon": {"h": "x", "summary": "연준이 이번 주 금리를 올릴지", "keywords": ["연준", "금리", "인상"],
             "subtopic": "미국 통화정책"},
}


@pytest.fixture
def repository(tmp_path):
    """`write_generation`과 같은 꼴(byte-addressed detail)을 회계 검사 없이 쓴다."""
    root = tmp_path / "pm"
    folder = root / "generations" / GENERATION
    folder.mkdir(parents=True)
    events, offset = [], 0
    with open(folder / "details-000.jsonl", "wb") as handle:
        for compact, detail in _events():
            data = (json.dumps({**detail, "generation_id": GENERATION}, ensure_ascii=False) + "\n").encode()
            handle.write(data)
            events.append({**compact, "generation_id": GENERATION, "detail_ref": {
                "shard": "details-000.jsonl", "offset": offset, "length": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}})
            offset += len(data)
    (root / "current.json").write_text(json.dumps(
        {"generation_id": GENERATION, "generated_at": "2026-11-11T16:00:00+09:00", "events": events},
        ensure_ascii=False), encoding="utf-8")
    (root / "search_index.json").write_text(json.dumps({"events": INDEX}, ensure_ascii=False), encoding="utf-8")
    return PolymarketRepository(root)


CLAIMS = [Claim("연준이 정책금리를 올린다", ["연준 금리 인상"]),
          Claim("미국 국채금리가 오른다", ["미국 국채 금리", "연준 금리 인상"]),
          Claim("비트코인이 연말에 15만 달러를 넘는다", ["비트코인 가격"])]


def _titles(rows):
    return [row.event["title"] for row in rows]


# ── 상품 표 ────────────────────────────────────────────────────────────────

def test_every_instrument_can_go_into_the_watchlist_and_says_which_way_it_moves():
    codes = [item.code for theme in instruments.THEMES for item in theme.instruments]
    assert len(codes) == len(set(codes)) == len(instruments.BY_CODE)
    for theme in instruments.THEMES:
        assert theme.label and theme.moves and theme.instruments
        for item in theme.instruments:
            assert CANONICAL_CODE.fullmatch(item.code), item.code
            assert item.side in (1, -1) and item.leverage > 0
            assert item.kind in ("ETF", "ETN") and item.market in ("KR", "US")
    assert instruments.describe(instruments.BY_CODE["KR:KOSPI:252670"]) == "역방향 2배"
    assert instruments.describe(instruments.BY_CODE["KR:KOSPI:069500"]) == "정방향"


def test_the_model_sees_what_each_theme_follows():
    rates = next(theme for theme in instruments.for_model() if theme["theme"] == "미국 금리(국채 가격)")
    assert "금리가 오르면 내린다" in rates["moves"]
    inverse = next(item for item in rates["instruments"] if item["code"] == "US:NYSEARCA:TBT")
    assert inverse["direction"] == "역방향 2배" and inverse["type"] == "ETF" and inverse["market"] == "미국"


# ── 응답 검사 ──────────────────────────────────────────────────────────────

def test_claims_need_text_and_search_words():
    raw = json.dumps({"claims": [
        {"claim": "연준이 정책금리를 올린다", "queries": ["연준 금리 인상", "연준 금리 인상", ""]},
        {"claim": "연준이 정책금리를 올린다", "queries": ["미국 금리"]},      # 같은 전제
        {"claim": "검색어가 없는 전제는 찾을 수 없다", "queries": []},
        {"claim": "폴리마켓에서 금리 인상에 돈이 몰린다", "queries": ["금리"]},  # 출처 이름
    ]}, ensure_ascii=False)
    assert parse_claims(raw) == [Claim("연준이 정책금리를 올린다", ["연준 금리 인상"])]
    with pytest.raises(Rejected):
        parse_claims('{"claims": []}')


def test_a_view_may_say_buying_but_not_name_the_source():
    """뷰를 다시 쓰는 글이라 "매수세"는 받는다(공개 줄글의 금지어보다 좁다)."""
    assert parse_claims(json.dumps({"claims": [{"claim": "외국인 매수세가 반도체로 이어진다",
                                                "queries": ["코스피"]}]}, ensure_ascii=False))


def test_model_probabilities_are_rejected_but_levels_from_the_input_are_kept():
    known = "10년물 금리가 5%를 넘는다 4.76%"
    assert text_problem("참여자들은 인상을 70%로 본다는 말은 쓸 수 없다.", (5, 200), known=known)
    assert text_problem("금리가 5%를 넘는다는 전제는 2년물 4.76% 칸의 기대와 같은 쪽이다.", (5, 200), known=known) is None
    assert text_problem("지금 사세요. 반드시 오릅니다.", (5, 200))
    assert text_problem("채권 비중을 줄여야 한다.", (5, 200))
    assert text_problem("폴리마켓 참여자들은 인상을 크게 기울여 본다.", (5, 200))
    assert text_problem("짧다", (5, 200)) and text_problem("외국인 매수세가 이어진다는 전제다.", (5, 200)) is None


def test_a_verdict_needs_a_question_it_stands_on():
    options = {1: {1, 2}, 2: {1, 2}, 9: {1, 2}}
    raw = json.dumps({"claims": [
        {"c": 1, "verdict": "supported", "picks": [{"n": 1, "focus": [1, 7]}, {"n": 5, "focus": []}],
         "note": "참여자들은 연준의 인상을 크게 기울여 본다."},
        {"c": 2, "verdict": "contradicted", "picks": [{"n": 44, "focus": []}], "note": "근거로 든 질문이 입력에 없다."},
        {"c": 3, "verdict": "unpriced", "picks": [{"n": 2, "focus": []}], "note": "재는 질문이 없었다는 설명이다."},
        {"c": 8, "verdict": "supported", "picks": [{"n": 1, "focus": []}], "note": "입력에 없는 전제 번호다."},
    ], "refined_view": "연준의 인상은 컨센서스와 같은 쪽이다. 나머지 전제는 컨센서스로 확인하지 못했으므로 보류한다.",
        "blind_spots": [{"n": 9, "note": "참여자들은 휴전이 이어진다고 본다. 뷰의 유가 전제와 이어진다."},
                        {"n": 1, "note": "이미 전제의 근거로 쓴 질문은 놓친 신호가 아니다."},
                        {"n": 2, "note": "뷰의 검색어로 찾은 질문은 뷰 밖의 신호가 아니다."}]}, ensure_ascii=False)
    judgement = parse_judgement(raw, [1, 2, 3, 4], options, {9}, known=VIEW)
    assert judgement.verdicts[0] == Verdict(1, "supported", [Pick(1, [1])], "참여자들은 연준의 인상을 크게 기울여 본다.")
    # 없는 후보만 든 판정은 판정이 아니고, "확인 못 함"은 근거를 보이지 않고, 응답에 없는 전제도 "확인 못 함"이다.
    assert [(v.verdict, v.picks) for v in judgement.verdicts[1:]] == [("unpriced", [])] * 3
    assert judgement.blind_spots == [(9, "참여자들은 휴전이 이어진다고 본다. 뷰의 유가 전제와 이어진다.")]
    assert judgement.refined_view and not judgement.dropped


def test_a_note_with_a_probability_is_dropped_and_reported():
    raw = json.dumps({"claims": [{"c": 1, "verdict": "supported", "picks": [{"n": 1, "focus": []}],
                                  "note": "참여자들의 85.5%가 인상을 본다."}],
                      "refined_view": "", "blind_spots": []}, ensure_ascii=False)
    judgement = parse_judgement(raw, [1], {1: {1}}, set(), known=VIEW)
    assert judgement.verdicts[0].note is None and judgement.verdicts[0].verdict == "supported"
    assert judgement.dropped and "note" in judgement.dropped[0]


def test_positions_stay_inside_the_table_and_the_claims():
    codes = {"KR:KOSPI:305080", "US:NYSEARCA:TBF", "US:NASDAQ:NVDA"}
    reason = "참여자들은 연준의 인상을 크게 기울여 본다. 금리가 오르면 국채 가격이 내려 이 상품에 부담이 된다."
    raw = json.dumps({"positions": [
        {"code": "kr:kospi:305080", "stance": "reduce", "claims": [1, 9], "reason": reason,
         "invalidation": "연준이 동결로 돌아서면 이 아이디어를 접는다."},
        {"code": "KR:KOSPI:999999", "stance": "long", "claims": [1], "reason": reason, "invalidation": ""},
        {"code": "US:NYSEARCA:TBF", "stance": "long", "claims": [], "reason": reason, "invalidation": ""},
        {"code": "US:NASDAQ:NVDA", "stance": "buy", "claims": [1], "reason": reason, "invalidation": ""},
        {"code": "US:NYSEARCA:TBF", "stance": "long", "claims": [1], "reason": "지금 사세요. " + reason,
         "invalidation": ""},
    ]}, ensure_ascii=False)
    positions, dropped = parse_positions(raw, codes, [1, 2], known=VIEW)
    assert positions == [Position("KR:KOSPI:305080", "reduce", [1], reason, "연준이 동결로 돌아서면 이 아이디어를 접는다.")]
    assert len(dropped) == 4


class _Backend:
    """정해 둔 응답을 차례로 준다. 예외를 넣으면 그 차례에 던진다."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.last_usage = None

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _checker(backend):
    return ViewChecker(
        backend, claims_prompt=config.VIEW_CHECK_CLAIMS_PROMPT_FILE, judge_prompt=config.VIEW_CHECK_JUDGE_PROMPT_FILE,
        positions_prompt=config.VIEW_CHECK_POSITIONS_PROMPT_FILE, claims_tokens=100, judge_tokens=200,
        positions_tokens=300)


def test_a_rejected_reply_is_corrected_once_and_then_gives_up():
    good = json.dumps({"claims": [{"claim": "연준이 정책금리를 올린다", "queries": ["연준 금리 인상"]}]},
                      ensure_ascii=False)
    backend = _Backend('{"claims": []}', good)
    assert _checker(backend).claims(VIEW) == [Claim("연준이 정책금리를 올린다", ["연준 금리 인상"])]
    first, second = (json.loads(call["user_prompt"]) for call in backend.calls)
    assert "revision" not in first and second["revision"]["reason"]
    assert backend.calls[1]["temperature"] > backend.calls[0]["temperature"]
    assert backend.calls[0]["max_tokens"] == 100 and backend.calls[0]["response_format"]["type"] == "json_schema"

    with pytest.raises(ViewCheckError) as failed:
        _checker(_Backend('{"claims": []}', "not json")).claims(VIEW)
    assert failed.value.stage == "claims" and failed.value.rejected

    with pytest.raises(ViewCheckError) as down:
        _checker(_Backend(LLMBackendError("cloudflare", "timeout"))).claims(VIEW)
    assert not down.value.rejected


def test_a_reply_cut_at_the_limit_is_asked_again_once():
    """상한까지 같은 문장을 되풀이한 응답(실측)은 온도를 올려 한 번 다시 부른다. 두 번 다 끊기면 실패다."""
    good = json.dumps({"claims": [{"claim": "연준이 정책금리를 올린다", "queries": ["연준 금리 인상"]}]},
                      ensure_ascii=False)
    backend = _Backend(LLMBackendError("cloudflare", "truncated"), good)
    assert _checker(backend).claims(VIEW) and len(backend.calls) == 2
    assert "revision" not in json.loads(backend.calls[1]["user_prompt"])
    assert backend.calls[1]["temperature"] > backend.calls[0]["temperature"]
    with pytest.raises(ViewCheckError):
        _checker(_Backend(LLMBackendError("cloudflare", "truncated"),
                          LLMBackendError("cloudflare", "truncated"))).claims(VIEW)


def test_when_only_the_wording_fails_twice_the_picks_survive():
    """사실 줄은 서버가 쓴다. 해설이 두 번 다 걸려도 고른 질문과 판정은 남는다."""
    reply = json.dumps({"claims": [{"c": 1, "verdict": "supported", "picks": [{"n": 1, "focus": [1]}],
                                    "note": "참여자들의 85.5%가 인상을 본다."}],
                        "refined_view": "", "blind_spots": []}, ensure_ascii=False)
    backend = _Backend(reply, reply)
    claims = [{"c": 1, "claim": "연준이 정책금리를 올린다",
               "candidates": [{"n": 1, "options": [{"m": 1, "label": "예", "outlook": "매우 높음"}]}]}]
    judgement = _checker(backend).judge(VIEW, claims, [])
    assert len(backend.calls) == 2
    assert judgement.verdicts == [Verdict(1, "supported", [Pick(1, [1])], None)]


def test_the_prompts_keep_the_naming_rule():
    for path in (config.VIEW_CHECK_JUDGE_PROMPT_FILE, config.VIEW_CHECK_POSITIONS_PROMPT_FILE):
        text = path.read_text(encoding="utf-8")
        assert "[호칭 규칙]" in text and "숫자로 된 확률" in text


# ── 후보 찾기 ──────────────────────────────────────────────────────────────

def test_thin_or_expiring_questions_do_not_judge_a_view(repository):
    board = service.collect(repository, CLAIMS)
    found = [title for rows in board.by_claim for title in _titles(rows)]
    assert "Fed Decision in December?" in _titles(board.by_claim[0])
    assert "Fed emergency rate hike before 2027?" not in found and "Fed rate hike this week?" not in found
    # 같은 질문은 한 전제에만 선다 — 둘째 전제도 "연준 금리 인상"을 검색했지만 첫 전제가 이미 가져갔다.
    assert len(found) == len(set(found))
    assert "How high will 2-year Treasury yield go in November?" in _titles(board.by_claim[1])
    assert _titles(board.by_claim[2]) == ["What price will Bitcoin hit in 2026?"]
    # 뷰와 무관한 신호는 경제·지정학 질문뿐이고, 번호는 통틀어 하나씩이다.
    assert _titles(board.market) == ["Israel x Iran ceasefire continues through...?"]
    assert sorted(board.rows) == list(range(1, len(board.rows) + 1))


def test_the_model_never_sees_a_probability(repository):
    board = service.collect(repository, CLAIMS)
    claims, market = service.judge_payload(CLAIMS, board)
    text = json.dumps({"claims": claims, "market": market}, ensure_ascii=False)
    for number in ("0.855", "85.5", "0.965", "0.785", "0.03"):
        assert number not in text
    assert "매우 높음" in text and [claim["c"] for claim in claims] == [1, 2, 3]


# ── 조립 ───────────────────────────────────────────────────────────────────

def _judged(repository):
    board = service.collect(repository, CLAIMS)
    number = {row.event["id"]: n for n, row in board.rows.items()}
    judgement = Judgement(
        [Verdict(1, "supported", [Pick(number["fed"], [1])], "참여자들은 인상을 크게 기울여 본다."),
         Verdict(2, "mixed", [Pick(number["yield"], [])], None),
         # 선택지 번호는 확률이 높은 순이다(`build_row`). 15만 달러 칸은 셋째다.
         Verdict(3, "contradicted", [Pick(number["btc"], [3])], "참여자들은 그 수준을 매우 낮게 본다.")],
        "다듬은 뷰", [(number["war"], "참여자들은 휴전이 이어진다고 본다.")])
    return board, service.claims_report(CLAIMS, judgement, board), service.blind_spots_report(judgement, board)


def test_the_screen_numbers_are_the_detail_values(repository):
    _, claims, blind = _judged(repository)
    assert claims[0]["verdict"] == "supported" and claims[0]["queries"] == ["연준 금리 인상"]
    assert claims[0]["signals"][0]["options"][0] == {"label": "25 bps increase", "probability": 0.855, "focus": True}
    assert {"label": "↑ $150,000", "probability": 0.03, "focus": True} in claims[2]["signals"][0]["options"]
    assert blind[0]["signal"]["title"] == "Israel x Iran ceasefire continues through...?"


def test_the_positions_model_sees_grades_and_only_stocks_outside_the_table(repository):
    _, claims, blind = _judged(repository)
    payload = service.positions_payload("다듬은 뷰", claims, blind,
                                        {"US:NASDAQ:NVDA": "NVIDIA", "KR:KOSPI:069500": "KODEX 200"})
    assert payload["claims"][0] == {"c": 1, "claim": "연준이 정책금리를 올린다", "verdict": "컨센서스가 받침", "consensus": [
        {"question": "연준이 12월 회의에서 금리를 어떻게 정할지", "option": "25 bps increase", "outlook": "매우 높음"}]}
    assert payload["stocks"] == [{"code": "US:NASDAQ:NVDA", "name": "NVIDIA"}]
    assert "0.855" not in json.dumps(payload, ensure_ascii=False)
    assert service.position_codes({"US:NASDAQ:NVDA": "NVIDIA"}) >= {"US:NASDAQ:NVDA", "KR:KOSPI:305080"}


def test_ideas_resting_only_on_contradicted_claims_become_wait(repository):
    _, claims, _ = _judged(repository)
    reason = "이유는 모델이 쓴 그대로 둔다."
    report = service.positions_report([
        Position("US:NASDAQ:IBIT", "long", [3], reason, None),           # 어긋난 전제뿐 → 지켜보기
        Position("KR:KOSPI:305080", "reduce", [1], reason, "접는 조건"),  # 받침 전제 → 그대로
        Position("US:NYSEARCA:TBT", "long", [1], reason, None),          # 같은 주제(미국 금리) 둘째 → 버림
        Position("KR:KOSPI:530031", "long", [2], reason, None),          # 엇갈림뿐 + 2배 ETN
        Position("US:NASDAQ:NVDA", "hedge", [2, 3], reason, None),       # 관심종목
    ], claims, {"US:NASDAQ:NVDA": "NVIDIA"})
    by_code = {row["code"]: row for row in report}
    assert list(by_code) == ["US:NASDAQ:IBIT", "KR:KOSPI:305080", "KR:KOSPI:530031", "US:NASDAQ:NVDA"]
    assert by_code["US:NASDAQ:IBIT"]["stance"] == "wait"
    assert by_code["US:NASDAQ:IBIT"]["cautions"] == [service.CAUTION_DOWNGRADED]
    bond = by_code["KR:KOSPI:305080"]
    assert bond["stance"] == "reduce" and bond["cautions"] == [] and bond["name"] == "TIGER 미국채10년선물"
    assert bond["theme"] == "미국 금리(국채 가격)" and bond["direction"] == "정방향" and bond["kind"] == "ETF"
    # 같은 주제·같은 방향·같은 배수의 다른 상품을 나란히 보인다. 역방향과 3배는 같은 성격이 아니다.
    names = {item["code"] for item in bond["alternatives"]}
    assert "US:NASDAQ:TLT" in names and "US:NYSEARCA:TBT" not in names and "US:NYSEARCA:TMF" not in names
    assert by_code["KR:KOSPI:530031"]["cautions"] == [
        service.CAUTION_UNSUPPORTED, service.CAUTION_GEARED, service.CAUTION_ETN]
    stock = by_code["US:NASDAQ:NVDA"]
    assert (stock["name"], stock["kind"], stock["theme"], stock["alternatives"]) == ("NVIDIA", "주식", None, [])


def test_drift_reads_the_same_options_from_the_current_data(repository):
    _, claims, blind = _judged(repository)
    report = {"claims": claims, "blind_spots": blind}
    report["claims"][0]["signals"].append({"id": "gone", "options": []})
    drift = service.drift(repository, report)
    assert drift["generation_id"] == GENERATION and drift["alert"] == config.VIEW_CHECK_DRIFT_ALERT
    assert drift["now"]["fed"]["25 bps increase"] == 0.855 and drift["now"]["btc"]["↑ $150,000"] == 0.03
    assert drift["now"]["gone"] is None


# ── API ────────────────────────────────────────────────────────────────────

class _Checker:
    """모델 자리. 후보판에서 번호를 찾아 정해 둔 판정을 낸다."""

    def __init__(self):
        self.views, self.payloads = [], []
        self.fail = None

    def claims(self, view):
        self.views.append(view)
        if self.fail == "claims":
            raise ViewCheckError("claims correction exhausted", stage="claims", rejected=True)
        return CLAIMS

    def judge(self, view, claims, market):
        if self.fail == "judge":
            raise ViewCheckError("cloudflare timeout", stage="judge", rejected=False)
        number = {row["title"]: row["n"] for claim in claims for row in claim["candidates"]}
        if self.fail == "unpriced":
            return Judgement([Verdict(c, "unpriced", [], None) for c in (1, 2, 3)], None, [])
        return Judgement(
            [Verdict(1, "supported", [Pick(number["Fed Decision in December?"], [1])], "참여자들은 인상을 크게 본다."),
             Verdict(2, "unpriced", [], None),
             Verdict(3, "contradicted", [Pick(number["What price will Bitcoin hit in 2026?"], [1])], None)],
            "연준의 인상은 컨센서스와 같은 쪽이다. 비트코인 전제는 컨센서스와 어긋나므로 보류한다.",
            [(market[0]["n"], "참여자들은 휴전이 이어진다고 본다.")])

    def positions(self, payload, codes, claim_ids):
        self.payloads.append((payload, codes, claim_ids))
        if self.fail == "positions":
            raise ViewCheckError("cloudflare truncated", stage="positions", rejected=False)
        return [Position("KR:KOSPI:305080", "reduce", [1], "금리가 오르면 국채 가격이 내려 부담이 된다.", "접는 조건"),
                Position("US:NASDAQ:IBIT", "long", [3], "비트코인 전제에 기댄 아이디어다.", None)]


@pytest.fixture
def setup(tmp_path, monkeypatch, repository):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    accounts = Accounts(tmp_path / "users", client_id="client", client_secret="secret",
                        identity_key="k" * 32, origin="https://testserver")
    # 두 계정을 운영자 목록에 올린다. 목록 밖 계정은 `test_other_accounts_never_see_the_feature`가 본다.
    monkeypatch.setattr(config, "VIEW_CHECK_ACCOUNT_KEYS", frozenset(
        hmac.new(b"k" * 32, subject.encode(), hashlib.sha256).hexdigest() for subject in ("subject-a", "subject-b")))
    checker = _Checker()
    pending = []
    # 실제로는 요청이 끝난 뒤 스레드에서 돈다. 테스트는 응답을 받은 뒤 그 일을 이어서 돌린다.
    router = build_view_router(accounts, repository, checker_factory=lambda: checker, start=pending.append)
    app = server.build_app(accounts=accounts, view_router=router)

    def client(subject):
        value = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
        value.cookies.set(COOKIE, accounts.issue(subject))
        return value

    def run():
        while pending:
            pending.pop(0)()

    return accounts, client("subject-a"), client("subject-b"), checker, run, client


def _ready(client, topic=VIEW):
    client.put("/api/research/profile", json={"topic": topic})
    client.put("/api/research/consent", json={"agree": True})


def test_other_accounts_never_see_the_feature(setup):
    """운영자 개인용이다. 목록에 없는 계정에는 없는 주소처럼 답하고, 동의했어도 모델을 부르지 않는다."""
    _, a, _, checker, run, client = setup
    other = client("subject-c")
    _ready(other)
    assert other.get("/api/research").json()["view_check"] is False
    assert other.get("/api/research/view").status_code == 404
    assert other.post("/api/research/view").status_code == 404
    assert other.post("/api/research/view/watch", json={"code": "KR:KOSPI:305080"}).status_code == 404
    run()
    assert checker.views == []
    assert a.get("/api/research").json()["view_check"] is True


def test_nobody_has_it_until_the_operator_lists_an_account(setup, monkeypatch):
    _, a, _, _, _, _ = setup
    monkeypatch.setattr(config, "VIEW_CHECK_ACCOUNT_KEYS", frozenset())
    assert a.get("/api/research/view").status_code == 404 and a.get("/api/research").json()["view_check"] is False


def test_a_view_is_checked_only_after_consent_and_stays_in_the_account(setup):
    _, a, b, checker, run, _ = setup
    assert TestClient(a.app, base_url="https://testserver").get("/api/research/view").status_code == 401
    assert a.get("/api/research/view").json() == {
        "report": None, "run": None, "drift": None, "ready": True,
        "usage": {"used": 0, "limit": config.VIEW_CHECK_MAX_DAILY}}
    a.put("/api/research/profile", json={"topic": VIEW})
    assert a.post("/api/research/view").status_code == 403
    a.put("/api/research/consent", json={"agree": True})
    a.put("/api/portfolio/watchlist", json={"items": [{"code": "NVDA", "name": "NVIDIA", "market": "US",
                                                       "exchange": "NASDAQ"}]})
    started = a.post("/api/research/view")
    assert started.status_code == 202 and started.json()["status"] == "running"
    assert a.post("/api/research/view").status_code == 409
    assert a.get("/api/research/view").json()["report"] is None
    run()

    state = a.get("/api/research/view").json()
    report = state["report"]
    assert state["run"]["status"] == "ok" and state["usage"]["used"] == 1
    assert checker.views == [VIEW] and report["view"] == VIEW and report["generation_id"] == GENERATION
    assert [claim["verdict"] for claim in report["claims"]] == ["supported", "unpriced", "contradicted"]
    assert report["claims"][0]["signals"][0]["options"][0]["probability"] == 0.855
    assert report["refined_view"].startswith("연준의 인상") and len(report["blind_spots"]) == 1
    # 받침 전제의 축소는 그대로, 어긋난 전제뿐인 확대는 지켜보기로 내려간다.
    assert [(row["code"], row["stance"]) for row in report["positions"]] == [
        ("KR:KOSPI:305080", "reduce"), ("US:NASDAQ:IBIT", "wait")]
    assert report["positions_status"] == "ok" and report["disclaimer"] == service.DISCLAIMER
    assert state["drift"]["now"]["fed"]["25 bps increase"] == 0.855
    # 포지션 단계는 다듬은 뷰와 관심종목을 받는다. 자산은 어디에도 없다.
    payload, codes, claim_ids = checker.payloads[0]
    assert payload["view"] == report["refined_view"] and payload["stocks"] == [{"code": "US:NASDAQ:NVDA", "name": "NVIDIA"}]
    assert "US:NASDAQ:NVDA" in codes and claim_ids == [1, 2, 3]
    assert b.get("/api/research/view").json()["report"] is None

    a.put("/api/research/consent", json={"agree": False})
    assert a.post("/api/research/view").status_code == 403


def test_an_empty_view_is_refused_and_origin_is_required(setup):
    _, a, _, _, _, _ = setup
    a.put("/api/research/consent", json={"agree": True})
    assert a.post("/api/research/view").status_code == 422
    _ready(a)
    assert a.post("/api/research/view", headers={"origin": "https://evil.example"}).status_code == 403


def test_failures_are_told_apart_and_counted(setup, monkeypatch):
    _, a, _, checker, run, _ = setup
    _ready(a)
    checker.fail = "claims"
    a.post("/api/research/view")
    run()
    state = a.get("/api/research/view").json()
    assert state["run"]["status"] == "failed" and "예상을 찾지 못했습니다" in state["run"]["error"]
    assert state["report"] is None and state["usage"]["used"] == 1

    checker.fail = "judge"
    a.post("/api/research/view")
    run()
    assert "다시 실행하세요" in a.get("/api/research/view").json()["run"]["error"]

    monkeypatch.setattr(config, "VIEW_CHECK_MAX_DAILY", 2)
    assert a.post("/api/research/view").status_code == 429


def test_the_server_wide_limit_holds_across_accounts(setup, monkeypatch):
    _, a, b, _, run, _ = setup
    monkeypatch.setattr(config, "VIEW_CHECK_SERVER_MAX_DAILY", 1)
    _ready(a)
    _ready(b)
    assert a.post("/api/research/view").status_code == 202
    run()
    assert b.post("/api/research/view").status_code == 429
    # 서버 상한에 걸린 요청은 계정의 횟수를 쓰지 않고, 다음 요청을 막는 자리도 남기지 않는다.
    assert b.get("/api/research/view").json()["usage"]["used"] == 0
    assert b.get("/api/research/view").json()["run"] is None


def test_verdicts_survive_a_failed_positions_stage(setup):
    _, a, _, checker, run, _ = setup
    _ready(a)
    checker.fail = "positions"
    a.post("/api/research/view")
    run()
    report = a.get("/api/research/view").json()["report"]
    assert report["positions"] == [] and report["positions_status"] == "failed"
    assert report["claims"][0]["verdict"] == "supported"


def test_no_positions_without_a_claim_the_consensus_speaks_to(setup):
    _, a, _, checker, run, _ = setup
    _ready(a)
    checker.fail = "unpriced"
    a.post("/api/research/view")
    run()
    report = a.get("/api/research/view").json()["report"]
    assert report["positions_status"] == "skipped" and checker.payloads == []


def test_only_positions_from_the_report_go_into_the_watchlist(setup):
    _, a, b, _, run, _ = setup
    _ready(a)
    a.post("/api/research/view")
    run()
    assert a.post("/api/research/view/watch", json={"code": "US:NYSEARCA:SPY"}).status_code == 422
    assert b.post("/api/research/view/watch", json={"code": "KR:KOSPI:305080"}).status_code == 422
    done = a.post("/api/research/view/watch", json={"code": "KR:KOSPI:305080"})
    assert done.status_code == 200 and done.json()["watched"] == ["KR:KOSPI:305080"]
    assert a.get("/api/portfolio/watchlist").json()["items"] == {"KR:KOSPI:305080": "TIGER 미국채10년선물"}
    assert a.get("/api/research/view").json()["report"]["watched"] == ["KR:KOSPI:305080"]


def test_us_etf_exchanges_are_valid_watchlist_codes(setup):
    """미국 ETF는 NYSE Arca·Cboe에 상장한다. 화면에서 직접 넣는 경로도 같은 정규 코드를 만든다."""
    _, a, _, _, _, _ = setup
    saved = a.put("/api/portfolio/watchlist", json={"items": [
        {"code": "spy", "name": "SPY", "market": "US", "exchange": "NYSEARCA"},
        {"code": "ITA", "name": "ITA", "market": "US", "exchange": "CBOE"}]})
    assert saved.json()["items"] == {"US:NYSEARCA:SPY": "SPY", "US:CBOE:ITA": "ITA"}


def test_the_page_keeps_the_view_check_hidden_until_the_account_has_it():
    from services.web.pages.research import RESEARCH_HTML
    assert "id='v-create' type='button' hidden>" in RESEARCH_HTML and "id='v-box' hidden>" in RESEARCH_HTML
    assert "d.view_check" in RESEARCH_HTML and "/view/watch" in RESEARCH_HTML
    # 공개 안내·동의 문구는 이 기능을 말하지 않는다.
    consent = RESEARCH_HTML.split("id='r-consent'")[1].split("</section>")[0]
    assert "뷰 검증" not in consent and "컨센서스" not in consent
    assert "polymarket" not in RESEARCH_HTML.lower() and "폴리마켓" not in RESEARCH_HTML
    assert " style='" not in RESEARCH_HTML  # CSP가 인라인 style 속성을 막는다
