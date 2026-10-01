"""뉴스레터: 계정별 구독·확인 코드, 공개 산출물로 만드는 다이제스트, 발송 one-shot.

SMTP는 전부 가짜로 바꾼다. 지키는 규칙은 `code_guide.md`의 「개인 화면」 표의 뉴스레터 줄이다.
"""

import json
import re
import smtplib
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from services.web import server
from services.web.accounts import COOKIE, Accounts, account_lock
from services.web.core.clock import JST
from services.web.newsletter import routes as routes_module
from services.web.newsletter.digest import build_digest, render
from services.web.newsletter.send import run
from services.web.newsletter.subscription import SubscriptionStore, normalize_email, unsubscribe_url

NOW = datetime(2026, 9, 30, 8, 30, tzinfo=JST)
KEY = "k" * 32


class FakeMailer:
    sender = "news@nunchi.live"

    def __init__(self, configured=True):
        self.configured = configured
        self.sent = []
        self.reject = set()
        self.connect_error = None

    @contextmanager
    def session(self):
        if self.connect_error:
            raise self.connect_error
        yield self._send

    def _send(self, message):
        if message["To"] in self.reject:
            raise smtplib.SMTPRecipientsRefused({message["To"]: (550, b"no such user")})
        self.sent.append(message)

    def send(self, message):
        with self.session() as send:
            send(message)


def _code(message) -> str:
    return re.search(r"확인 코드: (\d{6})", message.get_content()).group(1)


@pytest.fixture
def env(tmp_path, monkeypatch):
    public = tmp_path / "public"
    public.mkdir()
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    clock = {"now": NOW}
    monkeypatch.setattr(routes_module, "now", lambda: clock["now"])
    mailer = FakeMailer()
    monkeypatch.setattr(routes_module, "build_mailer", lambda: mailer)
    accounts = Accounts(tmp_path / "users", client_id="client", client_secret="secret",
                        identity_key=KEY, origin="https://testserver")
    app = server.build_app(accounts=accounts)
    clients = []
    for subject in ("subject-a", "subject-b"):
        client = TestClient(app, base_url="https://testserver", headers={"origin": "https://testserver"})
        client.cookies.set(COOKIE, accounts.issue(subject))
        clients.append(client)
    return {"a": clients[0], "b": clients[1], "mailer": mailer, "clock": clock, "users": tmp_path / "users",
            "app": app}


# ── 구독 API ────────────────────────────────────────────────────────────────

def test_subscribe_confirm_and_unsubscribe(env):
    a, b, mailer = env["a"], env["b"], env["mailer"]
    assert a.get("/api/account/newsletter").json()["status"] == "none"

    body = a.put("/api/account/newsletter", json={"email": "Me@Example.COM"}).json()
    assert body["status"] == "pending" and body["email"] == "Me@example.com" and body["codes_today"] == 1
    assert mailer.sent[-1]["To"] == "Me@example.com"
    code = _code(mailer.sent[-1])
    stored = json.loads(next(env["users"].glob("*/newsletter.json")).read_text(encoding="utf-8"))
    assert code not in json.dumps(stored)  # 코드 원문은 저장하지 않는다

    wrong = "000000" if code != "000000" else "111111"
    response = a.post("/api/account/newsletter/confirm", json={"code": wrong})
    assert response.status_code == 422 and "4번" in response.json()["detail"]
    assert b.post("/api/account/newsletter/confirm", json={"code": code}).status_code == 404

    assert a.post("/api/account/newsletter/confirm", json={"code": code}).json()["status"] == "active"
    assert a.get("/api/account/newsletter").json()["status"] == "active"
    assert b.get("/api/account/newsletter").json()["status"] == "none"
    assert "newsletter.json" in a.get("/api/account/export").json()
    assert "no-store" in a.get("/api/account/newsletter").headers["cache-control"]

    assert a.delete("/api/account/newsletter").status_code == 204
    assert a.get("/api/account/newsletter").json()["status"] == "none"
    assert not list(env["users"].glob("*/newsletter.json"))


def test_changing_address_pauses_until_confirmed(env):
    a, mailer = env["a"], env["mailer"]
    a.put("/api/account/newsletter", json={"email": "one@example.com"})
    a.post("/api/account/newsletter/confirm", json={"code": _code(mailer.sent[-1])})
    body = a.put("/api/account/newsletter", json={"email": "two@example.com"}).json()
    assert body["status"] == "pending" and body["email"] == "two@example.com"


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "me@example.com\r\nBcc: x@y.com", "x" * 250 + "@a.io"])
def test_rejects_malformed_and_header_injection(env, email):
    assert env["a"].put("/api/account/newsletter", json={"email": email}).status_code == 422
    assert env["mailer"].sent == []


def test_code_sends_are_capped_per_day(env):
    a = env["a"]
    for _ in range(5):
        assert a.put("/api/account/newsletter", json={"email": "me@example.com"}).status_code == 200
    assert a.put("/api/account/newsletter", json={"email": "me@example.com"}).status_code == 429
    env["clock"]["now"] = NOW + timedelta(days=1)
    assert a.put("/api/account/newsletter", json={"email": "me@example.com"}).json()["codes_today"] == 1


def test_attempts_and_expiry_end_the_code(env):
    a, mailer = env["a"], env["mailer"]
    a.put("/api/account/newsletter", json={"email": "me@example.com"})
    code = _code(mailer.sent[-1])
    wrong = "000000" if code != "000000" else "111111"
    statuses = [a.post("/api/account/newsletter/confirm", json={"code": wrong}).status_code for _ in range(5)]
    assert statuses == [422, 422, 422, 422, 410]
    assert a.post("/api/account/newsletter/confirm", json={"code": code}).status_code == 410

    a.put("/api/account/newsletter", json={"email": "me@example.com"})
    env["clock"]["now"] = NOW + timedelta(minutes=11)
    assert a.post("/api/account/newsletter/confirm", json={"code": _code(mailer.sent[-1])}).status_code == 410


def test_unconfigured_smtp_and_send_failure(env):
    a, mailer = env["a"], env["mailer"]
    mailer.connect_error = smtplib.SMTPConnectError(421, "busy")
    assert a.put("/api/account/newsletter", json={"email": "me@example.com"}).status_code == 502
    # 실패한 발송도 하루 상한에 센다.
    assert a.get("/api/account/newsletter").json()["codes_today"] == 1

    mailer.configured = False
    assert a.get("/api/account/newsletter").json()["configured"] is False
    assert a.put("/api/account/newsletter", json={"email": "me@example.com"}).status_code == 503


def test_requires_login_and_same_origin(env):
    client = TestClient(env["a"].app, base_url="https://testserver")
    assert client.get("/api/account/newsletter").status_code == 401
    a = env["a"]
    response = a.put("/api/account/newsletter", json={"email": "me@example.com"},
                     headers={"origin": "https://evil.example"})
    assert response.status_code == 403


class FakeSMTP:
    calls: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls.append(("connect", type(self).__name__, port, context is not None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        self.calls.append(("send", message["To"], bool(message["Date"])))


class FakeSMTPSSL(FakeSMTP):
    pass


def test_mailer_always_uses_tls(monkeypatch):
    from services.web.newsletter import mailer as mailer_module
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer_module.smtplib, "SMTP_SSL", FakeSMTPSSL)
    for port in (587, 465):
        FakeSMTP.calls = []
        mailer = mailer_module.Mailer("smtp.example.com", port, "user", "pw", "news@nunchi.live", 5)
        mailer.send(mailer_module.compose(mailer, "me@example.com", "제목", "본문"))
        if port == 465:
            assert FakeSMTP.calls[0] == ("connect", "FakeSMTPSSL", 465, True)
            assert not any(call[0] == "starttls" for call in FakeSMTP.calls)
        else:
            assert FakeSMTP.calls[:2] == [("connect", "FakeSMTP", 587, False), ("starttls", True)]
        assert FakeSMTP.calls[-2:] == [("login", "user"), ("send", "me@example.com", True)]


def test_normalize_email_lowercases_domain_only():
    assert normalize_email("  Kim.Lee+news@Mail.Example.KR ") == "Kim.Lee+news@mail.example.kr"


# ── 다이제스트 ───────────────────────────────────────────────────────────────

def _public(public, *, market_at=NOW - timedelta(hours=1)):
    (public / "market.json").write_text(json.dumps({
        "generated_at": market_at.isoformat(),
        "markets": {
            "US": {"avg_sentiment": 0.1, "count": 40, "daily": [
                {"date": "2026-09-29", "avg_sentiment": 0.05, "count": 20, "summary": "어제"},
                {"date": "2026-09-30", "avg_sentiment": 0.31, "count": 20, "summary": "금리 <b>인하</b> 기대"},
            ]},
            "EU": {"avg_sentiment": -0.2, "count": 30, "daily": [
                {"date": "2026-09-30", "avg_sentiment": -0.22, "count": 30, "summary": "ECB 경계"},
            ]},
        },
    }, ensure_ascii=False), encoding="utf-8")
    (public / "news.json").write_text(json.dumps({"documents": [
        {"id": "r2", "kind": "report", "market": "KR", "title": "한국 시장상황 보고서 · 새것",
         "text": "첫 문단\n둘째 문단", "date": "2026-09-30", "published_at": "2026-09-30T08:00:00+09:00"},
        {"id": "r1", "kind": "report", "market": "KR", "title": "한국 시장상황 보고서 · 옛것",
         "text": "옛 판단", "date": "2026-09-30", "published_at": "2026-09-30T04:00:00+09:00"},
        {"id": "r0", "kind": "report", "market": "JP", "title": "일본 오래된 보고서",
         "text": "이틀 전", "date": "2026-09-28", "published_at": "2026-09-28T08:00:00+09:00"},
        {"id": "n1", "kind": "news", "market": "KR", "title": "코스피 <반등>", "date": "2026-09-30",
         "source": "연합", "url": "https://example.com/a?x=1&y=2"},
        {"id": "n2", "kind": "news", "market": "KR", "title": "링크 없음", "date": "2026-09-30",
         "source": "매경", "url": "javascript:alert(1)"},
    ]}, ensure_ascii=False), encoding="utf-8")


def _digest(public, moment=NOW):
    return build_digest(public, moment, window_hours=24, max_market_age_hours=36, headlines=3)


def test_digest_orders_markets_and_escapes(tmp_path):
    _public(tmp_path)
    digest = _digest(tmp_path)
    assert digest["subject"] == "[눈치] 9월 30일 시장 요약"
    link = "https://nunchi.live/newsletter/unsubscribe?a=" + "a" * 64 + "&t=" + "b" * 64
    text, html = render(digest, site="https://nunchi.live", unsubscribe_url=link)
    # 시장 순서는 검색·화면과 같은 MARKETS 순서다. 자료 없는 시장(CN·HK·JP)은 빠진다.
    assert text.index("■ 미국") < text.index("■ 한국") < text.index("■ 유럽")
    assert "■ 일본" not in text and "이틀 전" not in text
    assert "새것" in text and "옛것" not in text  # 최신 보고서 한 편만
    assert "논조 +0.31" in text and "논조 -0.22" in text  # 마지막 날의 값
    assert "<b>인하</b>" not in html and "&lt;b&gt;인하" in html
    assert "코스피 &lt;반등&gt;" in html and "href='https://example.com/a?x=1&amp;y=2'" in html
    assert "javascript:" not in html
    assert "#b42331" in html and "#1f57b0" in html  # 빨강이 긍정, 파랑이 부정
    assert link in text and link.replace("&", "&amp;") in html


def test_digest_skips_stale_or_missing_data(tmp_path):
    assert _digest(tmp_path) is None
    _public(tmp_path, market_at=NOW - timedelta(hours=40))
    (tmp_path / "news.json").write_text('{"documents": []}', encoding="utf-8")
    assert _digest(tmp_path) is None


# ── 발송 one-shot ────────────────────────────────────────────────────────────

def _subscriber(users, key, email, *, status="active", last_sent_on=None):
    SubscriptionStore(users / key / "newsletter.json").write({
        "email": email, "status": status, "code": None, "codes_sent": [],
        "confirmed_at": None, "last_sent_on": last_sent_on, "updated_at": NOW.isoformat(),
    })


def test_send_once_per_day_to_active_subscribers(tmp_path):
    public, users = tmp_path / "public", tmp_path / "users"
    public.mkdir()
    _public(public)
    _subscriber(users, "a" * 64, "a@example.com")
    _subscriber(users, "b" * 64, "b@example.com")
    _subscriber(users, "c" * 64, "c@example.com", status="pending")
    _subscriber(users, "d" * 64, "d@example.com", last_sent_on="2026-09-30")
    (users / ".locks").mkdir()
    mailer = FakeMailer()

    counts = run(users_dir=users, public_dir=public, identity_key=KEY, mailer=mailer, moment=NOW)
    assert counts == {"sent": 2, "skipped": 2, "failed": 0, "busy": 0, "result": "done"}
    assert sorted(m["To"] for m in mailer.sent) == ["a@example.com", "b@example.com"]
    first = next(m for m in mailer.sent if m["To"] == "a@example.com")
    link = unsubscribe_url("https://nunchi.live", KEY, "a" * 64, "a@example.com")
    assert first["List-Unsubscribe"] == f"<{link}>"
    assert first["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # 받는 사람마다 자기 링크다.
    assert link in first.get_body(("plain",)).get_content()
    other = next(m for m in mailer.sent if m["To"] == "b@example.com")
    assert link not in other.get_body(("plain",)).get_content()
    assert SubscriptionStore(users / ("a" * 64) / "newsletter.json").read()["last_sent_on"] == "2026-09-30"

    again = run(users_dir=users, public_dir=public, identity_key=KEY, mailer=mailer, moment=NOW)
    assert again["sent"] == 0 and len(mailer.sent) == 2


def test_send_counts_refused_busy_and_session_failures(tmp_path):
    public, users = tmp_path / "public", tmp_path / "users"
    public.mkdir()
    _public(public)
    _subscriber(users, "a" * 64, "a@example.com")
    _subscriber(users, "b" * 64, "b@example.com")
    mailer = FakeMailer()
    mailer.reject.add("a@example.com")
    with account_lock(users, "b" * 64, timeout=1):
        counts = run(users_dir=users, public_dir=public, identity_key=KEY, mailer=mailer, moment=NOW, lock_timeout=0.1)
    assert (counts["failed"], counts["busy"], counts["sent"]) == (1, 1, 0)
    assert SubscriptionStore(users / ("a" * 64) / "newsletter.json").read()["last_sent_on"] is None

    mailer.connect_error = smtplib.SMTPConnectError(421, "down")
    counts = run(users_dir=users, public_dir=public, identity_key=KEY, mailer=mailer, moment=NOW)
    assert counts["failed"] == 2 and counts["sent"] == 0


def test_send_without_smtp_or_fresh_data(tmp_path):
    public, users = tmp_path / "public", tmp_path / "users"
    public.mkdir()
    assert run(users_dir=users, public_dir=public, identity_key=KEY, mailer=FakeMailer(configured=False),
               moment=NOW)["result"] == "done"  # 구독자가 없으면 설정이 없어도 조용히 끝난다
    _subscriber(users, "a" * 64, "a@example.com")
    assert run(users_dir=users, public_dir=public, identity_key=KEY, mailer=FakeMailer(configured=False),
               moment=NOW)["result"] == "not_configured"
    mailer = FakeMailer()
    assert run(users_dir=users, public_dir=public, identity_key=KEY, mailer=mailer, moment=NOW)["result"] == "no_fresh_data"
    assert mailer.sent == []


def test_send_refuses_without_signing_key(tmp_path):
    public, users = tmp_path / "public", tmp_path / "users"
    public.mkdir()
    _public(public)
    _subscriber(users, "a" * 64, "a@example.com")
    mailer = FakeMailer()
    counts = run(users_dir=users, public_dir=public, identity_key="", mailer=mailer, moment=NOW)
    assert counts["result"] == "not_configured" and mailer.sent == []


# ── 편지 안의 원클릭 해지 ───────────────────────────────────────────────────

def _active(env, email="me@example.com"):
    a, mailer = env["a"], env["mailer"]
    a.put("/api/account/newsletter", json={"email": email})
    a.post("/api/account/newsletter/confirm", json={"code": _code(mailer.sent[-1])})
    folder = next(env["users"].glob("*/newsletter.json")).parent
    return folder.name, unsubscribe_url("https://testserver", KEY, folder.name, email).split("testserver", 1)[1]


def test_one_click_unsubscribe_needs_post_and_valid_signature(env):
    key, path = _active(env)
    anonymous = TestClient(env["app"], base_url="https://testserver")
    # 여는 것만으로는 끄지 않는다(메일 보안 검사기의 미리 열기).
    page = anonymous.get(path)
    assert page.status_code == 200 and "method='post'" in page.text
    assert "no-store" in page.headers["cache-control"] and "noindex" in page.headers["x-robots-tag"]
    assert "script-src 'sha256-" in page.headers["content-security-policy"]
    assert env["a"].get("/api/account/newsletter").json()["status"] == "active"

    forged = path[:-4] + ("0000" if not path.endswith("0000") else "1111")
    assert anonymous.post(forged).status_code == 400
    assert anonymous.get(f"/newsletter/unsubscribe?a=../../etc&t={'0' * 64}").status_code == 400
    assert env["a"].get("/api/account/newsletter").json()["status"] == "active"

    # 메일 서비스의 원클릭 해지: 다른 출처, Origin 없음, 본문 List-Unsubscribe=One-Click.
    response = anonymous.post(path, data={"List-Unsubscribe": "One-Click"},
                              headers={"origin": "https://mail.google.com"})
    assert response.status_code == 200 and "껐습니다" in response.text
    assert env["a"].get("/api/account/newsletter").json()["status"] == "none"
    # 같은 해지가 다시 와도 같은 결과다.
    assert anonymous.post(path).status_code == 200


def test_old_link_cannot_cancel_new_address(env):
    _, old_path = _active(env, "old@example.com")
    env["a"].delete("/api/account/newsletter")
    _active(env, "new@example.com")
    anonymous = TestClient(env["app"], base_url="https://testserver")
    assert anonymous.post(old_path).status_code == 400
    assert env["a"].get("/api/account/newsletter").json()["email"] == "new@example.com"
