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
    assert a.put("/api/research/profile", json={"topic": "반도체", "days": 7}).status_code == 200
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


def test_research_uses_own_profile_and_public_sources_only(setup):
    _, a, b, public = setup
    today = now().date().isoformat()
    (public / "news.json").write_text(json.dumps({"documents": [
        {"id": "news-1", "kind": "news", "market": "KR", "title": "반도체 투자 확대", "text": "공개 근거",
         "date": today, "published_at": today, "source": "뉴스", "url": "https://example.com/news"},
        {"id": "news-2", "kind": "news", "market": "US", "title": "은행 실적", "text": "은행 근거", "date": today},
    ]}), encoding="utf-8")
    a.put("/api/research/profile", json={"topic": "반도체", "days": 7})
    report = a.post("/api/research/reports")
    assert report.status_code == 201
    assert report.json()["sections"][0]["evidence"][0]["id"] == "news-1"
    assert "은행 근거" not in report.text
    assert b.get("/api/research").json()["report"] is None
    for _ in range(9):
        assert a.post("/api/research/reports").status_code == 201
    assert a.post("/api/research/reports").status_code == 429


def test_missing_credentials_never_restore_legacy_password_or_public_research(tmp_path):
    accounts = Accounts(tmp_path, client_id="", client_secret="", identity_key="", origin="https://testserver")
    client = TestClient(server.build_app(accounts=accounts))
    for path in ("/api/portfolio/assets", "/api/research", "/api/account/export", "/auth/google"):
        assert client.get(path).status_code == 503
    assert client.post("/api/portfolio/session", json={"password": "legacy"}).status_code == 405
    assert client.get("/api/account/session").json() == {"configured": False, "unlocked": False}


def test_optional_ai_receives_only_public_evidence_and_has_shared_quota(setup, monkeypatch):
    from services.web import personal_research
    accounts, a, b, public = setup
    day = now().date().isoformat()
    (public / "news.json").write_text(json.dumps({"documents": [
        {"id": "n1", "kind": "news", "market": "KR", "title": "반도체 투자", "text": "공개 근거",
         "date": day, "source": "뉴스"},
    ]}), encoding="utf-8")
    captured = []
    monkeypatch.setattr(personal_research, "analyze_public_evidence",
                        lambda rows: captured.append(rows) or "공개 근거에 대한 분석 [1]")
    a.put("/api/research/profile", json={"topic": "반도체 관련 뉴스를 찾아줘"})
    a.post("/api/portfolio/assets", json={"kind": "stock", "name": "secret holding", "value_krw": 987654321})
    assert a.post("/api/research/reports").status_code == 201
    assert captured == []
    report = a.post("/api/research/reports", json={"public_evidence_ai": True}).json()
    assert report["analysis_status"] == "ok"
    sent = json.dumps(captured, ensure_ascii=False)
    assert "공개 근거" in sent
    assert all(secret not in sent for secret in ("secret holding", "987654321", "subject-a", "찾아줘"))
    assert b.get("/api/research").json()["report"] is None
    (accounts.root / ".research-ai-usage.json").write_text(json.dumps({"day": day, "count": 20}))
    assert a.post("/api/research/reports", json={"public_evidence_ai": True}).json()["analysis_status"] == "daily_limit"
    assert len(captured) == 1


def test_personal_assets_cannot_be_opted_into_external_ai(setup):
    _, a, _, _ = setup
    assert a.post("/api/portfolio/advice", json={"use_ai": True}).status_code == 422
