"""OIDC callback binding, revocation and cross-account private-data isolation."""
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from services.web import accounts as auth, server
from services.web.accounts import Accounts, COOKIE
from services.web.core.clock import now
from services.web.core.storage import file_lock


@pytest.fixture
def setup(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    accounts = Accounts(tmp_path / "users", client_id="client", client_secret="secret",
                        identity_key="k" * 32, origin="https://testserver")
    app = server.build_app(accounts=accounts)
    a = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
    b = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
    a.cookies.set(COOKIE, accounts.issue("subject-a"))
    b.cookies.set(COOKIE, accounts.issue("subject-b"))
    return accounts, a, b, public


def start_flow(client):
    response = client.get("/auth/google?next=/research", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["scope"] == ["openid"]
    assert query["code_challenge_method"] == ["S256"]
    assert "email" not in query and "profile" not in query
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    return query


def test_callback_is_browser_bound_one_time_and_rotates_session(setup, monkeypatch):
    accounts, a, b, _ = setup
    old_token = a.cookies.get(COOKIE)
    query = start_flow(a)
    def exchange(code, flow):
        assert code == "code" and flow["nonce"] == query["nonce"][0]
        challenge = auth.base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest()).decode().rstrip("=")
        assert challenge == query["code_challenge"][0]
        return "subject-a"
    monkeypatch.setattr(accounts, "exchange", exchange)
    params = {"code": "code", "state": query["state"][0]}
    assert b.get("/auth/google/callback", params=params).status_code == 400
    response = a.get("/auth/google/callback", params=params, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/research"
    assert "no-store" in response.headers["cache-control"]
    assert "secure" in response.headers["set-cookie"].lower()
    assert a.get("/auth/google/callback", params=params).status_code == 400
    assert hashlib.sha256(old_token.encode()).hexdigest() not in accounts._sessions
    assert "subject-a" not in response.text


def test_oidc_checks_nonce_and_authorized_party(setup, monkeypatch):
    from google.oauth2 import id_token
    accounts, _, _, _ = setup
    class Result:
        def raise_for_status(self):
            pass
        def json(self):
            return {"id_token": "signed-id-token"}
    def verify(token, request, audience):
        assert token == "signed-id-token" and audience == "client"
        return {"sub": "abc", "nonce": "nonce", "azp": "client"}
    monkeypatch.setattr(auth.requests, "post", lambda *a, **k: Result())
    monkeypatch.setattr(id_token, "verify_oauth2_token", verify)
    assert accounts.exchange("code", {"nonce": "nonce", "verifier": "pkce"}) == "abc"
    with pytest.raises(ValueError, match="nonce"):
        accounts.exchange("code", {"nonce": "wrong", "verifier": "pkce"})
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda *a: {"sub": "abc", "nonce": "nonce", "azp": "attacker"})
    with pytest.raises(ValueError, match="authorized"):
        accounts.exchange("code", {"nonce": "nonce", "verifier": "pkce"})


def test_expired_flow_and_session_rejected(setup, monkeypatch):
    _, a, _, _ = setup
    query = start_flow(a)
    future = now() + timedelta(hours=13)
    monkeypatch.setattr(auth, "now", lambda: future)
    assert a.get("/api/portfolio/assets").status_code == 401
    assert a.get("/auth/google/callback", params={"state": query["state"][0], "code": "code"}).status_code == 400


def test_external_redirect_and_forged_callback_fail_closed(setup):
    accounts, a, _, _ = setup
    response = a.get("/auth/google?next=https://evil.example", follow_redirects=False)
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert accounts._flows[query["state"][0]]["next"] == "/portfolio"
    assert a.get("/auth/google/callback?state=forged&code=stolen").status_code == 400
    assert a.get("/api/portfolio/assets", headers={"cookie": COOKIE + "=forged"}).status_code == 401


def test_invalid_google_token_never_issues_service_session(setup, monkeypatch):
    accounts, a, _, _ = setup
    a.cookies.clear()
    query = start_flow(a)
    def invalid(*args):
        raise ValueError("provider token with sensitive data")
    monkeypatch.setattr(accounts, "exchange", invalid)
    response = a.get("/auth/google/callback", params={"state": query["state"][0], "code": "code"})
    assert response.status_code == 401
    assert "sensitive" not in response.text
    assert a.get("/api/account/session").json()["unlocked"] is False


def test_assets_watchlist_research_and_exports_are_isolated(setup):
    accounts, a, b, public = setup
    (public / "research.json").write_text('{"secret":"operator research"}', encoding="utf-8")
    row = a.post("/api/portfolio/assets", json={"kind": "stock", "name": "private-a", "value_krw": 100}).json()
    assert b.get("/api/portfolio/assets").json() == {"assets": []}
    assert b.put("/api/portfolio/assets/" + row["id"], json={"kind": "stock", "name": "stolen", "value_krw": 1}).status_code == 404
    assert b.delete("/api/portfolio/assets/" + row["id"]).status_code == 404
    assert a.put("/api/portfolio/watchlist", json={"items": [{"code": "KR:KOSPI:005930", "name": "삼성전자"}]}).status_code == 200
    assert b.get("/api/portfolio/watchlist").json() == {"items": {}}
    assert a.put("/api/research/profile", json={"topic": "반도체"}).status_code == 200
    assert b.get("/api/research").json()["profile"]["topic"] == ""
    assert "operator research" not in a.get("/api/research").text
    assert "private-a" not in b.get("/api/account/export").text
    export = a.get("/api/account/export")
    assert "private-a" in export.text and "subject-a" not in export.text
    assert "attachment" in export.headers["content-disposition"]
    assert all("subject-" not in str(path) for path in accounts.root.rglob("*"))


def test_csrf_requires_exact_origin_for_all_mutations(setup):
    _, a, _, _ = setup
    for url in ("/api/account", "/api/account/session", "/api/portfolio/assets/x"):
        assert a.delete(url, headers={"origin": "https://evil.example"}).status_code == 403
    assert a.post("/api/research/reports", headers={"origin": "null"}).status_code == 403
    assert a.put("/api/research/profile", json={"topic": "bad"}, headers={"origin": "https://testserver.evil.example"}).status_code == 403
    assert a.get("/api/account/session").json()["unlocked"] is True


def test_delete_revokes_all_sessions_without_affecting_other_accounts(setup):
    accounts, a, b, _ = setup
    other_token = accounts.issue("subject-a")
    a.post("/api/portfolio/assets", json={"kind": "stock", "name": "a", "value_krw": 1})
    b.post("/api/portfolio/assets", json={"kind": "stock", "name": "b", "value_krw": 2})
    assert a.delete("/api/account").status_code == 204
    a.cookies.clear()
    a.cookies.set(COOKIE, other_token)
    assert a.get("/api/portfolio/assets").status_code == 401
    assert b.get("/api/portfolio/assets").json()["assets"][0]["name"] == "b"
    a.cookies.set(COOKIE, accounts.issue("subject-a"))
    assert a.get("/api/account/export").json() == {}


def test_delete_waits_for_inflight_mutation_and_logout_revokes_replay(setup):
    accounts, a, _, _ = setup
    token = a.cookies.get(COOKIE)
    key = accounts._sessions[hashlib.sha256(token.encode()).hexdigest()][0]
    with file_lock(accounts.root / ".locks" / (key + ".lock")):
        assert a.delete("/api/account").status_code == 409
    assert a.delete("/api/account/session").status_code == 204
    a.cookies.clear()
    a.cookies.set(COOKIE, token)
    assert a.get("/api/portfolio/assets").status_code == 401


def _inputs(path, *, hours_old=0):
    path.write_text(json.dumps({
        "format": 1, "generated_at": (now() - timedelta(hours=hours_old)).isoformat(timespec="seconds"),
        "news_items": [{"title": "Fed signals higher for longer", "content": "Treasury yields rose", "source": "wire",
                        "market": "US", "url": "https://example.com/fed"}],
        "candidates": [{"code": "US:NASDAQ:NVDA", "name": "NVIDIA", "market": "US", "in_watchlist": False,
                        "matched_news": []}],
        "sector_summary_context": {"sector_top": []},
    }), encoding="utf-8")


class _Analyzer:
    def __init__(self):
        self.calls = []

    def analyze(self, topic, watchlist, news_items, candidates, sector, history):
        self.calls.append({"topic": topic, "watchlist": watchlist, "news": news_items,
                           "candidates": candidates, "sector": sector, "history": history})
        return {"generated_at": now().isoformat(timespec="seconds"), "summary": "금리 상승 경로 요약",
                "actions": [{"ticker": "US:NASDAQ:NVDA", "name": "NVIDIA", "action": "add", "confidence": 0.7,
                             "relevance": 0.8, "reason": "근거", "evidence": []},
                            {"ticker": "KR:KOSPI:005930", "name": "삼성전자", "action": "remove", "confidence": 0.6,
                             "relevance": 0.2, "reason": "약함", "evidence": []}],
                "risks": ["반전 조건"], "view_critique": []}


@pytest.fixture
def research(tmp_path, monkeypatch):
    from services.web.personal_research import build_research_router
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    accounts = Accounts(tmp_path / "users", client_id="client", client_secret="secret",
                        identity_key="k" * 32, origin="https://testserver")
    analyzer = _Analyzer()
    inputs = public / "research_inputs.json"
    pending = []
    # 실제로는 요청이 끝난 뒤 스레드에서 돈다. 테스트는 응답을 받은 뒤 그 일을 이어서 돌린다.
    router = build_research_router(accounts, analyzer_factory=lambda: analyzer, start=pending.append,
                                   inputs_path=inputs)
    app = server.build_app(accounts=accounts, research_router=router)
    class Client(TestClient):
        def post(self, *args, **kwargs):
            response = super().post(*args, **kwargs)
            while pending:
                pending.pop(0)()
            return response

    clients = []
    for subject in ("subject-a", "subject-b"):
        client = Client(app, base_url="https://testserver", headers={"origin": "https://testserver"})
        client.cookies.set(COOKIE, accounts.issue(subject))
        clients.append(client)
    return accounts, clients[0], clients[1], inputs, analyzer


def test_research_runs_the_operator_analysis_only_after_consent(research):
    """운영자 봇 리서치와 같은 분석을 계정 주제로 돈다(2026-10-05). 동의 전에는 실행하지 않는다."""
    accounts, a, b, inputs, analyzer = research
    _inputs(inputs)
    a.put("/api/portfolio/watchlist", json={"items": [{"code": "KR:KOSPI:005930", "name": "삼성전자"}]})
    a.post("/api/portfolio/assets", json={"kind": "stock", "name": "secret holding", "value_krw": 987654321})
    a.put("/api/research/profile", json={"topic": "미국 국채금리가 더 오를 수 있을까?"})
    assert a.post("/api/research/reports").status_code == 403
    assert analyzer.calls == []

    assert a.put("/api/research/consent", json={"agree": True}).json()["consented_at"]
    run = a.post("/api/research/reports")
    assert run.status_code == 202

    call = analyzer.calls[0]
    assert call["topic"] == "미국 국채금리가 더 오를 수 있을까?"
    assert call["watchlist"] == {"KR:KOSPI:005930": "삼성전자"}
    # 관심종목이 후보 맨 앞, 그다음 봇이 구운 후보
    assert [c["code"] for c in call["candidates"]] == ["KR:KOSPI:005930", "US:NASDAQ:NVDA"]
    assert call["candidates"][0]["in_watchlist"] is True
    assert call["news"][0]["title"] == "Fed signals higher for longer"
    sent = json.dumps(call, ensure_ascii=False)
    assert all(secret not in sent for secret in ("secret holding", "987654321", "subject-a"))

    state = a.get("/api/research").json()
    assert state["run"]["status"] == "ok"
    assert state["report"]["result"]["summary"] == "금리 상승 경로 요약"
    assert state["history"][0]["actions"] == [{"ticker": "US:NASDAQ:NVDA", "action": "add"},
                                              {"ticker": "KR:KOSPI:005930", "action": "remove"}]
    assert b.get("/api/research").json()["report"] is None

    # 다음 실행은 이전 분석을 넘긴다. 주제를 바꾸면 비교 기록을 지운다(봇과 같다).
    a.post("/api/research/reports")
    assert len(analyzer.calls[1]["history"]) == 1
    a.put("/api/research/profile", json={"topic": "다른 주제"})
    a.post("/api/research/reports")
    assert analyzer.calls[2]["history"] == []

    # 동의를 철회하면 다시 막힌다.
    a.put("/api/research/consent", json={"agree": False})
    assert a.post("/api/research/reports").status_code == 403


def test_research_suggestions_change_the_watchlist_only_when_applied(research):
    _, a, _, inputs, _ = research
    _inputs(inputs)
    a.put("/api/portfolio/watchlist", json={"items": [{"code": "KR:KOSPI:005930", "name": "삼성전자"}]})
    a.put("/api/research/profile", json={"topic": "반도체"})
    a.put("/api/research/consent", json={"agree": True})
    a.post("/api/research/reports")
    assert a.get("/api/portfolio/watchlist").json()["items"] == {"KR:KOSPI:005930": "삼성전자"}

    assert a.post("/api/research/actions", json={"ticker": "US:NASDAQ:NVDA", "action": "add"}).status_code == 200
    assert a.post("/api/research/actions", json={"ticker": "KR:KOSPI:005930", "action": "remove"}).status_code == 200
    assert a.get("/api/portfolio/watchlist").json()["items"] == {"US:NASDAQ:NVDA": "NVIDIA"}
    assert a.get("/api/research").json()["report"]["applied"] == {"US:NASDAQ:NVDA": "add", "KR:KOSPI:005930": "remove"}
    # 결과에 없는 제안은 적용하지 않는다.
    assert a.post("/api/research/actions", json={"ticker": "US:NASDAQ:AAPL", "action": "add"}).status_code == 422


def test_research_refuses_stale_inputs_and_enforces_daily_limits(research):
    accounts, a, _, inputs, analyzer = research
    a.put("/api/research/profile", json={"topic": "반도체"})
    a.put("/api/research/consent", json={"agree": True})
    assert a.post("/api/research/reports").status_code == 503          # 묶음 없음
    _inputs(inputs, hours_old=13)
    assert a.post("/api/research/reports").status_code == 503          # 오래된 묶음
    assert a.get("/api/research").json()["inputs"]["ready"] is False
    _inputs(inputs)
    for _ in range(10):
        assert a.post("/api/research/reports").status_code == 202
    assert a.post("/api/research/reports").status_code == 429
    assert len(analyzer.calls) == 10


def test_research_server_quota_and_failed_analysis(research):
    accounts, a, _, inputs, analyzer = research
    from services.web.llm.market_view import MarketViewError
    _inputs(inputs)
    a.put("/api/research/profile", json={"topic": "반도체"})
    a.put("/api/research/consent", json={"agree": True})

    def broken(*args):
        raise MarketViewError("invalid analysis JSON")
    analyzer.analyze = broken
    assert a.post("/api/research/reports").status_code == 202
    state = a.get("/api/research").json()
    assert state["run"]["status"] == "failed" and state["report"] is None

    (accounts.root / ".research-usage.json").write_text(json.dumps({"day": now().date().isoformat(), "count": 40}))
    assert a.post("/api/research/reports").status_code == 429


def test_a_stale_running_marker_does_not_block_forever(research):
    accounts, a, _, inputs, _ = research
    from services.web.accounts import account_lock  # noqa: F401  (same lock the worker uses)
    _inputs(inputs)
    a.put("/api/research/profile", json={"topic": "반도체"})
    root = next(path for path in accounts.root.iterdir() if not path.name.startswith("."))
    (root / "research").mkdir(exist_ok=True)
    (root / "research/run.json").write_text(json.dumps({
        "format": 2, "id": "x", "status": "running",
        "started_at": (now() - timedelta(hours=1)).isoformat(timespec="seconds")}), encoding="utf-8")
    assert a.get("/api/research").json()["run"]["status"] == "failed"
    a.put("/api/research/consent", json={"agree": True})
    assert a.post("/api/research/reports").status_code == 202


def test_missing_credentials_never_restore_legacy_password_or_public_research(tmp_path):
    accounts = Accounts(tmp_path, client_id="", client_secret="", identity_key="", origin="https://testserver")
    client = TestClient(server.build_app(accounts=accounts))
    for path in ("/api/portfolio/assets", "/api/research", "/api/account/export", "/auth/google"):
        assert client.get(path).status_code == 503
    assert client.post("/api/portfolio/session", json={"password": "legacy"}).status_code == 405
    assert client.get("/api/account/session").json() == {"configured": False, "unlocked": False}


def test_personal_assets_cannot_be_opted_into_external_ai(setup):
    _, a, _, _ = setup
    assert a.post("/api/portfolio/advice", json={"use_ai": True}).status_code == 422
