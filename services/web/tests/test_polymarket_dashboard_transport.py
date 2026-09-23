"""Gamma 읽기 transport의 프록시 failover와 오류 분류.

옛 `test_polymarket_proxy.py`·`test_polymarket_client.py`가 지키던 계약을
`services.web.polymarket.dashboard.transport`로 옮긴 것이다(services/web/docs/polymarket-dashboard.md
10-3). 지키는 규칙은 둘이다.

**프록시가 죽은 것과 Gamma가 거절한 것을 섞지 않는다.** 프록시에 연결 자체가
안 될 때만 직접 연결로 넘어간다. HTTP 451(지역 차단)처럼 Gamma가 돌려준 응답에
개입해 프록시를 우회하면, 프록시를 붙인 이유가 그 자리에서 사라진다.

**재시도는 재시도해서 달라지는 것에만 한다.** 429·5xx·timeout은 다시 걸면
되지만 4xx는 같은 요청을 몇 번 보내도 같은 답이다.
"""

import pytest
import requests

from services.web.polymarket.dashboard.transport import (
    MAX_ATTEMPTS,
    MAX_RETRY_AFTER_SECONDS,
    JsonEndpoint,
    PolymarketError,
    build_session,
)


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text
        self.content = b"x" * 10

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _endpoint(*responses, sleeps=None):
    session = _Session(*responses)
    endpoint = JsonEndpoint(
        url="https://gamma.example/events/keyset",
        timeout=7,
        session=session,
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
    )
    return endpoint, session


# ── 프록시 세션 ────────────────────────────────────────────────────────────

def test_empty_url_means_no_proxy():
    assert build_session("") is None


def test_proxy_url_is_applied_to_both_schemes():
    session = build_session("http://user:pass@proxy-host:8080")

    assert session.proxies == {
        "http": "http://user:pass@proxy-host:8080",
        "https": "http://user:pass@proxy-host:8080",
    }


def test_proxy_connection_failure_falls_back_to_direct(monkeypatch):
    calls = []
    direct_response = object()

    def request(_session, method, url, **kwargs):
        calls.append((method, url))
        if len(calls) == 1:
            raise requests.exceptions.ProxyError("proxy is down")
        return direct_response

    monkeypatch.setattr(requests.Session, "request", request)
    session = build_session("http://proxy-host:8080")

    assert session.get("https://gamma.example/events/keyset", timeout=7) is direct_response
    assert len(calls) == 2


def test_direct_fallback_is_sticky_for_the_process(monkeypatch):
    sessions = []

    def request(current, _method, _url, **_kwargs):
        sessions.append(current)
        if len(sessions) == 1:
            raise requests.Timeout("proxy timed out")
        return object()

    monkeypatch.setattr(requests.Session, "request", request)
    session = build_session("http://proxy-host:8080")

    session.get("https://gamma.example/events/keyset")
    session.get("https://gamma.example/events/keyset?after_cursor=2")

    # 죽은 프록시를 남은 219 page마다 매번 다시 기다리지 않는다.
    assert sessions[0] is session
    assert sessions[1] is sessions[2] is not session
    assert sessions[1].trust_env is False


def test_http_error_response_does_not_bypass_the_proxy(monkeypatch):
    """451은 Gamma의 답이지 프록시 고장이 아니다. 우회하면 차단된 IP로 되돌아간다."""
    response = object()
    calls = []

    def request(current, _method, _url, **_kwargs):
        calls.append(current)
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    session = build_session("http://proxy-host:8080")

    assert session.get("https://gamma.example/events/keyset") is response
    assert calls == [session]


# ── 오류 분류와 재시도 ─────────────────────────────────────────────────────

def test_successful_json_is_returned_and_counted():
    endpoint, session = _endpoint(_Response(payload={"events": [], "next_cursor": None}))

    assert endpoint.request({"limit": 5}) == {"events": [], "next_cursor": None}
    assert endpoint.metrics.request_count == 1
    assert endpoint.metrics.retry_count == 0
    assert session.calls[0][2] == 7


@pytest.mark.parametrize(
    ("status", "reason"),
    [(429, "rate_limited"), (500, "server_error"), (503, "server_error")],
)
def test_retryable_statuses_are_retried_to_the_attempt_limit(status, reason):
    endpoint, _ = _endpoint(*[_Response(status_code=status) for _ in range(MAX_ATTEMPTS)])

    with pytest.raises(PolymarketError) as error:
        endpoint.request({})

    assert error.value.reason == reason
    assert error.value.retryable is True
    assert endpoint.metrics.request_count == MAX_ATTEMPTS


@pytest.mark.parametrize("status", [400, 404, 451])
def test_client_errors_are_not_retried(status):
    """451(지역 차단)을 세 번 더 걸어 봐야 같은 답이다."""
    endpoint, _ = _endpoint(_Response(status_code=status, text="blocked"))

    with pytest.raises(PolymarketError) as error:
        endpoint.request({})

    assert error.value.reason == "bad_request"
    assert error.value.status_code == status
    assert endpoint.metrics.request_count == 1


def test_a_retry_can_succeed():
    endpoint, _ = _endpoint(_Response(status_code=500), _Response(payload={"events": []}))

    assert endpoint.request({}) == {"events": []}
    assert endpoint.metrics.retry_count == 1


def test_timeout_and_connection_errors_are_retryable():
    endpoint, _ = _endpoint(requests.Timeout("slow"), _Response(payload={"events": []}))
    assert endpoint.request({}) == {"events": []}

    endpoint, _ = _endpoint(
        requests.ConnectionError("reset"), _Response(payload={"events": []})
    )
    assert endpoint.request({}) == {"events": []}


def test_unparseable_body_is_not_retried():
    endpoint, _ = _endpoint(_Response(payload=None))

    with pytest.raises(PolymarketError) as error:
        endpoint.request({})

    assert error.value.reason == "invalid_response"
    assert endpoint.metrics.request_count == 1


def test_retry_after_is_honoured_but_capped():
    """서버가 시킨 대기를 따르되 상한을 둔다. 한 page가 순회 전체를 잡아먹는다."""
    sleeps = []
    endpoint, _ = _endpoint(
        _Response(status_code=429, headers={"Retry-After": "3600"}),
        _Response(payload={"events": []}),
        sleeps=sleeps,
    )

    endpoint.request({})

    assert sleeps == [MAX_RETRY_AFTER_SECONDS]


def test_response_bytes_are_measured_even_on_failure():
    endpoint, _ = _endpoint(_Response(status_code=451, text="blocked"))

    with pytest.raises(PolymarketError):
        endpoint.request({})

    assert endpoint.metrics.response_bytes == 10


def test_error_detail_is_truncated():
    endpoint, _ = _endpoint(_Response(status_code=400, text="y" * 5000))

    with pytest.raises(PolymarketError) as error:
        endpoint.request({})

    assert len(error.value.detail) == 500


def test_endpoint_without_a_session_still_works(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "request", lambda *_a, **_k: _Response(payload={"events": []})
    )
    endpoint = JsonEndpoint(url="https://gamma.example/events/keyset", timeout=7)

    assert endpoint.request({}) == {"events": []}


def test_metrics_start_at_zero():
    endpoint = JsonEndpoint(url="https://gamma.example/events/keyset", timeout=7)

    assert (
        endpoint.metrics.request_count,
        endpoint.metrics.retry_count,
        endpoint.metrics.response_bytes,
    ) == (0, 0, 0)
    assert isinstance(endpoint.session, requests.Session)
