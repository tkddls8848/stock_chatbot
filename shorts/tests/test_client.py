import pytest

from polymarket_shorts import client
from polymarket_shorts.client import PolymarketWebClient, SourceError


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.content = payload if isinstance(payload, bytes) else b""

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.urls = []

    def get(self, url, timeout):
        self.urls.append(url)
        return Response(next(self.payloads))


def test_snapshot_reads_every_page_and_never_sector_brief(issue_source, event_factory, monkeypatch):
    monkeypatch.setattr(client, "PAGE_SIZE", 2)
    summary = issue_source[0].summary
    session = Session([summary,
                       {"generation_id": "g1", "total": 3, "events": [event_factory("1"), event_factory("2")]},
                       {"generation_id": "g1", "total": 3, "events": [event_factory("3")]},
                       {"generation_id": "old", "state": "ok"}])
    snapshot = PolymarketWebClient("https://example.test", session=session).snapshot()
    assert [e["id"] for e in snapshot.events] == ["1", "2", "3"]
    assert snapshot.trending == {}
    assert "page=2" in session.urls[2]
    assert all("sector-brief" not in url for url in session.urls)


@pytest.mark.parametrize("problem", ["generation", "missing", "duplicate", "over_budget"])
def test_incomplete_or_mixed_data_stops(issue_source, event_factory, problem):
    page = {"generation_id": "g1", "total": 2, "events": [event_factory("1"), event_factory("2")]}
    if problem == "generation":
        page["generation_id"] = "other"
    elif problem == "missing":
        page["events"].pop()
    elif problem == "duplicate":
        page["events"][1]["id"] = "1"
    else:
        page["total"] = 10001
    session = Session([issue_source[0].summary, page])
    with pytest.raises(SourceError):
        PolymarketWebClient("https://example.test", session=session).snapshot()


@pytest.mark.parametrize("freshness", ["missing", "delayed", "stale"])
def test_snapshot_rejects_non_fresh_data(issue_source, freshness):
    session = Session([{**issue_source[0].summary, "freshness": {"state": freshness}}])
    with pytest.raises(SourceError, match=freshness):
        PolymarketWebClient("https://example.test", session=session).snapshot()


def test_detail_budget_and_generation_guard():
    api = PolymarketWebClient("https://example.test", session=Session([{"id": "1", "generation_id": "other"}]))
    with pytest.raises(SourceError, match="generation"):
        api.detail("1", "g1")
    api.requests["details"] = 5
    with pytest.raises(SourceError, match="예산"):
        api.detail("2", "g1")


def test_news_keeps_only_recent_non_future_unique_titles():
    def item(title, stamp):
        return f"<item><title>{title}</title><link>https://news.example/a</link><pubDate>{stamp}</pubDate><source>News</source></item>"
    rss = "<rss><channel>" + item("Old", "Tue, 01 Sep 2026 00:00:00 GMT")
    rss += item("Future", "Thu, 24 Sep 2026 00:00:00 GMT")
    rss += item("Current", "Tue, 22 Sep 2026 00:00:00 GMT") * 2 + "</channel></rss>"
    api = PolymarketWebClient("https://example.test", session=Session([rss.encode()]))
    news = api.news("Fed Decision?", reference="2026-09-23T00:00:00+00:00")
    assert len(news) == 1 and news[0]["title"] == "Current"
