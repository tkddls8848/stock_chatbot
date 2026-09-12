"""`/events/keyset` 전수 순회의 종료 조건과 안전장치.

이 파일이 지키는 규칙은 하나다. **끝까지 돌았는지 아닌지를 조용히 넘기지
않는다.** 옛 `/markets/keyset`은 커서를 어떤 이름으로 넘겨도 같은 첫 페이지를
돌려줘 100건에서 멈췄는데, 그게 성공으로 보이면 전수 순회를 표방하는 화면이
일부만 그린 채 "전수 순회 완료"라고 쓴다. 그래서 커서가 전진하지 않거나 같은
페이지가 다시 오면 실패시키고, coverage_status를 순회 결과에서 받아 온다.
"""

import pytest

from web.polymarket.dashboard.client import MAX_PAGES, REQUESTED_PAGE_SIZE, EventsClient


class _Endpoint:
    """EventsClient가 만든 JsonEndpoint를 대신한다."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.requests = []
        self.metrics = type("M", (), {"request_count": 0, "retry_count": 0, "response_bytes": 0})()

    def request(self, params):
        self.requests.append(dict(params))
        self.metrics.request_count += 1
        if not self._pages:
            return {"events": [], "next_cursor": None}
        return self._pages.pop(0)


def _client(pages):
    client = EventsClient(base_url="https://gamma.example", timeout=7)
    client.endpoint = _Endpoint(pages)
    return client


def _events(*ids):
    return [{"id": str(value)} for value in ids]


def test_walk_stops_when_the_cursor_runs_out():
    client = _client([
        {"events": _events(1, 2), "next_cursor": "c1"},
        {"events": _events(3), "next_cursor": None},
    ])

    pages = list(client.walk_pages())

    assert [len(page) for page in pages] == [2, 1]
    assert client.stats.page_count == 2
    assert client.stats.actual_page_sizes == [2, 1]
    assert client.stats.coverage_status == "complete"


def test_the_first_request_carries_no_cursor_and_asks_for_the_full_page():
    client = _client([{"events": _events(1), "next_cursor": "c1"},
                      {"events": _events(2), "next_cursor": None}])

    list(client.walk_pages())

    first, second = client.endpoint.requests
    assert first == {"closed": "false", "limit": REQUESTED_PAGE_SIZE}
    assert second["after_cursor"] == "c1"


def test_a_cursor_that_does_not_advance_fails_the_walk():
    client = _client([
        {"events": _events(1), "next_cursor": "c1"},
        {"events": _events(2), "next_cursor": "c1"},
    ])

    with pytest.raises(RuntimeError, match="cursor did not advance"):
        list(client.walk_pages())

    assert client.stats.coverage_status == "failed"
    assert client.stats.repeated_cursor_count == 1


def test_a_cursor_seen_earlier_fails_the_walk():
    """앞으로 돌아가는 커서는 무한 순회가 된다."""
    client = _client([
        {"events": _events(1), "next_cursor": "c1"},
        {"events": _events(2), "next_cursor": "c2"},
        {"events": _events(3), "next_cursor": "c1"},
    ])

    with pytest.raises(RuntimeError, match="cursor did not advance"):
        list(client.walk_pages())

    assert client.stats.coverage_status == "failed"


def test_an_identical_page_fails_the_walk_even_when_the_cursor_moves():
    """커서만 바뀌고 내용이 같으면 서버가 전진하지 않은 것이다."""
    client = _client([
        {"events": _events(1, 2), "next_cursor": "c1"},
        {"events": _events(1, 2), "next_cursor": "c2"},
    ])

    with pytest.raises(RuntimeError, match="repeated an identical page"):
        list(client.walk_pages())

    assert client.stats.coverage_status == "failed"


def test_repeated_empty_pages_are_not_treated_as_repeats():
    """빈 page의 signature는 비어 있어 서로 같다. 이것으로 실패시키지 않는다."""
    client = _client([
        {"events": [], "next_cursor": "c1"},
        {"events": [], "next_cursor": None},
    ])

    pages = list(client.walk_pages())

    assert pages == [[], []]
    assert client.stats.coverage_status == "complete"


def test_the_page_safety_limit_fails_instead_of_looping_forever():
    endless = [
        {"events": _events(index), "next_cursor": f"c{index}"}
        for index in range(MAX_PAGES + 5)
    ]
    client = _client(endless)

    with pytest.raises(RuntimeError, match="page safety limit"):
        list(client.walk_pages())

    assert client.stats.coverage_status == "failed"
    assert client.stats.page_count == MAX_PAGES


def test_non_dict_records_are_dropped_without_killing_the_page():
    client = _client([{"events": [{"id": "1"}, "junk", None, {"id": "2"}], "next_cursor": None}])

    assert list(client.walk_pages()) == [[{"id": "1"}, {"id": "2"}]]


def test_a_malformed_envelope_yields_an_empty_page():
    client = _client([{"events": "not-a-list", "next_cursor": None}])

    assert list(client.walk_pages()) == [[]]


def test_stats_reset_between_walks():
    client = _client([{"events": _events(1), "next_cursor": None}])
    list(client.walk_pages())
    client.endpoint = _Endpoint([{"events": _events(9), "next_cursor": None}])

    list(client.walk_pages())

    assert client.stats.page_count == 1
    assert client.stats.actual_page_sizes == [1]
