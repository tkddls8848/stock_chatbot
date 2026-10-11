"""봇이 공유 저장소(`storage/public/`)에 쓰는 공개 산출물.

웹은 이 파일들을 자기 코드로 읽기만 한다(`services/web/tests/test_webpub.py`가 같은
형식을 직접 써서 읽는 쪽을 검사한다). 형식을 바꾸면 두 테스트를 같은 커밋에서 고친다.
"""

import json
from datetime import datetime

import pytest

from services.telegram_bot import publish
from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.config import PUBLIC_DIR, STORAGE_DIR
from services.telegram_bot.publish import RESEARCH_JSON as PRIVATE_RESEARCH_PATH


@pytest.fixture(autouse=True)
def public(tmp_path, monkeypatch):
    monkeypatch.setattr(publish, "now", lambda: datetime(2026, 9, 23, 12, tzinfo=JST))
    for name, file in (("MARKET_JSON", "market.json"), ("MARKET_CHART", "market_chart.png"),
                       ("RESEARCH_JSON", "research.json"), ("NEWS_JSON", "news.json"),
                       ("META_JSON", "meta.json"), ("WATCH_NEWS_JSON", "watch_news.json")):
        monkeypatch.setattr(publish, name, tmp_path / file)
    return tmp_path


def article(key="jp", **changes):
    return {
        "id": key, "kind": "news", "market": "JP", "date": "2026-09-22",
        "published_at": "2026-09-22T15:00:00+09:00", "title": "BOJ interest rate hike lifts yen",
        "text": "Bank of Japan considers higher interest rates", "sentiment": -0.3,
        "source": "와이어", "url": "https://example.com/news", **changes,
    }


def test_public_files_live_in_the_shared_storage():
    assert PUBLIC_DIR == STORAGE_DIR / "public"
    assert PRIVATE_RESEARCH_PATH == STORAGE_DIR / "bot" / "research" / "snapshot.json"


def test_market_and_research_share_one_meta_file(public):
    publish.publish_market(b"png", {"KR": {"avg_sentiment": 0.2, "count": 12, "daily": []}}, 7)
    publish.publish_research("반도체", {"summary": "요약"}, [{"summary": "이전"}])
    assert (public / "market_chart.png").read_bytes() == b"png"
    market = json.loads((public / "market.json").read_text(encoding="utf-8"))
    assert market["lookback_days"] == 7 and market["markets"]["KR"]["count"] == 12
    research = json.loads((public / "research.json").read_text(encoding="utf-8"))
    assert research["sight"] == "반도체" and research["history"] == [{"summary": "이전"}]
    meta = json.loads((public / "meta.json").read_text(encoding="utf-8"))
    assert {"market_generated_at", "research_generated_at"} <= set(meta)


def test_news_keeps_only_public_fields_deduplicates_and_expires(public):
    publish.publish_news([article(chat_id="SECRET"), article("old", date="2026-08-01")])
    publish.publish_news([article(title="새 제목")])
    payload = json.loads((public / "news.json").read_text(encoding="utf-8"))
    assert len(payload["documents"]) == 1
    assert payload["documents"][0]["title"] == "새 제목"
    assert "SECRET" not in json.dumps(payload)


def test_news_drops_already_published_non_articles_on_the_next_publish(public):
    """규칙이 늘면 그 전에 공개된 광고·시세 화면도 다음 발행 때 빠진다. 원문 제목을 남기지 않던
    예전 근거(`text` 빈 문자열)와 보고서 본문은 판정하지 않는다."""
    old = [article("ad", text="3 European Defense Stocks Investors Are Watching As NATO Risk Returns"),
           article("quote", text="NVIDIA Corporation (NVDA) stock price, news, quote and history"),
           article("legacy", text=""),
           article("report", kind="report", text="3 Bank Stocks That Could Gain From Higher Long Term US Rates")]
    (public / "news.json").write_text(json.dumps({"documents": old}), encoding="utf-8")

    publish.publish_news([article()])

    payload = json.loads((public / "news.json").read_text(encoding="utf-8"))
    assert sorted(row["id"] for row in payload["documents"]) == ["jp", "legacy", "report"]


def test_news_failure_preserves_existing_artifact(public, monkeypatch):
    publish.publish_news([article()])
    before = (public / "news.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("full disk")

    monkeypatch.setattr(publish, "write_json_atomic", fail)
    with pytest.raises(OSError):
        publish.publish_news([article("new")])
    assert (public / "news.json").read_bytes() == before


def queued(article_id, title="Samsung Electronics beats estimates", published="Wed, 23 Sep 2026 01:00:00 GMT", **changes):
    return {"article_id": article_id, "event_id": "e" + article_id, "source": "gnews_us", "label": "미국 증시 뉴스",
            "market": "US", "title": title, "publisher": "Reuters", "url": "https://example.com/" + article_id,
            "published_at": published, "published_date": "", "prefilter_candidate_id": "c",
            "prefilter_exploration": False, **changes}


def test_watch_news_rolls_queued_articles_for_72_hours(public):
    publish.publish_watch_news([queued("a"), queued("old", published="Sat, 19 Sep 2026 00:00:00 GMT"),
                                queued("junk", title="NVIDIA Corporation (NVDA) Stock Price, News, Quote & History"),
                                queued("notime", published="")])
    # 보고서가 발행해 큐가 비어도 이미 담은 기사는 남는다. 같은 기사는 한 번만이다.
    assert publish.publish_watch_news([]) == 1
    assert publish.publish_watch_news([queued("a"), queued("b", publisher="", published="Wed, 23 Sep 2026 02:00:00 GMT")]) == 2
    payload = json.loads((public / "watch_news.json").read_text(encoding="utf-8"))
    assert payload["format"] == publish.WATCH_NEWS_FORMAT and payload["hours"] == 72
    assert [row["id"] for row in payload["items"]] == ["b", "a"]  # 최신순
    first = payload["items"][1]
    assert first == {"id": "a", "market": "US", "title": "Samsung Electronics beats estimates", "source": "Reuters",
                     "url": "https://example.com/a", "published_at": "2026-09-23T10:00:00+09:00"}
    assert payload["items"][0]["source"] == "미국 증시 뉴스"  # 매체명이 없으면 수집 경로 이름
