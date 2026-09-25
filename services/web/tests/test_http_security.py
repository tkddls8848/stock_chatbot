"""실제 응답의 CSP와 HEAD·오류·개인 정보 헤더 계약."""

import base64
import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.web import server

SCREENS = ("/", "/forecast", "/research", "/about", "/terms", "/search", "/portfolio")
PUBLIC_API = (
    "/api/market", "/api/research", "/api/meta", "/api/search",
    "/api/forecast/summary", "/api/forecast/categories", "/api/forecast/sector-brief",
    "/api/forecast/trending", "/api/forecast/health", "/api/forecast/events",
    "/api/forecast/events/one",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path)
    (tmp_path / "market_chart.png").write_bytes(b"png")
    (tmp_path / "polymarket").mkdir()
    for name in ("sector_brief", "trending"):
        (tmp_path / "polymarket" / (name + ".json")).write_bytes(b'{"generation_id":"one"}')
    repository = Mock()
    repository.load.return_value = True
    for method in ("summary", "categories", "health", "events", "detail"):
        getattr(repository, method).return_value = {"generation_id": "one"}
    repository.index_version.return_value = "one"
    monkeypatch.setattr(server, "POLYMARKET_REPOSITORY", repository)
    return TestClient(server.build_app(), raise_server_exceptions=False)


def assert_security(response):
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    permissions = response.headers["permissions-policy"]
    for feature in ("camera", "microphone", "geolocation", "payment", "usb"):
        assert feature + "=()" in permissions
    csp = response.headers["content-security-policy"]
    for directive in ("default-src 'none'", "frame-ancestors 'none'", "base-uri 'none'",
                      "form-action 'self'", "object-src 'none'", "connect-src 'self'",
                      "img-src 'self' data:", "style-src-attr 'none'", "script-src-attr 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


@pytest.mark.parametrize("path", (*SCREENS, "/robots.txt", "/favicon.ico",
                                 "/market_chart.png", *PUBLIC_API))
def test_get_and_head_share_status_and_headers_without_body(client, path):
    get = client.get(path)
    head = client.head(path)
    assert get.status_code == head.status_code == 200
    assert head.content == b""
    assert head.headers == get.headers
    assert_security(get)
    assert_security(head)


@pytest.mark.parametrize("path", (*SCREENS, "/missing"))
def test_csp_hashes_match_every_inline_block_exactly(client, path):
    response = client.get(path)
    policy = dict(part.strip().split(" ", 1)
                  for part in response.headers["content-security-policy"].split(";"))
    for tag in ("script", "style"):
        blocks = re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", response.text, re.S)
        expected = {"'sha256-" + base64.b64encode(
            hashlib.sha256(block.encode()).digest()).decode() + "'" for block in blocks}
        assert set(policy[tag + "-src"].split()) == (expected or {"'none'"})


class AttributeParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        for name, _ in attrs:
            assert name != "style" and not name.startswith("on"), (tag, name)


@pytest.mark.parametrize("path", SCREENS)
def test_no_inline_style_or_event_attributes_are_left(client, path):
    html = client.get(path).text
    AttributeParser().feed(html)
    # JavaScript가 innerHTML로 생성하는 막대도 style 속성을 넣지 않는다.
    for script in re.findall(r"<script>(.*?)</script>", html, re.S):
        assert "style=" not in script
        assert ".style.cssText" not in script


@pytest.mark.parametrize("path", ("/missing", "/api/missing", "/portfolio/missing"))
def test_unknown_paths_distinguish_html_and_api_json(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert_security(response)
    if path.startswith("/api/"):
        assert response.json() == {"detail": "Not Found"}
    else:
        assert response.headers["content-type"].startswith("text/html")
        assert "페이지를 찾을 수 없습니다" in response.text
        assert "<nav" in response.text and "<footer" in response.text
        assert "href='/'>홈으로 돌아가기" in response.text
    assert client.head(path).status_code == 404
    assert client.head(path).content == b""


@pytest.mark.parametrize("path", ("/broken", "/api/broken", "/portfolio/broken"))
@pytest.mark.parametrize("explicit", (False, True))
def test_server_errors_have_no_stack_or_exception_detail(client, path, explicit):
    @client.app.api_route(path, methods=["GET", "HEAD"])
    def broken():
        if explicit:
            raise HTTPException(500, "요청을 처리할 수 없습니다")
        raise RuntimeError("secret diagnostic")

    response = client.get(path)
    assert response.status_code == 500
    assert_security(response)
    for hidden in ("Traceback", "RuntimeError", "secret diagnostic", "server.py"):
        assert hidden not in response.text
    if path.startswith("/api/"):
        detail = "요청을 처리할 수 없습니다" if explicit else "Internal Server Error"
        assert response.json() == {"detail": detail}
    else:
        assert "잠시 후 다시 시도해 주세요" in response.text
        assert "<footer" in response.text
    assert client.head(path).status_code == 500
    assert client.head(path).content == b""
    if path.startswith("/portfolio"):
        assert response.headers["cache-control"] == "no-store"


def test_private_headers_and_cookies_survive(client):
    for method in (client.get, client.head):
        response = method("/portfolio")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    @client.app.get("/cookies")
    def cookies():
        response = server.Response()
        response.set_cookie("one", "1", httponly=True, secure=True, samesite="strict")
        response.set_cookie("two", "2")
        return response

    assert len(client.get("/cookies").headers.get_list("set-cookie")) == 2


def test_forecast_error_and_conditional_response_keep_their_contract(client):
    response = client.get("/api/forecast/summary")
    cached = client.get("/api/forecast/summary", headers={"If-None-Match": response.headers["etag"]})
    assert cached.status_code == 304 and cached.content == b""
    assert_security(cached)
    server.POLYMARKET_REPOSITORY.detail.return_value = None
    missing = client.get("/api/forecast/events/missing")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "이 event가 현재 generation에 없습니다."}
    server.POLYMARKET_REPOSITORY.load.return_value = False
    assert client.head("/api/forecast/summary").status_code == 503


def test_headers_are_not_recomputed_on_requests(client, monkeypatch):
    def forbidden(*args):
        raise AssertionError("request-time CSP calculation")

    monkeypatch.setattr(server, "_content_security_policy", forbidden)
    for path in (*SCREENS, "/missing", "/api/missing"):
        assert_security(client.get(path))


def test_caddy_has_transport_security_and_removes_server_header():
    caddy = (Path(__file__).resolve().parents[3] / "infra/Caddyfile.example").read_text("utf-8")
    assert 'header Strict-Transport-Security "max-age=31536000; includeSubDomains"' in caddy
    assert "header -Server" in caddy
