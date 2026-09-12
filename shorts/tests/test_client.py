import pytest

from polymarket_shorts.client import PolymarketWebClient, SourceError


class Response:
    def __init__(self, payload):
        self.payload = payload

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


def test_snapshot_reads_existing_web_app_contract():
    session = Session(
        [
            {"generation_id": "g1", "groups": []},
            {"generation_id": "g1", "freshness": {"state": "normal"}},
        ]
    )
    snapshot = PolymarketWebClient("https://example.test", session=session).snapshot()

    assert snapshot.generation_id == "g1"
    assert session.urls == [
        "https://example.test/api/polymarket/sector-brief",
        "https://example.test/api/polymarket/summary",
    ]


def test_snapshot_rejects_mixed_generations():
    session = Session(
        [
            {"generation_id": "old"},
            {"generation_id": "new", "freshness": {"state": "normal"}},
        ]
    )

    with pytest.raises(SourceError, match="generation"):
        PolymarketWebClient("https://example.test", session=session).snapshot()


@pytest.mark.parametrize("freshness", ["missing", "delayed", "stale"])
def test_snapshot_rejects_non_fresh_data(freshness):
    session = Session(
        [
            {"generation_id": "g1"},
            {"generation_id": "g1", "freshness": {"state": freshness}},
        ]
    )

    with pytest.raises(SourceError, match=freshness):
        PolymarketWebClient("https://example.test", session=session).snapshot()

