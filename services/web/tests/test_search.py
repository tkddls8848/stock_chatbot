"""검색어 해석·실제 자료 검색·공개 경계·갱신·저장 실패를 검증한다."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from services.web import search, server
from services.web.core.storage import write_json_atomic


@pytest.fixture(autouse=True)
def frozen(monkeypatch):
    monkeypatch.setattr(search, "today", lambda: date(2026, 9, 23))


def article(key="jp", **changes):
    return {
        "id": key, "kind": "news", "market": "JP", "date": "2026-09-22",
        "published_at": "2026-09-22T15:00:00+09:00", "title": "BOJ interest rate hike lifts yen",
        "text": "Bank of Japan considers higher interest rates", "sentiment": -0.3,
        "source": "와이어", "url": "https://example.com/news", **changes,
    }


def corpus(tmp_path, rows):
    write_json_atomic(tmp_path / "news.json", {"generated_at": "2026-09-23T12:00:00+09:00", "documents": rows})
    return search.NewsSearch(tmp_path)


@pytest.mark.parametrize(("query", "market", "start", "end", "topics", "sentiment"), [
    ("최근 일주일 일본 금리 뉴스", ["JP"], "2026-09-17", "2026-09-23", ["금리"], None),
    ("미국 반도체 악재", ["US"], "2026-08-25", "2026-09-23", ["반도체"], "negative"),
    ("어제 한국 시장", ["KR"], "2026-09-22", "2026-09-22", [], None),
    ("이번 주 홍콩 물가 뉴스 찾아줘", ["HK"], "2026-09-21", "2026-09-23", ["인플레이션"], None),
    ("지난주 중국 관세", ["CN"], "2026-09-14", "2026-09-20", ["관세"], None),
    ("최근 3일 일본 금리 인상에 대한 뉴스", ["JP"], "2026-09-21", "2026-09-23", ["금리", "인상"], None),
    ("2026-09-01부터 2026-09-04까지 중국 뉴스", ["CN"], "2026-09-01", "2026-09-04", [], None),
    ("이번 달 한국 긍정적인 뉴스", ["KR"], "2026-09-01", "2026-09-23", [], "positive"),
])
def test_natural_query_conditions(query, market, start, end, topics, sentiment):
    parsed = search.interpret(query)
    assert parsed == {"markets": market, "start_date": start, "end_date": end,
                      "topics": topics, "sentiment": sentiment, "keywords": []}


def test_bank_of_japan_is_a_topic_not_a_country_prefix():
    parsed = search.interpret("일본은행 금리 인상 뉴스 찾아줘")
    assert set(parsed["topics"]) == {"일본은행", "금리", "인상"}
    assert parsed["keywords"] == []


def test_korean_question_matches_english_and_japanese_evidence(tmp_path):
    repo = corpus(tmp_path, [article(), article("ja", title="日銀が利上げを検討", text="金利政策"),
                             article("cut", title="BOJ rate cut", text="easing"),
                             article("old", date="2026-09-01"),
                             article("us", market="US")])
    results = repo.search("최근 일주일 일본 금리 인상 뉴스")["results"]
    assert {row["id"] for row in results} == {"jp", "ja"}


def test_sentiment_is_evidence_score_and_missing_scores_are_not_invented(tmp_path):
    repo = corpus(tmp_path, [article("bad", market="US", title="Chip earnings fall"),
                             article("good", market="US", title="Semiconductor profit", sentiment=0.5),
                             article("unknown", market="US", title="Chip report", sentiment=None)])
    assert [r["id"] for r in repo.search("미국 반도체 악재")["results"]] == ["bad"]
    assert [r["id"] for r in repo.search("미국 반도체 호재")["results"]] == ["good"]


def test_explicit_controls_override_query_and_unknown_terms_do_not_return_everything(tmp_path):
    repo = corpus(tmp_path, [article("kr", market="KR", date="2026-09-23"), article()])
    found = repo.search("어제 일본 시장", market="KR", days=1)
    assert [r["id"] for r in found["results"]] == ["kr"]
    assert repo.search("없는회사이름")['total'] == 0


def test_english_tokens_do_not_match_inside_unrelated_words(tmp_path):
    repo = corpus(tmp_path, [article(title="Officials said economy is stable", text=""),
                             article("ai", title="AI demand", text="")])
    assert [r["id"] for r in repo.search("AI 뉴스")["results"]] == ["ai"]


def test_daily_summaries_are_searchable_but_research_and_private_fields_are_not(tmp_path):
    repo = corpus(tmp_path, [article(mentioned_stocks=["SECRET"], chat_id="SECRET")])
    write_json_atomic(tmp_path / "market.json", {"markets": {"JP": {"daily": [
        {"date": "2026-09-22", "summary": "반도체 수출 감소", "avg_sentiment": -0.2},
    ]}}, "generated_at": "2026-09-22T23:00:00+09:00"})
    write_json_atomic(tmp_path / "research.json", {"last_result": {"summary": "SECRET"}})
    assert repo.search("일본 반도체")["results"][0]["kind"] == "market"
    assert repo.search("SECRET")["total"] == 0
    assert "SECRET" not in json.dumps(repo.search(""))


def test_cache_refresh_paging_dates_and_unsafe_links(tmp_path, monkeypatch):
    repo = corpus(tmp_path, [article(str(i), title=f"금리 뉴스 {i:02d}") for i in range(25)])
    first = repo.search("금리", page=1)
    second = repo.search("금리", page=2)
    assert first["total"] == 25 and len(second["results"]) == 5
    assert not {r["id"] for r in first["results"]} & {r["id"] for r in second["results"]}
    corpus(tmp_path, [article("new", url="javascript:alert(1)")])
    assert repo.search("금리")["results"][0]["id"] == "new"
    assert repo.search("금리")["results"][0]["url"] == ""
    monkeypatch.setattr(search, "today", lambda: date(2026, 11, 1))
    assert repo.search("")["total"] == 0
    assert repo.search("")["available_documents"] == 0


def test_failed_read_keeps_last_good_and_exposes_original_update_time(tmp_path):
    repo = corpus(tmp_path, [article()])
    previous = repo.search("일본")
    (tmp_path / "news.json").write_text("broken", encoding="utf-8")
    assert repo.search("일본") == previous


def test_search_routes_validation_empty_state_and_markup(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
    client = TestClient(server.build_app())
    empty = client.get("/api/search", params={"q": "일본 금리"})
    assert empty.status_code == 200 and empty.json()["available_documents"] == 0
    assert empty.headers["cache-control"] == "no-store"
    for params in ({"q": "x" * 201}, {"days": 31}, {"q": "최근 300일"}, {"market": "unknown"},
                   {"page": 0}, {"q": "2026-02-31 뉴스"}):
        assert client.get("/api/search", params=params).status_code == 422
    assert client.post("/api/search").status_code == 405
    body = client.get("/search").text
    assert "뉴스·시장 검색" in body
    assert "textContent=row.title" in body
    assert "textContent=row.text" in body
    assert "AbortController" in body
    # 첫 화면(시장)에는 검색 바를 두지 않는다(2026-09-24 운영자 결정).
    assert "action='/search'" not in client.get("/").text


def test_server_reads_the_shared_public_folder():
    from services.web.core.config import PUBLIC_DIR, STORAGE_DIR

    assert server.PUBLIC_DIR == PUBLIC_DIR == STORAGE_DIR / "public"
