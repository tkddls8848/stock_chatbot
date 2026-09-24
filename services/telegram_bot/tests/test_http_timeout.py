"""타임아웃 없이 나가는 requests 호출에 기본값이 채워지는가.

`socket.setdefaulttimeout()`로는 막을 수 없었다 — requests가 "타임아웃 없음"을
명시적으로 넘기기 때문이다(서버 실측, core/http_timeout.py).
"""

import requests

from services.telegram_bot.core.http_timeout import install_default_requests_timeout


def _capture(monkeypatch):
    seen = []

    def send(self, request, **kwargs):
        seen.append(kwargs.get("timeout"))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.request = request
        return response

    # 설치가 클래스 속성을 바꾸므로 원래 값을 되돌리게 등록해 둔다.
    monkeypatch.setattr(requests.sessions.Session, "request", requests.sessions.Session.request)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return seen


def test_missing_timeout_gets_the_default(monkeypatch):
    seen = _capture(monkeypatch)
    install_default_requests_timeout((10.0, 30.0))

    requests.get("http://example.invalid/")
    requests.get("http://example.invalid/", timeout=None)

    assert seen == [(10.0, 30.0), (10.0, 30.0)]


def test_explicit_timeout_is_left_alone_and_install_is_idempotent(monkeypatch):
    seen = _capture(monkeypatch)
    install_default_requests_timeout((10.0, 30.0))
    install_default_requests_timeout((1.0, 1.0))

    requests.get("http://example.invalid/", timeout=3)
    requests.get("http://example.invalid/")

    assert seen == [3, (10.0, 30.0)]
