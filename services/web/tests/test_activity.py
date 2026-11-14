"""가입·활동 집계의 중복, 날짜 경계, 삭제, 비공개 파일 계약."""
from datetime import datetime, timedelta
import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from services.web import activity, server
from services.web.accounts import Accounts, COOKIE
from services.web.core.clock import JST


@pytest.fixture
def setup(tmp_path, monkeypatch):
    moment = [datetime(2026, 11, 14, 23, 59, tzinfo=JST)]
    monkeypatch.setattr(activity, "now", lambda: moment[0])
    accounts = Accounts(tmp_path / "users", client_id="c", client_secret="s", identity_key="k" * 32,
                        origin="https://testserver")
    monkeypatch.setattr(server, "PUBLIC_DIR", tmp_path / "public")
    app = server.build_app(accounts=accounts)
    with TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"}) as client:
        yield accounts, app.state.activity, client, moment


def summary(stats):
    stats.flush()
    return json.loads((stats.root / "activity_summary.json").read_text())


def login(accounts, client, subject):
    token = accounts.issue(subject)
    client.cookies.set(COOKIE, token)
    return accounts.root / accounts._sessions[hashlib.sha256(token.encode()).hexdigest()][0]


def test_counts_login_only_accounts_and_deduplicates_across_days(setup):
    accounts, stats, client, moment = setup
    root = login(accounts, client, "one")
    client.get("/")
    client.get("/")
    assert summary(stats)["periods"]["today"] == {"new": 1, "active": 1, "views": 2, "errors": 0}
    login(accounts, client, "one")
    assert summary(stats)["accounts"] == 1
    moment[0] += timedelta(minutes=2)
    client.get("/forecast")
    data = summary(stats)
    assert data["periods"]["today"]["new"] == 0
    assert data["periods"]["today"]["active"] == 1
    assert data["periods"]["yesterday"]["active"] == 1
    assert data["periods"]["7d"]["active"] == 1
    assert str(root.name) not in json.dumps(data)
    assert "one" not in json.dumps(data)


def test_excludes_bots_head_assets_polling_and_never_stores_query(setup):
    _, stats, client, _ = setup
    client.get("/?secret=hidden", headers={"user-agent": "Mozilla/5.0"})
    client.get("/", headers={"user-agent": "Googlebot"})
    client.head("/")
    client.get("/api/account/session")
    client.get("/favicon.ico")
    client.get("/api/search?q=private-query")
    data = summary(stats)
    assert data["periods"]["today"]["views"] == 1
    assert data["periods"]["today"]["active"] == 0
    assert data["days"]["2026-11-14"]["actions"]["news_search"] == 1
    saved = (stats.root / "activity.json").read_text()
    assert "secret" not in saved and "private-query" not in saved and "Mozilla" not in saved
    assert client.get("/api/activity").status_code == 404
    assert client.get("/web/activity_summary.json").status_code == 404


def test_existing_accounts_are_not_invented_new_signups(setup):
    accounts, stats, client, _ = setup
    import hmac
    key = hmac.new(accounts.identity_key.encode(), b"legacy", hashlib.sha256).hexdigest()
    (accounts.root / key).mkdir(parents=True)
    assert summary(stats)["legacy_accounts"] == 1
    login(accounts, client, "legacy")
    assert summary(stats)["legacy_accounts"] == 1
    assert summary(stats)["periods"]["today"]["new"] == 0


def test_delete_removes_activity_and_export_includes_only_own_record(setup):
    accounts, stats, client, _ = setup
    root = login(accounts, client, "one")
    assert "activity.json" in client.get("/api/account/export").json()
    assert client.delete("/api/account").status_code == 204
    assert not root.exists()
    client.get("/portfolio")
    assert not root.exists()
    assert summary(stats)["accounts"] == 0
    assert summary(stats)["periods"]["today"]["active"] == 0


def test_retention_and_restart_keep_counts_without_resurrecting_history(setup):
    accounts, stats, client, moment = setup
    root = login(accounts, client, "one")
    client.get("/")
    summary(stats)
    reopened = activity.Activity(stats.root, accounts.root)
    assert summary(reopened)["periods"]["today"]["views"] == 1
    moment[0] += timedelta(days=31)
    assert summary(reopened)["periods"]["30d"]["active"] == 0
    assert json.loads((root / "activity.json").read_text())["days"] == []
    assert summary(reopened)["days"] == {}


def test_atomic_save_failure_retains_pending_counts(setup, monkeypatch):
    _, stats, client, _ = setup
    client.get("/")
    old = (stats.root / "activity_summary.json").read_text()
    write = activity.write_json_atomic
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(activity, "write_json_atomic", fail)
    with pytest.raises(OSError):
        stats.flush()
    assert (stats.root / "activity_summary.json").read_text() == old
    monkeypatch.setattr(activity, "write_json_atomic", write)
    assert summary(stats)["periods"]["today"]["views"] == 1


def test_errors_are_counted_but_not_successful_actions(setup):
    accounts, stats, client, _ = setup
    from starlette.requests import Request
    request = Request({"type": "http", "method": "POST", "path": "/api/research/reports",
                       "headers": [(b"user-agent", b"Mozilla")], "query_string": b""})
    stats.observe(request, SimpleNamespace(status_code=500), accounts)
    data = summary(stats)["periods"]["today"]
    assert data["errors"] == 1 and data["active"] == 0


def test_corrupt_statistics_do_not_stop_site_or_overwrite_evidence(setup):
    accounts, stats, client, _ = setup
    path = stats.root / "activity.json"
    path.write_text('{"broken":')
    broken = activity.Activity(stats.root, accounts.root)
    with pytest.raises(ValueError):
        broken.flush()
    assert path.read_text() == '{"broken":'
    app = server.build_app(accounts=accounts)
    with TestClient(app) as visitor:
        assert visitor.get("/").status_code == 200
    assert path.read_text() == '{"broken":'
