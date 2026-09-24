"""폴리마켓 자연어 검색: 주석 one-shot, 관련도 검색, 화면 표기.

이 파일이 지키는 규칙은 넷이다.

**검색하는 순간에는 LLM을 부르지 않는다.** 한국어 질문이 영어 제목에 닿는
다리는 미리 달아 둔 키워드다.

**새로 생겼거나 제목이 바뀐 event만 부른다.** 한 번 단 주석은 event가 닫힐
때까지 다시 쓴다 — 그래야 비용이 신규 event 수에 비례한다.

**하루 Neurons 예산을 스스로 지킨다. 실패한 호출도 센다.**

**요약에 확률을 쓰지 않는다.** 3시간마다 바뀌는 값을 한 번 만든 주석에 넣으면
곧 틀린 문장이 된다.
"""

import json

from fastapi.testclient import TestClient

from services.web import server
from services.web.core.clock import now
from services.web.llm import PolymarketAnnotationError
from services.web.llm.backends import CloudflareWorkersAIBackend, ResilientBackend, TokenUsage
from services.web.llm.polymarket_annotation import PolymarketAnnotator, parse_response
from services.web.polymarket import relevance
from services.web.polymarket.annotate import build, call_neurons, title_hash
from services.web.polymarket.repository import PolymarketRepository

GENERATION = "20260923T000000000000+0900"


def _event(index, title, *, volume=100.0, tags=()):
    return {
        "id": str(index),
        "title": title,
        "tags": list(tags),
        "volume24hr": volume,
        "liquidity": 5000.0,
        "leader": "Yes",
        "leader_probability": 0.7,
        "data_status": "ok",
        "event_type": "binary",
        "category": "economy_finance",
        "category_label": "경제·금융",
        "regions": [],
        "end_date": "2026-12-31T00:00:00Z",
    }


def _write_current(root, events):
    root.mkdir(parents=True, exist_ok=True)
    (root / "current.json").write_text(
        json.dumps({"generation_id": GENERATION, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )


def _row(summary="연준이 12월 회의에서 기준금리를 내릴지", keywords=None, subtopic="미국 통화정책"):
    return {
        "summary": summary,
        "keywords": keywords or ["연준", "미국 금리", "금리 인하", "FOMC", "파월"],
        "subtopic": subtopic,
    }


class _Backend:
    def __init__(self, usage=None):
        self.last_usage = usage


class _Annotator:
    """번호마다 같은 주석을 돌려주는 가짜. 부른 배치를 남긴다."""

    def __init__(self, *, fail=None, usage=TokenUsage(neurons=10.0)):
        self.backend = _Backend(usage)
        self.calls = []
        self._fail = fail

    def annotate(self, events):
        from services.web.llm import AnnotationBatch

        self.calls.append(events)
        if self._fail is not None:
            raise self._fail
        return AnnotationBatch(rows={event["n"]: _row(summary=f"{event['title']} 질문인지") for event in events})


# ── 응답 검사 ───────────────────────────────────────────────────────────────

def test_parser_keeps_good_rows_and_drops_bad_ones():
    raw = json.dumps({"events": [
        {"n": 1, **_row()},
        {"n": 2, **_row(summary="연준이 금리를 내릴 확률 70%인지")},
        {"n": 3, **_row(keywords=["연준", "금리"])},
        {"n": 9, **_row()},
    ]}, ensure_ascii=False)

    batch = parse_response(raw, {1, 2, 3})

    assert list(batch.rows) == [1]
    assert batch.dropped == 3
    assert batch.rows[1]["subtopic"] == "미국 통화정책"


def test_a_duplicated_number_drops_both_copies():
    raw = json.dumps({"events": [{"n": 1, **_row()}, {"n": 1, **_row()}]}, ensure_ascii=False)

    assert parse_response(raw, {1}).rows == {}


def test_keywords_are_deduplicated_without_case():
    raw = json.dumps(
        {"events": [{"n": 1, **_row(keywords=["Fed", "fed", "연준", "금리", "FOMC"])}]},
        ensure_ascii=False,
    )

    assert parse_response(raw, {1}).rows[1]["keywords"] == ["Fed", "연준", "금리", "FOMC"]


def test_broken_json_fails_the_whole_batch():
    try:
        parse_response('{"events": [', {1})
    except PolymarketAnnotationError:
        return
    raise AssertionError("broken JSON must fail")


def test_the_annotator_asks_for_structured_output(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("prompt", encoding="utf-8")
    seen = {}

    class _Recording:
        def generate(self, **kwargs):
            seen.update(kwargs)
            return json.dumps({"events": [{"n": 1, **_row()}]}, ensure_ascii=False)

    batch = PolymarketAnnotator(_Recording(), prompt, 100).annotate([{"n": 1, "title": "t"}])

    assert seen["response_format"]["type"] == "json_schema"
    assert list(batch.rows) == [1]


# ── 주석 one-shot ──────────────────────────────────────────────────────────

def _paths(tmp_path):
    root = tmp_path / "pm"
    return root, root / "search_index.json", root / "annotate_status.json"


def test_only_new_or_retitled_events_are_sent_largest_first(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [
        _event(1, "Fed cut in December?", volume=10),
        _event(2, "Trump tariff on Korea?", volume=500),
        _event(3, "Bitcoin above 100k?", volume=50),
    ])
    index.write_text(json.dumps({"events": {
        "1": {"h": title_hash("Fed cut in December?"), **_row()},
        "3": {"h": title_hash("Bitcoin above 90k?"), **_row()},
    }}, ensure_ascii=False), encoding="utf-8")
    annotator = _Annotator()

    result = build(root=root, index_path=index, status_path=status, annotator=annotator)

    sent = [event["title"] for event in annotator.calls[0]]
    assert sent == ["Trump tariff on Korea?", "Bitcoin above 100k?"]
    assert result["annotated_now"] == 2 and result["pending"] == 0
    stored = json.loads(index.read_text(encoding="utf-8"))["events"]
    assert stored["3"]["h"] == title_hash("Bitcoin above 100k?")
    assert stored["1"]["summary"] == _row()["summary"]


def test_closed_events_leave_the_index(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(1, "Open question?")])
    index.write_text(json.dumps({"events": {
        "1": {"h": title_hash("Open question?"), **_row()},
        "99": {"h": "x", **_row()},
    }}), encoding="utf-8")

    result = build(root=root, index_path=index, status_path=status, annotator=_Annotator())

    assert result["state"] == "up_to_date" and result["pruned"] == 1
    assert list(json.loads(index.read_text(encoding="utf-8"))["events"]) == ["1"]


def test_over_budget_skips_without_building_the_model(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(1, "Anything?")])
    status.write_text(json.dumps({"samples": [{"at": now().isoformat(), "neurons": 5000}]}), encoding="utf-8")

    class _Explodes:
        def annotate(self, events):
            raise AssertionError("must not be called")

    result = build(
        root=root, index_path=index, status_path=status,
        annotator=_Explodes(), max_daily_neurons=4000,
    )

    assert result["state"] == "skipped_budget"
    assert not index.exists()


def test_old_samples_fall_out_of_the_window(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(1, "Anything?")])
    status.write_text(json.dumps({"samples": [{"at": "2020-01-01T00:00:00+09:00", "neurons": 99999}]}), encoding="utf-8")

    result = build(root=root, index_path=index, status_path=status, annotator=_Annotator())

    assert result["state"] == "ok" and result["annotated_now"] == 1


def test_the_run_stops_once_the_budget_is_spent(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(i, f"Question {i}?") for i in range(10)])
    annotator = _Annotator(usage=TokenUsage(neurons=30.0))

    result = build(
        root=root, index_path=index, status_path=status, annotator=annotator,
        batch_size=2, max_daily_neurons=50,
    )

    assert len(annotator.calls) == 2
    assert result["state"] == "stopped_budget"
    saved = json.loads(status.read_text(encoding="utf-8"))
    assert saved["rolling_neurons"] == 60.0 and saved["pending"] == 6


def test_failed_calls_count_against_the_budget(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(1, "Anything?")])
    annotator = _Annotator(fail=PolymarketAnnotationError("bad json"), usage=None)

    result = build(root=root, index_path=index, status_path=status, annotator=annotator, max_tokens=1000)

    assert result["state"] == "failed"
    # 응답이 없으면 예약한 출력 전부가 나갔다고 본다.
    assert result["neurons"] >= 1000 * 0.0304


def test_quota_exhaustion_stops_the_run_without_failing_it(tmp_path):
    root, index, status = _paths(tmp_path)
    _write_current(root, [_event(i, f"Question {i}?") for i in range(4)])
    annotator = _Annotator(fail=PolymarketAnnotationError("quota", stop=True))

    result = build(root=root, index_path=index, status_path=status, annotator=annotator, batch_size=1)

    assert len(annotator.calls) == 1
    assert result["state"] == "stopped_llm"


def test_neurons_fall_back_to_the_token_formula():
    backend = _Backend(TokenUsage(input_tokens=1000, output_tokens=100))

    assert round(call_neurons(backend, 0, 4096), 2) == round(1000 * 0.00463 + 100 * 0.0304, 2)


def test_the_web_backend_remembers_the_last_usage():
    class _Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "neurons": 1.5},
            }

    class _Session:
        def post(self, *args, **kwargs):
            return _Response()

    backend = CloudflareWorkersAIBackend(account_id="a", api_token="t", model="m", session=_Session())
    wrapped = ResilientBackend(backend=backend)
    wrapped.generate(system_prompt="s", user_prompt="u", max_tokens=10, temperature=0)

    assert wrapped.last_usage.neurons == 1.5


# ── 관련도 검색 ────────────────────────────────────────────────────────────

def test_tokens_drop_particles_and_intent_words():
    assert relevance.query_tokens("트럼프 관세가 한국에 미칠 영향 배팅 찾아줘") == ["트럼프", "관세", "한국", "미칠"]


def test_only_stopwords_still_search_something():
    assert relevance.query_tokens("폴리마켓 배팅") == ["폴리마켓", "배팅"]


def _repository(tmp_path, events, annotations):
    root = tmp_path / "pm"
    _write_current(root, events)
    (root / "search_index.json").write_text(
        json.dumps({"events": annotations}, ensure_ascii=False), encoding="utf-8"
    )
    return PolymarketRepository(root)


def test_a_korean_question_reaches_an_english_title_through_keywords(tmp_path):
    repository = _repository(
        tmp_path,
        [_event(1, "Fed decision in December?", volume=10), _event(2, "Bitcoin above 100k?", volume=9999)],
        {"1": {"h": "x", **_row()}},
    )

    payload = repository.events(query="연준 금리 인하 배팅")

    assert [event["id"] for event in payload["events"]] == ["1"]
    assert payload["sort"] == "relevance"
    assert payload["events"][0]["summary"] == _row()["summary"]
    assert payload["search_index"] == {"annotated": 1, "total": 2}


def test_relevance_beats_volume(tmp_path):
    repository = _repository(
        tmp_path,
        [
            _event(1, "Will Trump raise tariffs on Korea?", volume=10),
            _event(2, "Will Trump win the 2028 primary?", volume=99999),
        ],
        {},
    )

    # 두 낱말이면 하나만 맞아도 남는다. 둘 다 맞은 쪽이 거래량과 무관하게 먼저다.
    payload = repository.events(query="trump tariffs")

    assert [event["id"] for event in payload["events"]] == ["1", "2"]


def test_half_of_the_words_must_match(tmp_path):
    repository = _repository(
        tmp_path,
        [_event(1, "Korea election winner?"), _event(2, "Trump tariff on Korea?")],
        {},
    )

    payload = repository.events(query="trump tariff korea")

    assert [event["id"] for event in payload["events"]] == ["2"]


def test_english_prefixes_match_while_typing_but_short_words_need_the_whole_word(tmp_path):
    repository = _repository(
        tmp_path,
        [_event(1, "Trump approval rating?"), _event(2, "What will he said about AI?")],
        {},
    )

    assert [e["id"] for e in repository.events(query="tru")["events"]] == ["1"]
    assert [e["id"] for e in repository.events(query="ai")["events"]] == ["2"]
    assert repository.events(query="sa")["total"] == 0


def test_an_explicit_sort_still_filters_by_relevance(tmp_path):
    repository = _repository(
        tmp_path,
        [_event(1, "Trump A?", volume=1), _event(2, "Trump B?", volume=5), _event(3, "Other?", volume=9)],
        {},
    )

    payload = repository.events(query="trump", sort="volume24hr")

    assert [event["id"] for event in payload["events"]] == ["2", "1"]


def test_relevance_without_a_query_falls_back_to_volume(tmp_path):
    repository = _repository(tmp_path, [_event(1, "A?", volume=1), _event(2, "B?", volume=5)], {})

    payload = repository.events(sort="relevance")

    assert payload["sort"] == "volume24hr"
    assert [event["id"] for event in payload["events"]] == ["2", "1"]


def test_the_manifest_is_not_mutated_by_attaching_summaries(tmp_path):
    repository = _repository(tmp_path, [_event(1, "Fed?")], {"1": {"h": "x", **_row()}})

    repository.events()

    assert "summary" not in repository.load()["events"][0]


def test_the_etag_changes_when_only_the_index_changes(tmp_path, monkeypatch):
    repository = _repository(tmp_path, [_event(1, "Fed?")], {})
    monkeypatch.setattr(server, "POLYMARKET_REPOSITORY", repository)
    client = TestClient(server.build_app())
    first = client.get("/api/forecast/events", params={"q": "연준"})
    assert first.json()["total"] == 0

    index = tmp_path / "pm" / "search_index.json"
    index.write_text(json.dumps({"events": {"1": {"h": "x", **_row()}}}, ensure_ascii=False), encoding="utf-8")
    import os
    stat = index.stat()
    os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = client.get(
        "/api/forecast/events", params={"q": "연준"},
        headers={"if-none-match": first.headers["etag"]},
    )

    assert second.status_code == 200
    assert second.json()["total"] == 1


# ── 화면 표기 ──────────────────────────────────────────────────────────────

def test_the_polymarket_page_speaks_plain_korean():
    body = server.POLYMARKET_HTML

    for jargon in ("Binary", "EVENT", "MARKET", "generation을", "freshness ", "'pp'",
                   "event 탐색기", "주의 event", "<span>Yes ", " · No ", "24h ", "UTC +9"):
        assert jargon not in body, jargon
    for plain in ("이지선다", "열린 예측 질문", "한국 시간", "'%p'", "자연어로 찾기"):
        assert plain in body, plain
