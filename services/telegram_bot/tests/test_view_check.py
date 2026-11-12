"""뷰 검증(텔레그램 `/view`): 상품 표, 응답 검사, 후보 찾기, 포지션 조립, 검증 뒤 이동, 명령.

이 파일이 지키는 규칙은 여섯이다.

**숫자는 봇이 쓴다.** 모델 입력에 확률 숫자가 없고, 모델이 쓴 확률은 반려한다. 입력에 적힌 기준값은 받는다.

**모델은 입력 밖을 가리키지 못한다.** 없는 후보 번호·표에 없는 상품·근거 전제가 없는 포지션은 그 줄만 버린다.

**얇거나 곧 끝나는 질문으로 뷰를 판정하지 않는다.**

**컨센서스와 어긋난 전제에만 기댄 확대·축소는 지켜보기로 내린다.**

**하루 상한 안에서, 한 번에 하나씩. 실패한 실행도 센다.**

**포지션 단계가 실패해도 판정은 남는다.**

컨센서스는 웹 공개 API(`/api/forecast/*`)의 응답 꼴을 흉내 낸 `_Web`으로 준다 — 봇은 웹 코드를 import하지 않는다.
"""

import asyncio
import json
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from services.telegram_bot.core import config
from services.telegram_bot.core.clock import now
from services.telegram_bot.features.view_check import handlers, instruments, service
from services.telegram_bot.features.view_check.consensus import Consensus, ConsensusUnavailable
from services.telegram_bot.llm.backends import LLMBackendError, TokenUsage
from services.telegram_bot.llm.view_check import (
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

GENERATION = "20261111T160000000000+0900"
VIEW = "연준이 금리를 올려 미국 국채금리가 오르고, 비트코인은 연말에 15만 달러를 넘는다."
# 관심종목과 같은 정규 코드 꼴(웹 `portfolio/store.py`와 같은 형식).
CANONICAL_CODE = re.compile(r"KR:KOSPI:\d{6}|US:(?:NASDAQ|NYSE|NYSEARCA|CBOE):[A-Z][A-Z0-9.]*")


def _market(market_id, label, yes):
    return {"id": market_id, "outcome_label": label, "question": label, "yes_probability": yes,
            "no_probability": round(1 - yes, 6), "price_valid": True, "closed": False}


def _event(event_id, title, event_type, markets, *, summary=None, category="economy_finance", volume=50000.0,
           liquidity=1000.0, days=30):
    end = (now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": event_id, "title": title, "category": category, "event_type": event_type, "volume24hr": volume,
            "liquidity": liquidity, "end_date": end, "price_status": "ok", "summary": summary, "markets": markets}


def _events():
    return [
        _event("fed", "Fed Decision in December?", "exclusive_multi", [
            _market("f1", "25 bps increase", 0.855), _market("f2", "No change", 0.03),
            _market("f3", "50+ bps increase", 0.1065)], summary="연준이 12월 회의에서 금리를 어떻게 정할지",
            volume=2_000_000.0),
        _event("hike", "Another Fed rate hike in 2026?", "binary", [_market("h1", "Yes", 0.965)], volume=24000.0),
        _event("yield", "How high will 2-year Treasury yield go in November?", "independent_multi", [
            _market("y1", "4.76%", 0.815), _market("y2", "4.80%", 0.635), _market("y3", "4.97%", 0.15)],
            volume=9000.0),
        _event("btc", "What price will Bitcoin hit in 2026?", "independent_multi", [
            _market("b1", "↑ $150,000", 0.03), _market("b2", "↑ $100,000", 0.12), _market("b3", "↓ $60,000", 0.4)],
            category="crypto", volume=300000.0),
        # 참여가 얇다(하루 규모·잔액 모두 하한 아래).
        _event("thin", "Fed emergency rate hike before 2027?", "binary", [_market("t1", "Yes", 0.9)],
               volume=10.0, liquidity=10.0),
        # 내일 끝난다 — 결과 확정을 향한 수렴이다.
        _event("soon", "Fed rate hike this week?", "binary", [_market("s1", "Yes", 0.99)], volume=90000.0, days=1),
        _event("war", "Israel x Iran ceasefire continues through...?", "independent_multi", [
            _market("w1", "December 15", 0.785), _market("w2", "December 31", 0.695)],
            category="geopolitics", volume=150000.0),
        _event("match", "Premier League Winner", "exclusive_multi", [
            _market("m1", "Arsenal", 0.4), _market("m2", "Liverpool", 0.3)], category="sports", volume=900000.0),
    ]


# 웹의 자연어 검색이 검색어마다 돌려줄 질문(관련도순).
SEARCH = {"연준 금리 인상": ["fed", "hike", "thin", "soon"], "미국 국채 금리": ["yield", "fed"], "비트코인 가격": ["btc"]}


class _Web:
    """공개 웹 `/api/forecast/events`·`/api/forecast/events/{id}`의 응답 꼴."""

    def __init__(self):
        self.events = {event["id"]: event for event in _events()}
        self.down = False

    def __call__(self, url, params=None, timeout=None):
        if self.down:
            return SimpleNamespace(status_code=503, json=dict)
        path = url.removeprefix(config.VIEW_CHECK_API_BASE_URL)
        if path == "/api/forecast/events":
            if "q" in params:
                found = [self.events[key] for key in SEARCH.get(params["q"], []) if key in self.events]
            else:
                found = sorted((event for event in self.events.values() if event["category"] == params["category"]),
                               key=lambda event: event["volume24hr"], reverse=True)
            body = {"generation_id": GENERATION,
                    "events": [{key: value for key, value in event.items() if key != "markets"} for event in found]}
            return SimpleNamespace(status_code=200, json=lambda: body)
        event = self.events.get(path.removeprefix("/api/forecast/events/"))
        return SimpleNamespace(status_code=200 if event else 404, json=lambda: event)


@pytest.fixture
def web():
    return _Web()


CLAIMS = [Claim("연준이 정책금리를 올린다", ["연준 금리 인상"]),
          Claim("미국 국채금리가 오른다", ["미국 국채 금리", "연준 금리 인상"]),
          Claim("비트코인이 연말에 15만 달러를 넘는다", ["비트코인 가격"])]


def _titles(rows):
    return [row.event["title"] for row in rows]


# ── 상품 표 ────────────────────────────────────────────────────────────────

def test_every_instrument_has_a_canonical_code_and_says_which_way_it_moves():
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
    # 모델은 주제가 무엇을 따라 움직이는지와 상품의 방향을 본다.
    rates = next(theme for theme in instruments.for_model() if theme["theme"] == "미국 금리(국채 가격)")
    assert "금리가 오르면 내린다" in rates["moves"]
    assert "US:NYSEARCA:TBT | ProShares UltraShort 20+ Year Treasury | 미국 ETF | 역방향 2배 | 장기" in rates["instruments"]


# ── 응답 검사 ──────────────────────────────────────────────────────────────

def test_claims_need_text_and_search_words():
    raw = json.dumps({"claims": [
        {"claim": "연준이 정책금리를 올린다", "queries": ["연준 금리 인상", "연준 금리 인상", ""]},
        {"claim": "연준이 정책금리를 올린다", "queries": ["미국 금리"]},      # 같은 전제
        {"claim": "검색어가 없는 전제는 찾을 수 없다", "queries": []},
        {"claim": "검색어 자리에 숫자를 넣은 응답도 있었다", "queries": 3},
        {"claim": "폴리마켓에서 금리 인상에 돈이 몰린다", "queries": ["금리"]},  # 출처 이름
    ]}, ensure_ascii=False)
    assert parse_claims(raw) == [Claim("연준이 정책금리를 올린다", ["연준 금리 인상"])]
    with pytest.raises(Rejected):
        parse_claims('{"claims": []}')
    # 뷰를 다시 쓰는 글이라 "매수세"는 받는다(공개 줄글의 금지어보다 좁다).
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

    # 확률을 숫자로 쓴 해설은 그 글만 비우고 사유를 남긴다. 판정과 근거는 그대로다.
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
        backend, reasoner=backend, claims_prompt=config.VIEW_CHECK_CLAIMS_PROMPT_FILE, judge_prompt=config.VIEW_CHECK_JUDGE_PROMPT_FILE,
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

    # 상한까지 같은 문장을 되풀이한 응답(실측)은 온도를 올려 한 번 다시 부른다. 두 번 다 끊기면 실패다.
    backend = _Backend(LLMBackendError("cloudflare", "truncated"), good)
    assert _checker(backend).claims(VIEW) and len(backend.calls) == 2
    assert "revision" not in json.loads(backend.calls[1]["user_prompt"])
    assert backend.calls[1]["temperature"] > backend.calls[0]["temperature"]
    with pytest.raises(ViewCheckError):
        _checker(_Backend(LLMBackendError("cloudflare", "truncated"),
                          LLMBackendError("cloudflare", "truncated"))).claims(VIEW)


def test_a_rejected_wording_costs_no_second_call():
    """사실 줄은 봇이 쓴다. 해설이 검사에 걸리면 그 글만 비우고, 그 단계를 다시 부르지 않는다(비용)."""
    reply = json.dumps({"claims": [{"c": 1, "verdict": "supported", "picks": [{"n": 1, "focus": [1]}],
                                    "note": "참여자들의 85.5%가 인상을 본다."}],
                        "refined_view": "", "blind_spots": []}, ensure_ascii=False)
    backend = _Backend(reply)
    claims = [{"c": 1, "claim": "연준이 정책금리를 올린다",
               "candidates": [{"n": 1, "options": [{"m": 1, "label": "예", "outlook": "매우 높음"}]}]}]
    judgement = _checker(backend).judge(VIEW, claims, [])
    assert len(backend.calls) == 1
    assert judgement.verdicts == [Verdict(1, "supported", [Pick(1, [1])], None)]


# ── 후보 찾기 ──────────────────────────────────────────────────────────────

def test_thin_or_expiring_questions_do_not_judge_a_view(web):
    consensus = Consensus(web)
    board = service.collect(consensus, CLAIMS)
    found = [title for rows in board.by_claim for title in _titles(rows)]
    assert "Fed Decision in December?" in _titles(board.by_claim[0])
    assert "Fed emergency rate hike before 2027?" not in found and "Fed rate hike this week?" not in found
    # 같은 질문은 한 전제에만 선다 — 둘째 전제도 "연준 금리 인상"을 검색했지만 첫 전제가 이미 가져갔다.
    assert len(found) == len(set(found))
    assert "How high will 2-year Treasury yield go in November?" in _titles(board.by_claim[1])
    assert _titles(board.by_claim[2]) == ["What price will Bitcoin hit in 2026?"]
    # 뷰와 무관한 신호는 경제·지정학 질문뿐이고(경기는 참여가 커도 뺀다), 번호는 통틀어 하나씩이다.
    assert _titles(board.market) == ["Israel x Iran ceasefire continues through...?"]
    assert sorted(board.rows) == list(range(1, len(board.rows) + 1))
    assert consensus.generation_id == GENERATION
    # 모델 입력에는 확률 숫자가 없고 등급만 있다.
    claims, market = service.judge_payload(CLAIMS, board)
    text = json.dumps({"claims": claims, "market": market}, ensure_ascii=False)
    for number in ("0.855", "85.5", "0.965", "0.785", "0.03"):
        assert number not in text
    assert "매우 높음" in text and [claim["c"] for claim in claims] == [1, 2, 3]


# ── 조립 ───────────────────────────────────────────────────────────────────

def _judged(web):
    board = service.collect(Consensus(web), CLAIMS)
    number = {row.event["id"]: n for n, row in board.rows.items()}
    judgement = Judgement(
        [Verdict(1, "supported", [Pick(number["fed"], [1])], "참여자들은 인상을 크게 기울여 본다."),
         Verdict(2, "mixed", [Pick(number["yield"], [])], None),
         # 선택지 번호는 확률이 높은 순이다(`build_row`). 15만 달러 칸은 셋째다.
         Verdict(3, "contradicted", [Pick(number["btc"], [3])], "참여자들은 그 수준을 매우 낮게 본다.")],
        "다듬은 뷰", [(number["war"], "참여자들은 휴전이 이어진다고 본다.")])
    return service.claims_report(CLAIMS, judgement, board), service.blind_spots_report(judgement, board)


def test_the_message_numbers_are_the_values_the_web_gave(web):
    claims, blind = _judged(web)
    assert claims[0]["verdict"] == "supported" and claims[0]["queries"] == ["연준 금리 인상"]
    assert claims[0]["signals"][0]["options"][0] == {"label": "25 bps increase", "probability": 0.855, "focus": True}
    assert {"label": "↑ $150,000", "probability": 0.03, "focus": True} in claims[2]["signals"][0]["options"]
    assert blind[0]["signal"]["title"] == "Israel x Iran ceasefire continues through...?"
    # 포지션 단계의 모델은 등급만 보고, 관심종목 가운데 표에 없는 것만 종목으로 받는다.
    payload = service.positions_payload("다듬은 뷰", claims, blind,
                                        {"US:NASDAQ:NVDA": "NVIDIA", "KR:KOSPI:069500": "KODEX 200"})
    assert payload["claims"][0] == {"c": 1, "claim": "연준이 정책금리를 올린다", "verdict": "컨센서스가 받침", "consensus": [
        {"question": "연준이 12월 회의에서 금리를 어떻게 정할지", "option": "25 bps increase", "outlook": "매우 높음"}]}
    assert payload["stocks"] == [{"code": "US:NASDAQ:NVDA", "name": "NVIDIA"}]
    assert "0.855" not in json.dumps(payload, ensure_ascii=False)
    assert service.position_codes({"US:NASDAQ:NVDA": "NVIDIA"}) >= {"US:NASDAQ:NVDA", "KR:KOSPI:305080"}


def test_ideas_resting_only_on_contradicted_claims_become_wait(web):
    claims, _ = _judged(web)
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


# ── 명령 ───────────────────────────────────────────────────────────────────

class _Checker:
    """모델 자리. 후보판에서 번호를 찾아 정해 둔 판정을 낸다."""

    def __init__(self):
        self.views, self.payloads, self.usages = [], [], []
        self.fail = None

    def claims(self, view):
        self.views.append(view)
        self.usages.append(TokenUsage(neurons=20.4))
        if self.fail == "claims":
            raise ViewCheckError("claims correction exhausted", stage="claims", rejected=True)
        return CLAIMS

    def judge(self, view, claims, market):
        if self.fail == "judge":
            raise ViewCheckError("cloudflare timeout", stage="judge", rejected=False)
        self.usages.append(TokenUsage(neurons=500.0))
        number = {row["title"]: row["n"] for claim in claims for row in claim["candidates"]}
        return Judgement(
            [Verdict(1, "supported", [Pick(number["Fed Decision in December?"], [1])], "참여자들은 인상을 <크게> 본다."),
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


class _Room:
    """텔레그램 방 자리: `/view …`를 보내고, 검증이 끝날 때까지 기다려, 봇이 보낸 글을 돌려준다."""

    def __init__(self, tmp_path, monkeypatch, web):
        monkeypatch.setattr(handlers, "VIEW_CHECK_STATE_FILE", tmp_path / "view_check" / "view_check.json")
        monkeypatch.setattr(handlers, "Consensus", lambda: Consensus(web))
        self.checker = _Checker()
        self.bot_data = {
            "view_checker": self.checker,
            "watchlist_manager": SimpleNamespace(get_all=self._watchlist),
        }
        self.sent: list[str] = []

    async def _watchlist(self):
        return {"US:NASDAQ:NVDA": "NVIDIA"}

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)

    def say(self, text="", *, wait=True):
        async def run():
            context = SimpleNamespace(args=text.split(), bot_data=self.bot_data)
            await handlers.cmd_view(SimpleNamespace(effective_message=self), context)
            task = self.bot_data.get("view_check_task")
            if wait and task is not None:
                await task

        self.sent = []
        asyncio.run(run())
        return "\n\n".join(self.sent)


@pytest.fixture
def room(tmp_path, monkeypatch, web):
    return _Room(tmp_path, monkeypatch, web)


def test_a_view_is_checked_and_the_result_comes_back_to_the_room(room):
    assert "/view 뷰 문장" in room.say()                      # 아직 결과가 없으면 사용법
    assert "10~600자" in room.say("금리 오른다")               # 나눌 전제가 없는 길이
    assert room.checker.views == []

    text = room.say(VIEW)
    assert room.checker.views == [VIEW]
    assert "뷰 검증을 시작했습니다" in room.sent[0] and "오늘 1/10회" in room.sent[0]
    # 판정과 숫자: 짚은 선택지는 굵게, 숫자는 웹이 준 값이다. 모델이 쓴 글의 꺾쇠는 태그가 되지 않는다.
    assert "🟢 <b>1. 연준이 정책금리를 올린다</b> — 컨센서스가 받침" in text
    assert "참여자들은 인상을 &lt;크게&gt; 본다." in text
    assert "· 연준이 12월 회의에서 금리를 어떻게 정할지" in text and "<i>Fed Decision in December?</i>" in text
    assert "<b>25 bps increase 86%</b> · 50+ bps increase 11% · No change 3%" in text
    assert "⚪️ <b>2. 미국 국채금리가 오른다</b> — 확인할 질문 없음" in text
    assert "<b>뷰가 놓친 신호</b>" in text and "December 15 78%" in text
    # 포지션: 이름·성격·주의 문구는 봇이 붙인다. 어긋난 전제에만 기댄 확대는 지켜보기로 내려간다.
    assert "📉 <b>TIGER 미국채10년선물</b> (305080 · ETF · 정방향 · 중기, 환율 노출) — 비중 축소 검토" in text
    assert "접을 조건: 접는 조건" in text and "iShares 20+ Year Treasury Bond(TLT · 장기)" in text
    assert "👀 <b>iShares Bitcoin Trust ETF</b> (IBIT · ETF · 정방향 · 비트코인) — 지켜보기" in text
    assert "전부 컨센서스와 어긋나" in text
    assert "이번 검증 약 520 Neurons · 오늘 1/10회" in text and "AI 판정은 틀릴 수 있습니다" in text
    assert not re.search(r"(?i)polymarket|폴리마켓|베팅", text)
    # 포지션 단계는 다듬은 뷰와 관심종목을 받았다.
    payload, codes, claim_ids = room.checker.payloads[0]
    assert payload["view"].startswith("연준의 인상은") and payload["stocks"] == [{"code": "US:NASDAQ:NVDA", "name": "NVIDIA"}]
    assert "US:NASDAQ:NVDA" in codes and claim_ids == [1, 2, 3]
    saved = handlers.read_state()
    assert saved["report"]["generation_id"] == GENERATION and saved["usage"]["count"] == 1


def test_the_last_check_shows_how_far_the_consensus_moved_since(room, web):
    room.say(VIEW)
    web.events["fed"]["markets"][0]["yes_probability"] = 0.70    # 25bp 인상 86% → 70%
    del web.events["war"]                                        # 닫힌 질문
    text = room.say()
    assert room.checker.views == [VIEW]                          # 모델을 다시 부르지 않는다
    assert "<b>25 bps increase 86% → 70% (-16%p)</b>" in text
    assert "10%p 넘게 움직였습니다" in text and "닫혔을 수 있습니다" in text
    assert "No change 3% ·" not in text and "No change 3%" in text  # 움직이지 않은 선택지에는 화살표가 없다
    # 웹이 응답하지 않아도 최근 결과는 보인다(이동만 뺀다).
    web.down = True
    text = room.say()
    assert "25 bps increase 86%</b>" in text and "→" not in text

    web.down = False
    room.say("run")                                              # 최근 뷰로 다시 검증
    assert room.checker.views == [VIEW, VIEW] and handlers.read_state()["usage"]["count"] == 2


def test_failed_runs_count_and_the_day_has_a_limit(room, web, monkeypatch):
    room.checker.fail = "claims"
    assert "확인할 예상을 찾지 못했습니다" in room.say(VIEW)
    room.checker.fail = "judge"
    assert "뷰 검증 실패(판정 단계)" in room.say(VIEW)
    web.down, room.checker.fail = True, None
    assert "컨센서스 자료를 읽지 못했습니다" in room.say(VIEW)
    state = handlers.read_state()
    assert state["usage"]["count"] == 3 and state["report"] is None

    monkeypatch.setattr(handlers, "VIEW_CHECK_MAX_DAILY", 3)
    assert "하루 3회까지" in room.say(VIEW) and len(room.checker.views) == 3
    # 다음 날에는 다시 센다.
    monkeypatch.setattr(handlers, "today", lambda: now().date() + timedelta(days=1))
    web.down = False
    assert "오늘 1/3회" in room.say(VIEW)


def test_one_check_at_a_time_and_verdicts_survive_a_failed_positions_stage(room):
    room.checker.fail = "positions"

    async def twice():
        context = SimpleNamespace(args=VIEW.split(), bot_data=room.bot_data)
        update = SimpleNamespace(effective_message=room)
        await handlers.cmd_view(update, context)
        await handlers.cmd_view(update, context)                 # 첫 검증이 끝나기 전
        await room.bot_data["view_check_task"]

    asyncio.run(twice())
    text = "\n\n".join(room.sent)
    assert "이미 진행 중입니다" in text and room.checker.views == [VIEW]
    assert "🟢 <b>1. 연준이 정책금리를 올린다</b>" in text
    assert "포지션 아이디어를 받지 못했습니다" in text
    assert handlers.read_state()["report"]["positions_status"] == "failed"


def test_a_long_result_is_split_into_messages_telegram_accepts():
    signal = {"id": "x", "title": "T" * 150, "summary": "요약 " * 30, "end_date": "2026-12-31T00:00:00Z",
              "options": [{"label": "옵션", "probability": 0.004, "focus": False},
                          {"label": "다른 옵션", "probability": 0.996, "focus": True}]}
    report = {"generated_at": "2026-11-12T14:03:00+09:00", "view": VIEW, "refined_view": None, "blind_spots": [],
              "claims": [{"c": c, "claim": "전제 " * 15, "verdict": "mixed", "note": "해설 " * 40, "signals": [signal] * 3}
                         for c in range(1, 6)],
              "positions": [], "positions_status": "skipped", "neurons": None}
    messages = handlers.render(report, used=2)
    assert len(messages) > 1 and all(len(message) <= 4096 for message in messages)
    text = "\n\n".join(messages)
    assert "<b>🧭 뷰 검증</b> · 11/12 14:03" in text and "옵션 1% 미만" in text and "<b>다른 옵션 99% 초과</b>" in text
    assert "확인된 전제가 없어 포지션 아이디어를 내지 않았습니다" in text and "오늘 2/10회" in text


def test_the_web_being_down_is_an_error_not_an_empty_board(web):
    web.down = True
    with pytest.raises(ConsensusUnavailable):
        service.collect(Consensus(web), CLAIMS)
