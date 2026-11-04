"""예측 컨센서스 질의응답(`/api/forecast/ask`): 찾기 보강, 선택지 이름 색인, 답변 검증, 표시 규칙, API.

이 파일이 지키는 규칙은 다섯이다.

**숫자는 서버가 쓴다.** 모델 입력에 확률 숫자가 없고, 화면 숫자는 detail 값 그대로다.

**찾기가 답의 상한이다.** 2026-11-04 예비 측정의 실패 넷(또 올릴까 · 금값 · 엔비디아 · 일본은행)이 후보에 든다.

**맞는 질문이 없으면 없다고 한다.** 후보가 없으면 모델을 부르지 않는다.

**로그인하고 동의한 계정만, 하루 상한 안에서.** 캐시는 상한도 Neurons도 쓰지 않는다.

**질의 로그에 계정 식별값을 남기지 않는다.**
"""

from datetime import date
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from services.web import server
from services.web.accounts import COOKIE, Accounts
from services.web.core import config
from services.web.llm.backends import TokenUsage
from services.web.llm.polymarket_answer import (
    Answer,
    AnswerRejected,
    Pick,
    PolymarketAnswerError,
    PolymarketAnswerer,
    parse_answer,
)
from services.web.polymarket import relevance
from services.web.polymarket.annotate import build, refresh_labels
from services.web.polymarket.ask import build_ask_router, build_row, shown_options
from services.web.polymarket.repository import PolymarketRepository

GENERATION = "20261104T160000000000+0900"


def _market(market_id, label, yes, *, closed=False):
    return {"id": market_id, "outcome_label": label, "question": label, "yes_probability": yes,
            "no_probability": round(1 - yes, 6), "price_valid": True, "closed": closed}


def _event(event_id, title, event_type, markets, *, category="economy_finance", volume=50000.0,
           end="2026-12-31T00:00:00Z", price_status="ok"):
    compact = {"id": event_id, "title": title, "category": category, "category_label": "경제·금융",
               "event_type": event_type, "tags": [], "regions": [], "volume24hr": volume, "liquidity": 1000.0,
               "end_date": end, "data_status": "ok", "price_status": price_status, "leader": None,
               "leader_probability": None}
    return compact, {**compact, "markets": markets}


FED_DECEMBER = _event("770450", "Fed Decision in December?", "exclusive_multi", [
    _market("m1", "25 bps increase", 0.755), _market("m2", "No change", 0.235),
    _market("m3", "50+ bps increase", 0.015), _market("m4", "25 bps decrease", 0.0045),
    _market("m5", "50+ bps decrease", 0.0035),
], volume=1055996.0, end="2026-12-10T00:00:00Z")
FED_HIKE = _event("1034268", "Another Fed rate hike in 2026?", "binary", [_market("h1", "Yes", 0.745)],
                  volume=94043.0)
BOJ = _event("1045438", "Bank of Japan Decision in December?", "exclusive_multi", [
    _market("b1", "No change", 0.8), _market("b2", "25 bps increase", 0.2)], volume=182.0)
COLOMBIA = _event("9001", "Central Bank of Colombia decision in December", "exclusive_multi", [
    _market("c1", "25 bps increase", 0.6), _market("c2", "No change", 0.4)], volume=192.0)
FED_HIKES_COUNT = _event("9002", "How many Fed rate hikes in 2026?", "exclusive_multi", [
    _market("k1", "1", 0.7), _market("k2", "0", 0.3)], volume=6124.0)
LARGEST = _event("1302854", "Largest Company end of November 2026?", "exclusive_multi", [
    _market("l1", "NVIDIA", 0.9), _market("l2", "Apple", 0.06), _market("l3", "Microsoft", 0.03)],
    volume=11082.0, end="2026-11-30T00:00:00Z")
GOLD = _event("192787", "What will Gold (GC) hit__ by end of December?", "independent_multi", [
    _market("g1", "↑ $5,000", 0.12), _market("g2", "↑ $4,500", 0.55), _market("g3", "↑ $4,200", 0.9),
    _market("g4", "↓ $3,500", 0.08), _market("g5", "↓ $3,000", 0.01)], volume=14067.0)
BTC_YEAR = _event("89502", "What price will Bitcoin hit in 2026?", "independent_multi", [
    _market("y1", "↑ $150,000", 0.2), _market("y2", "↑ $120,000", 0.5), _market("y3", "↓ $80,000", 0.3)],
    category="crypto", volume=148922.0, end="2027-01-01T05:00:00Z")
BTC_TODAY = _event("9003", "What price will Bitcoin hit today?", "independent_multi", [
    _market("t1", "↑ $110,000", 0.4), _market("t2", "↓ $100,000", 0.3)],
    category="crypto", volume=588274.0, end="2026-11-04T23:00:00Z")
FOOTBALL = _event("9004", "Premier League Winner", "exclusive_multi", [
    _market("f1", "Arsenal", 0.4), _market("f2", "Liverpool", 0.3)], category="sports", volume=900000.0)
EVENTS = [FED_DECEMBER, FED_HIKE, BOJ, COLOMBIA, FED_HIKES_COUNT, LARGEST, GOLD, BTC_YEAR, BTC_TODAY, FOOTBALL]
INDEX = {
    "770450": {"h": "x", "summary": "연준이 12월 회의에서 금리를 어떻게 정할지", "keywords": ["연준", "금리", "FOMC"],
               "subtopic": "미국 통화정책"},
    "1034268": {"h": "x", "summary": "연준이 올해 금리를 한 번 더 올릴지", "keywords": ["연준", "금리", "2026년"],
                "subtopic": "미국 통화정책"},
    "1045438": {"h": "x", "summary": "일본은행이 12월 회의에서 금리를 어떻게 정할지",
                "keywords": ["일본은행", "금리", "통화정책"], "subtopic": "일본 통화정책"},
    "9001": {"h": "x", "summary": "콜롬비아 중앙은행이 12월에 금리를 어떻게 정할지",
             "keywords": ["콜롬비아", "금리", "금리 인상"], "subtopic": "중남미 통화정책"},
    "9002": {"h": "x", "summary": "연준이 올해 금리를 몇 번 올릴지", "keywords": ["연준", "금리 인상", "횟수"],
             "subtopic": "미국 통화정책"},
    "1302854": {"h": "x", "summary": "11월 말 세계 시가총액 1위 기업", "keywords": ["시가총액", "최대 기업"],
                "subtopic": "기업 순위"},
    "192787": {"h": "x", "summary": "금 선물이 12월 말까지 얼마에 닿을지", "keywords": ["금 선물", "원자재"],
               "subtopic": "원자재 가격"},
}


def _write(root, rows, index):
    """write_generation과 같은 꼴(byte-addressed detail)을 회계 검사 없이 쓴다."""
    folder = root / "generations" / GENERATION
    folder.mkdir(parents=True)
    events, offset = [], 0
    with open(folder / "details-000.jsonl", "wb") as handle:
        for compact, detail in rows:
            data = (json.dumps({**detail, "generation_id": GENERATION}, ensure_ascii=False) + "\n").encode()
            handle.write(data)
            events.append({**compact, "generation_id": GENERATION, "detail_ref": {
                "shard": "details-000.jsonl", "offset": offset, "length": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}})
            offset += len(data)
    (root / "current.json").write_text(json.dumps(
        {"generation_id": GENERATION, "generated_at": "2026-11-04T16:00:00+09:00", "events": events},
        ensure_ascii=False), encoding="utf-8")
    (root / "search_index.json").write_text(json.dumps({"events": index}, ensure_ascii=False), encoding="utf-8")
    return events


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "pm"
    events = _write(root, EVENTS, json.loads(json.dumps(INDEX)))
    index = json.loads((root / "search_index.json").read_text(encoding="utf-8"))
    refresh_labels(root, events, index["events"])
    (root / "search_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return PolymarketRepository(root)


def _titles(repository, question, limit=30):
    return [event["title"] for event, _ in repository.candidates(question, limit=limit)]


# ── 찾기 보강 ──────────────────────────────────────────────────────────────

def test_colloquial_verbs_reach_the_direct_question(repository):
    """"또 올릴까"는 키워드의 "인상"·제목의 hike에 닿는다(예비 측정에서 결과 27건에 없던 질문)."""
    assert "Another Fed rate hike in 2026?" in _titles(repository, "올해 미국 금리 또 올릴까")[:3]


def test_compound_words_reach_english_titles(repository):
    assert _titles(repository, "금값 사상 최고치")[0] == "What will Gold (GC) hit__ by end of December?"


def test_names_that_live_only_in_the_options_are_found(repository):
    """엔비디아는 *Largest Company* 제목이 아니라 선택지 이름에만 있다."""
    assert _titles(repository, "엔비디아 시가총액")[0] == "Largest Company end of November 2026?"


def test_another_central_bank_goes_behind(repository):
    titles = _titles(repository, "일본은행 금리 인상")
    assert titles[0] == "Bank of Japan Decision in December?"
    # 금리·인상 두 낱말이 맞아도 다른 중앙은행(연준·콜롬비아)의 질문은 뒤로 간다.
    assert titles.index("How many Fed rate hikes in 2026?") > 0
    assert titles.index("Central Bank of Colombia decision in December") > 0


def test_the_asked_period_beats_participation(repository):
    """참여 규모가 네 배인 오늘 마감 질문보다 연말에 마감하는 질문이 먼저다."""
    titles = _titles(repository, "비트코인 연말 가격")
    assert titles.index("What price will Bitcoin hit in 2026?") < titles.index("What price will Bitcoin hit today?")


def test_time_words_become_a_window_not_tokens():
    today = date(2026, 11, 4)
    assert relevance.query_tokens("연준 12월 금리 인하") == ["연준", "금리", "인하"]
    assert relevance.query_window("12월 연준", today) == (date(2026, 12, 1), date(2027, 1, 7))
    assert relevance.query_window("3월 일본은행", today)[0] == date(2027, 3, 1)
    assert relevance.query_window("비트코인 연말", today) == (date(2026, 12, 1), date(2027, 1, 15))
    assert relevance.query_window("내년 경기침체", today)[0] == date(2027, 1, 1)
    assert relevance.query_window("연준 금리", today) is None


def test_glossary_words_match_whole_words_only(repository):
    """용어집 표기 "cut"이 "cute"에 걸리지 않는다. 사용자의 영문 낱말은 지금처럼 앞머리부터 맞춘다."""
    groups = relevance.expand(["인하"])
    term = relevance.compile_tokens(groups)[0]
    assert term.pattern.search("fed rate cuts in 2026?")
    assert not term.pattern.search("a cute dog")
    assert relevance.compile_tokens([["tru"]])[0].pattern.search("trump approval")


def test_only_priced_events_become_candidates(tmp_path):
    root = tmp_path / "pm"
    unpriced = _event("1", "Apple's Market Cap end of 2026?", "exclusive_multi",
                      [_market("a", "$4T", 0.5)], price_status="unavailable")
    _write(root, [unpriced], {})
    assert PolymarketRepository(root).candidates("애플 시가총액", limit=30) == []


# ── 선택지 이름 색인 ──────────────────────────────────────────────────────────

def test_labels_go_only_to_non_sports_multi_outcome_events_with_participation(tmp_path):
    root = tmp_path / "pm"
    small = _event("s", "Small multi?", "exclusive_multi", [_market("x", "Alpha", 0.5)], volume=10.0)
    rows = [LARGEST, FED_HIKE, FOOTBALL, small]
    events = _write(root, rows, {})
    index = {key: {"h": "x", "summary": "요약입니다 충분히", "keywords": ["a", "b", "c"], "subtopic": "t"}
             for key in ("1302854", "1034268", "9004", "s")}

    assert refresh_labels(root, events, index) == 1
    assert index["1302854"]["labels"] == ["NVIDIA", "Apple", "Microsoft"]
    assert all("labels" not in index[key] for key in ("1034268", "9004", "s"))
    # 바뀐 것이 없으면 다시 쓰지 않는다.
    assert refresh_labels(root, events, index) == 0


def test_closed_options_leave_the_labels(tmp_path):
    root = tmp_path / "pm"
    compact, detail = LARGEST
    detail = {**detail, "markets": [_market("l1", "NVIDIA", 0.9), _market("l2", "Apple", 0.1, closed=True)]}
    events = _write(root, [(compact, detail)], {})
    index = {"1302854": {"h": "x", "labels": ["NVIDIA", "Apple"]}}
    refresh_labels(root, events, index)
    assert index["1302854"]["labels"] == ["NVIDIA"]


def test_labels_are_written_even_when_the_llm_budget_is_spent(tmp_path):
    root = tmp_path / "pm"
    events = _write(root, [LARGEST], {})
    index_path, status_path = root / "search_index.json", root / "annotate_status.json"
    index_path.write_text(json.dumps({"events": {"1302854": {
        "h": __import__("services.web.polymarket.annotate", fromlist=["title_hash"]).title_hash(events[0]["title"]),
        "summary": "11월 말 세계 시가총액 1위 기업", "keywords": ["시가총액", "최대 기업", "기업"], "subtopic": "기업 순위"}}},
        ensure_ascii=False), encoding="utf-8")

    result = build(root=root, index_path=index_path, status_path=status_path, annotator=object())

    assert result["state"] == "up_to_date" and result["labels_changed"] == 1 and result["labeled"] == 1
    stored = json.loads(index_path.read_text(encoding="utf-8"))["events"]["1302854"]
    assert stored["labels"] == ["NVIDIA", "Apple", "Microsoft"]


# ── 답변 검증 ──────────────────────────────────────────────────────────────

OPTIONS = {1: {1, 2, 3}, 2: {1, 2}}


def test_parser_drops_unknown_duplicate_and_foreign_choices():
    raw = json.dumps({"answerable": True, "commentary": "해설입니다 충분히 긴 문장입니다.", "picks": [
        {"n": 9, "direct": True, "focus": []}, {"n": 1, "direct": True, "focus": [3, 3, 7]},
        {"n": 1, "direct": False, "focus": []}, {"n": 2, "direct": False, "focus": [2]}]})
    answer = parse_answer(raw, OPTIONS, max_picks=5)
    assert [(p.n, p.direct, p.focus) for p in answer.picks] == [(1, True, [3]), (2, False, [2])]


def test_answerable_needs_a_direct_pick_and_unanswerable_keeps_two_related():
    with pytest.raises(AnswerRejected) as caught:
        parse_answer(json.dumps({"answerable": True, "commentary": "x", "picks": []}), OPTIONS, max_picks=5)
    assert caught.value.structural
    raw = json.dumps({"answerable": False, "commentary": "직접 맞는 질문이 없습니다.", "picks": [
        {"n": 1, "direct": True, "focus": []}, {"n": 2, "direct": True, "focus": []}]})
    answer = parse_answer(raw, {1: set(), 2: set(), 3: set()}, max_picks=5)
    assert [p.direct for p in answer.picks] == [False, False]


class _Backend:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.last_usage = None

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        self.last_usage = TokenUsage(input_tokens=4000, output_tokens=300, neurons=27.5)
        return self.replies.pop(0)


def _answerer(*replies):
    return PolymarketAnswerer(_Backend(*replies), config.POLYMARKET_ASK_PROMPT_FILE, 700, 5)


CANDIDATES = [{"n": 1, "title": "Fed Decision in December?", "type": "다지선다(하나만 정답)", "ends": "2026-12-10",
               "participation": "큼", "options": [{"m": 1, "label": "25 bps increase", "outlook": "높음"},
                                                  {"m": 2, "label": "No change", "outlook": "낮음"}]}]
GOOD = json.dumps({"answerable": True, "picks": [{"n": 1, "direct": True, "focus": []}],
                   "commentary": "12월 회의는 인하가 아니라 인상 쪽으로 크게 기울어 있습니다."})


def test_a_written_probability_is_corrected_once_then_dropped():
    with_number = json.dumps({"answerable": True, "picks": [{"n": 1, "direct": True, "focus": [1]}],
                              "commentary": "인상 확률이 75.5%로 크게 기울어 있습니다."})
    answerer = _answerer(with_number, with_number)

    answer = answerer.answer("연준 12월 금리 인하 확률은?", CANDIDATES)

    assert answer.commentary is None and [(p.n, p.focus) for p in answer.picks] == [(1, [1])]
    assert len(answer.usages) == 2
    revision = json.loads(answerer.backend.calls[1]["user_prompt"])["revision"]
    # 교정 입력에 직전 응답(숫자)을 돌려주지 않는다.
    assert "75" not in json.dumps(revision, ensure_ascii=False)
    assert answerer.backend.calls[0]["response_format"]["type"] == "json_schema"


def test_a_corrected_answer_is_used_and_broken_twice_fails():
    assert _answerer("not json", GOOD).answer("q", CANDIDATES).commentary.startswith("12월 회의는")
    with pytest.raises(PolymarketAnswerError):
        _answerer("not json", "{}").answer("q", CANDIDATES)


def test_the_source_name_is_rejected_in_commentary():
    named = json.dumps({"answerable": True, "picks": [{"n": 1, "direct": True, "focus": []}],
                        "commentary": "폴리마켓 참여자들은 인상 쪽으로 크게 기울어 있습니다."})
    assert _answerer(named, named).answer("q", CANDIDATES).commentary is None


def test_the_model_never_sees_probability_numbers():
    _, detail = FED_DECEMBER
    row = build_row(1, FED_DECEMBER[0], "연준이 12월 회의에서 금리를 어떻게 정할지", detail["markets"])
    text = json.dumps(row.model, ensure_ascii=False)
    assert "0.755" not in text and "75" not in text and "%" not in text
    assert row.model["options"][0] == {"m": 1, "label": "25 bps increase", "outlook": "높음"}
    assert row.options[1] == "m1"


# ── 표시 규칙 ──────────────────────────────────────────────────────────────

def test_binary_shows_the_majority_answer_and_an_asked_minority():
    compact, detail = _event("b", "Hormuz normal by Dec 31?", "binary", [_market("x", "Yes", 0.115)])
    assert shown_options(compact, detail["markets"], set()) == [
        {"label": "아니오", "probability": 0.885, "focus": False}]
    assert [o["label"] for o in shown_options(compact, detail["markets"], {"yes"})] == ["아니오", "예"]


def test_exclusive_shows_top_three_and_the_asked_option():
    compact, detail = FED_DECEMBER
    options = shown_options(compact, detail["markets"], {"m4"})
    assert [(o["label"], o["probability"], o["focus"]) for o in options] == [
        ("25 bps increase", 0.755, False), ("No change", 0.235, False), ("50+ bps increase", 0.015, False),
        ("25 bps decrease", 0.0045, True)]


def test_a_ladder_shows_its_slope():
    compact, detail = GOLD
    labels = [o["label"] for o in shown_options(compact, detail["markets"], set())]
    # 3~97% 칸에서 가장 높은 칸·50%에 가까운 칸·가장 낮은 칸
    assert labels == ["↑ $4,200", "↑ $4,500", "↓ $3,500"]


def test_unopened_placeholder_options_are_never_shown_or_indexed(tmp_path):
    """열리지 않은 자리채움 칸("Company H", 기본값 50%)이 다지선다 위 셋을 차지하던 것(2026-11-04 실측)."""
    placeholder = {**_market("ph", "Company H", 0.5), "active": False}
    compact, detail = _event("p", "Largest Company end of November 2026?", "exclusive_multi", [
        _market("n", "NVIDIA", 0.76), _market("a", "Apple", 0.24), placeholder])
    from services.web.polymarket.ask import priced_markets
    markets = priced_markets(detail)
    assert [o["label"] for o in shown_options(compact, markets, set())] == ["NVIDIA", "Apple"]
    from services.web.polymarket.annotate import outcome_labels
    assert outcome_labels(detail) == ["NVIDIA", "Apple"]


def test_the_number_in_the_question_marks_its_option():
    """모델이 물은 선택지를 짚지 않아도 "1500원"이 이름에 든 칸(↑1,500)은 늘 보인다. "2026년"은 기준이 아니다."""
    compact, detail = _event("k", "Will USD/KRW hit __ in 2026?", "independent_multi", [
        _market("u1", "↑1,800", 0.04), _market("u2", "↑1,500", 0.2), _market("d1", "↓1,300", 0.355),
        _market("d2", "↓1,200", 0.12)])
    from services.web.polymarket.ask import Row, assemble
    row = Row(compact, None, detail["markets"], {}, {})
    answer = Answer(True, [Pick(1, True, [])], "참여자들은 1500원 돌파를 낮게 봅니다.")
    payload = assemble("2026년 원달러 환율 1500원 넘을까?", {"generated_at": "t"}, [row], answer)
    options = {o["label"]: o["focus"] for o in payload["items"][0]["options"]}
    assert options["↑1,500"] is True and not any(focus for label, focus in options.items() if label != "↑1,500")


def test_the_nearest_threshold_stands_in_when_the_asked_number_is_missing():
    """"1500원"에 맞는 칸이 없으면 25% 안에서 가장 가까운 칸(↑1,600)이다. 규모가 다른 숫자에는 붙지 않는다."""
    compact, detail = _event("k", "Will USD/KRW hit __ in 2026?", "independent_multi", [
        _market("u1", "↑1,600", 0.09), _market("u2", "↑1,800", 0.04), _market("d1", "↓1,300", 0.355)])
    from services.web.polymarket.ask import asked_markets
    terms = relevance.compile_tokens(relevance.expand(relevance.query_tokens("원달러 환율 1500원 넘을까")))
    assert asked_markets("원달러 환율 1500원 넘을까", terms, detail["markets"]) == {"u1"}
    assert asked_markets("환율 9000원 넘을까", terms, detail["markets"]) == set()


def test_korean_units_and_the_asked_period_mark_their_options():
    """"15만 달러"는 150,000·$150k에, "연말"은 그해 12월 날짜 칸에 닿는다. 날짜·횟수 숫자는 기준이 아니다."""
    from services.web.polymarket.ask import asked_markets
    price = [_market("p1", "↑ 150,000", 0.1), _market("p2", "↓ 70,000", 0.4)]
    assert asked_markets("비트코인 15만 달러 갈까", [], price) == {"p1"}
    assert asked_markets("비트코인 12월 3번째 주", [], price) == set()
    dates = [_market("d1", "by December 31, 2026", 0.09), _market("d2", "by June 30, 2027", 0.4),
             _market("d3", "by December 31, 2027", 0.6)]
    window = relevance.query_window("비트코인 연말에 10만 달러 갈까", date(2026, 11, 4))
    assert asked_markets("비트코인 연말에 10만 달러 갈까", [], dates, window) == {"d1"}
    ceasefire = [_market("c1", "November 30", 0.5), _market("c2", "December 31", 0.365)]
    window = relevance.query_window("휴전 12월에 끝날까", date(2026, 11, 4))
    assert asked_markets("휴전 12월에 끝날까", [], ceasefire, window) == {"c2"}


def test_a_ladder_shows_the_model_each_full_question_and_bare_numbers_do_not_match_names():
    """사다리 칸은 모델에 전체 질문으로 간다(짧은 이름을 거꾸로 읽었다). "15만"의 "15"는 "↓ 15,000"에 걸리지 않는다."""
    compact, detail = _event("i", "Israel x Iran ceasefire continues through...?", "independent_multi", [
        {**_market("n7", "November 7", 0.865), "question": "Israel x Iran ceasefire continues through November 7?"},
        {**_market("d31", "December 31", 0.365), "question": "Israel x Iran ceasefire continues through December 31?"}])
    row = build_row(1, compact, None, detail["markets"])
    assert row.model["options"][0]["label"] == "Israel x Iran ceasefire continues through November 7?"
    from services.web.polymarket.ask import Row, assemble
    bitcoin, prices = _event("b", "What price will Bitcoin hit in 2026?", "independent_multi", [
        _market("p1", "↑ 150,000", 0.017), _market("p2", "↓ 15,000", 0.007), _market("p3", "↓ 70,000", 0.425)])
    row = Row(bitcoin, None, prices["markets"], {}, {})
    payload = assemble("비트코인 연말에 15만 달러 갈까?", {"generated_at": "t"}, [row],
                       Answer(True, [Pick(1, True, [])], "참여자들은 15만 달러 돌파를 낮게 봅니다."))
    assert {o["label"] for o in payload["items"][0]["options"] if o["focus"]} == {"↑ 150,000"}


def test_a_model_that_marks_every_option_marks_none():
    """모델이 사다리 칸을 다 찍으면(실측: 휴전 날짜 11칸) 짚은 것이 아니다 — 규칙대로 셋만 보인다."""
    from services.web.polymarket.ask import Row, assemble
    compact, detail = GOLD
    row = Row(compact, None, detail["markets"], {m: market["id"] for m, market in enumerate(detail["markets"], 1)}, {})
    answer = Answer(True, [Pick(1, True, [1, 2, 3, 4, 5])], "참여자들은 금값 상승을 엇갈리게 봅니다.")
    payload = assemble("금값 사상 최고치 찍을까?", {"generated_at": "t"}, [row], answer)
    assert [(o["label"], o["focus"]) for o in payload["items"][0]["options"]] == [
        ("↑ $4,200", False), ("↑ $4,500", False), ("↓ $3,500", False)]


def test_related_questions_stay_at_two_even_with_a_direct_answer():
    raw = json.dumps({"answerable": True, "commentary": "해설입니다 충분히 긴 문장입니다.", "picks": [
        {"n": 1, "direct": True, "focus": []}, *({"n": n, "direct": False, "focus": []} for n in range(2, 6))]})
    answer = parse_answer(raw, {n: set() for n in range(1, 6)}, max_picks=5)
    assert [(p.n, p.direct) for p in answer.picks] == [(1, True), (2, False), (3, False)]


# ── API ────────────────────────────────────────────────────────────────────

class _FakeAnswerer:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def answer(self, question, candidates):
        self.calls.append((question, candidates))
        if self.error:
            raise self.error
        row = next(c for c in candidates if c["title"] == "Fed Decision in December?")
        asked = next(o["m"] for o in row["options"] if o["label"] == "25 bps decrease")
        return Answer(True, [Pick(row["n"], True, [asked])],
                      "12월 회의는 인하가 아니라 인상 쪽으로 크게 기울어 있습니다.", [TokenUsage(neurons=31.0)])


@pytest.fixture
def api(tmp_path, monkeypatch, repository):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    accounts = Accounts(tmp_path / "users", client_id="client", client_secret="secret",
                        identity_key="k" * 32, origin="https://testserver")
    answerer = _FakeAnswerer()
    app = server.build_app(accounts=accounts, ask_router=build_ask_router(
        accounts, repository, answerer_factory=lambda: answerer))
    client = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
    client.cookies.set(COOKIE, accounts.issue("subject-a"))
    anonymous = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
    return accounts, client, anonymous, answerer


def _ask(client, question="연준 12월 금리 인하 확률은?"):
    return client.post("/api/forecast/ask", json={"question": question})


def test_asking_needs_login_and_consent(api):
    accounts, client, anonymous, answerer = api
    assert anonymous.get("/api/forecast/ask").status_code == 401
    assert _ask(anonymous).status_code == 401
    assert client.get("/api/forecast/ask").json() == {"consented_at": None, "used": 0, "limit": 10}
    assert _ask(client).status_code == 403
    assert answerer.calls == []
    assert client.put("/api/forecast/ask/consent", json={"agree": True}).json()["consented_at"]
    assert client.post("/api/forecast/ask", json={"question": "연준"},
                       headers={"origin": "https://evil.example"}).status_code == 403


def test_the_answer_carries_server_numbers_and_the_asked_option(api):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})

    response = _ask(client)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["answerable"] is True and body["cached"] is False
    assert body["commentary"].startswith("12월 회의는")
    item = body["items"][0]
    assert item["title"] == "Fed Decision in December?" and item["summary"].startswith("연준이 12월")
    # "인하"는 용어집으로 decrease에 닿아 인하 칸 둘이 물은 선택지로 보인다(서버가 정한다).
    assert [(o["label"], o["probability"], o["focus"]) for o in item["options"]] == [
        ("25 bps increase", 0.755, False), ("No change", 0.235, False), ("50+ bps increase", 0.015, False),
        ("25 bps decrease", 0.0045, True), ("50+ bps decrease", 0.0035, True)]
    assert body["generated_at"] == "2026-11-04T16:00:00+09:00" and body["usage"] == {"used": 1, "limit": 10}
    question, candidates = answerer.calls[0]
    assert question == "연준 12월 금리 인하 확률은?"
    assert "0.755" not in json.dumps(candidates)


def test_a_repeated_question_is_served_from_the_cache_without_spending(api):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    _ask(client)
    again = _ask(client, " 연준  12월 금리 인하 확률은? ").json()
    assert again["cached"] is True and again["usage"]["used"] == 1
    assert len(answerer.calls) == 1


def test_daily_and_server_limits(api, monkeypatch):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    monkeypatch.setattr(config, "POLYMARKET_ASK_MAX_DAILY", 1)
    assert _ask(client).status_code == 200
    assert _ask(client, "올해 미국 금리 또 올릴까").status_code == 429

    monkeypatch.setattr(config, "POLYMARKET_ASK_MAX_DAILY", 10)
    monkeypatch.setattr(config, "POLYMARKET_ASK_SERVER_MAX_DAILY", 2)
    assert _ask(client, "올해 미국 금리 또 올릴까").status_code == 200
    assert _ask(client, "금값 사상 최고치").status_code == 429
    assert len(answerer.calls) == 2


def test_no_candidates_means_no_answer_and_no_model_call(api):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    body = _ask(client, "원달러 환율 1500원").json()
    assert body["answerable"] is False and body["items"] == [] and body["usage"]["used"] == 0
    assert answerer.calls == []


def test_a_failed_answer_counts_and_says_so(api):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    answerer.error = PolymarketAnswerError("timeout")
    response = _ask(client)
    assert response.status_code == 502
    assert client.get("/api/forecast/ask").json()["used"] == 1


def test_the_question_log_has_no_account_identity(api):
    accounts, client, _, _ = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    _ask(client)
    _ask(client)
    logs = list((accounts.root / ".forecast-ask-log").glob("*.jsonl"))
    lines = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    assert [line["cached"] for line in lines] == [False, True]
    assert lines[0]["question"] == "연준 12월 금리 인하 확률은?" and lines[0]["events"] == ["770450"]
    assert lines[0]["neurons"] == 31.0
    account_key = next(path.name for path in accounts.root.iterdir() if not path.name.startswith("."))
    assert account_key not in logs[0].read_text(encoding="utf-8")


def test_forbidden_copy_in_source_text_drops_the_item(api, repository):
    accounts, client, _, answerer = api
    client.put("/api/forecast/ask/consent", json={"agree": True})
    index = json.loads((repository.root / "search_index.json").read_text(encoding="utf-8"))
    index["events"]["770450"]["summary"] = "폴리마켓 참여자들이 12월 연준 결정을 어떻게 볼지"
    (repository.root / "search_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    body = _ask(client).json()
    assert body["items"] == [] and body["answerable"] is False and body["commentary"] is None


def test_the_forecast_page_offers_the_question_box_and_privacy_names_it():
    body = server.POLYMARKET_HTML
    for text in ("질문으로 컨센서스 보기", "/auth/google?next=/forecast", "Cloudflare Workers AI", "pm-ask-q"):
        assert text in body, text
    assert "예측 컨센서스 화면의 질문 답변" in server.PRIVACY_HTML
    assert f"{config.POLYMARKET_ASK_LOG_DAYS}일 보관" in server.PRIVACY_HTML
