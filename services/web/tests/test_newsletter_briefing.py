import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from services.web.core.clock import JST
from services.web.core.storage import write_json_atomic
from services.web.llm.backends import TokenUsage
from services.web.newsletter import briefing as b, send
from services.web.newsletter.digest import build_digest, render

NOW = datetime(2026, 11, 16, 8, 30, tzinfo=JST)


@pytest.fixture
def data(tmp_path):
    docs = [{"kind": "news", "market": "US", "id": f"long-{i}", "title": f"경제 소식 {i}",
             "text": "Original", "source": "매체", "url": f"https://example.com/{i}",
             "published_at": NOW.isoformat()} for i in range(10)]
    docs += [{"kind": "report", "market": "US", "title": "시장 보고서", "text": "보고서 전문" * 4000,
              "published_at": NOW.isoformat()},
             {"kind": "news", "market": "KR", "title": "오래된 기사", "published_at": (NOW - timedelta(hours=25)).isoformat()},
             {"kind": "other", "market": "KR", "title": "다른 종류", "published_at": NOW.isoformat()}]
    write_json_atomic(tmp_path / "news.json", {"documents": docs})
    write_json_atomic(tmp_path / "market.json", {"generated_at": NOW.isoformat(), "markets": {"US": {
        "daily": [{"date": "2026-11-16", "avg_sentiment": 0.13, "summary": "금리 1,500달러 25bp"}]}}})
    return b.collect_input(tmp_path, NOW)


def valid():
    sentence = "시장의 정책 변화와 기업 실적에 대한 관심이 이어지고 있으며 확인된 자료를 통해 각 흐름의 차이를 살펴볼 필요가 있다. "
    return {"subject": "오늘 아침 살펴보는 세계 경제의 흐름", "lead": sentence + "서로 다른 시장의 변화가 같은 방향인지 아직 확인이 필요하다.",
            "items": [{"title": title, "body": sentence + tail, "markets": ["US"], "news": ["n1"]}
                      for title, tail in [("금리 정책 변화의 의미", "정책의 구체적 영향은 후속 발표를 통해 확인해야 한다."),
                                          ("기업 실적 변화의 흐름", "기업별 실적 차이가 이어지는지는 앞으로의 자료를 확인해야 한다."),
                                          ("시장 수급 변화의 방향", "수급 변화가 지속되는지는 이후의 흐름을 확인해야 한다.")]],
            "watch": [{"text": "후속 정책 발표에서 기존 방향이 이어지는지 확인할 필요가 있다."}]}


def test_collect_window_ids_and_budget(data):
    assert len(b.encode(data["payload"])) <= 12000
    assert [r["code"] for r in data["payload"]["markets"]] == ["US"]
    assert [n["id"] for n in data["payload"]["markets"][0]["news"]] == [f"n{i}" for i in range(1, 9)]
    assert "https://" not in b.encode(data["payload"])


def test_validation_rejects_unsupported_claims_and_repairs_once(data):
    for key, value in (("number", "9999%"), ("reference", "n999"), ("instruction", "지금 매수하세요")):
        bad = valid()
        if key == "reference":
            bad["items"][0]["news"] = [value]
        else:
            bad["lead"] = value + " " + bad["lead"]
        backend = Mock(last_usage=TokenUsage(neurons=2))
        backend.generate.side_effect = [json.dumps(bad), json.dumps(valid())]
        usage = {}
        assert b.generate(data, backend, usage)
        assert backend.generate.call_count == 2 and usage["neurons"] == 4
        assert "반려 사유" in backend.generate.call_args.kwargs["system_prompt"]
        backend.generate.reset_mock(side_effect=True)
        backend.generate.return_value = json.dumps(bad)
        assert b.generate(data, backend, {}) is None
        assert backend.generate.call_count == 2


def test_numbers_normalize_without_using_reference_ids(data):
    assert b.numbers("1,500달러 25 bp −0.13%") == b.numbers("1 500 달러 25bp -0.13")
    assert b.numbers("1, 500 달러 .13%") == b.numbers("1500달러 0.13")
    good = valid()
    good["lead"] = "1500달러 25bp " + good["lead"]
    assert b.validate(json.dumps(good), data)
    bad = valid()
    bad["lead"] = "8% " + bad["lead"]  # n8은 수치 근거가 아니다.
    with pytest.raises(b.BriefingError):
        b.validate(json.dumps(bad), data)


def test_daily_cache_and_retention(tmp_path, data, monkeypatch):
    cache = tmp_path / "cache"
    backend = Mock(last_usage=TokenUsage(neurons=3), generate=Mock(return_value=json.dumps(valid())))
    monkeypatch.setattr(b.config, "require_cloudflare_credentials", lambda: None)
    factory = Mock(return_value=backend)
    monkeypatch.setattr(b, "build_backend", factory)
    write_json_atomic(cache / "briefing-2026-11-01.json", {})
    first = b.get_briefing(tmp_path, NOW, cache_dir=cache)
    assert first == b.get_briefing(tmp_path, NOW, cache_dir=cache)
    assert backend.generate.call_count == 1
    assert factory.call_args.kwargs["max_attempts"] == 1
    assert first["usage"]["neurons"] == 3
    assert not (cache / "briefing-2026-11-01.json").exists()


def test_failed_generation_is_cached(tmp_path, data, monkeypatch):
    monkeypatch.setattr(b.config, "require_cloudflare_credentials", lambda: None)
    backend = Mock(last_usage=None, generate=Mock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(b, "build_backend", lambda *a, **kw: backend)
    assert b.get_briefing(tmp_path, NOW, cache_dir=tmp_path / "cache")["briefing"] is None
    b.get_briefing(tmp_path, NOW, cache_dir=tmp_path / "cache")
    assert backend.generate.call_count == 1


def test_render_briefing_links_and_video(tmp_path, data):
    write_json_atomic(tmp_path / "shorts/ko.json", {"date": "2026-11-16", "title": "오늘 영상", "video_id": "abc123xyz00"})
    digest = build_digest(tmp_path, NOW, window_hours=24, max_market_age_hours=36, headlines=3,
                          briefing=b.validate(json.dumps(valid()), data))
    text, html = render(digest, site="https://example.com", unsubscribe_url="https://example.com/off")
    assert "보고서 전문" not in text + html
    assert "href='https://example.com/0'" in html and ">경제 소식 0</a>" in html
    assert "경제 소식 0 (매체)\n  https://example.com/0" in text
    assert text.index("오늘 지켜볼 것") < text.index("시장 논조") < text.index("오늘의 영상")


def test_fallback_only_korean_titles(tmp_path, data):
    docs = [{"kind": "news", "market": "US", "date": "2026-11-16", "title": t, "text": "원문 대체 금지"}
            for t in ["Foreign", "中文", "한국어 소식", "다른 소식", "세 번째 소식"]]
    write_json_atomic(tmp_path / "news.json", {"documents": docs})
    digest = build_digest(tmp_path, NOW, window_hours=24, max_market_age_hours=36, headlines=3)
    text, html = render(digest, site="https://example.com", unsubscribe_url="https://example.com/off")
    assert all(t not in text + html for t in ["Foreign", "中文", "원문 대체 금지", "세 번째 소식"])
    assert "한국어 소식" in text and "다른 소식" in text


def test_no_due_accounts_skips_generation(tmp_path, monkeypatch):
    generate = Mock(side_effect=AssertionError("LLM called"))
    monkeypatch.setattr(send, "get_briefing", generate)
    counts = send.run(users_dir=tmp_path / "users", public_dir=tmp_path, mailer=Mock(), identity_key="x" * 32, moment=NOW)
    assert counts["briefing"] == "skipped"
    generate.assert_not_called()


def test_preview_never_sends_mail(tmp_path, data, monkeypatch, capsys):
    monkeypatch.setattr(send.config, "PUBLIC_DIR", tmp_path)
    monkeypatch.setattr(send.config, "NEWSLETTER_DIR", tmp_path / "cache")
    monkeypatch.setattr(send, "now", lambda: NOW)
    monkeypatch.setattr(send, "get_briefing", Mock(return_value={"briefing": None, "usage": {"neurons": 5}}))
    mailer = Mock(side_effect=AssertionError("SMTP called"))
    monkeypatch.setattr(send, "build_mailer", mailer)
    assert send.main(["--preview"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["neurons"] == 5 and result["text_chars"] > 0
    assert (tmp_path / "cache/preview.html").exists() and (tmp_path / "cache/preview.txt").exists()
    mailer.assert_not_called()


def test_foreign_only_news_still_generates_before_fallback(tmp_path, data, monkeypatch):
    from contextlib import contextmanager
    from services.web.newsletter.subscription import SubscriptionStore

    (tmp_path / "market.json").unlink()
    write_json_atomic(tmp_path / "news.json", {"documents": [{
        "kind": "news", "market": "US", "title": "Foreign headline", "text": "Original",
        "published_at": NOW.isoformat(), "date": "2026-11-16"}]})
    users = tmp_path / "users"
    SubscriptionStore(users / ("a" * 64) / "newsletter.json").write({
        "email": "a@example.com", "status": "active", "code": None, "codes_sent": [],
        "confirmed_at": NOW.isoformat(), "last_sent_on": None, "updated_at": NOW.isoformat()})
    generate = Mock(return_value={"briefing": b.validate(json.dumps(valid()), data)})
    monkeypatch.setattr(send, "get_briefing", generate)
    delivered = []

    @contextmanager
    def session():
        yield delivered.append

    from services.web.newsletter.mailer import Mailer
    mailer = Mailer("smtp.example.com", 465, "user", "pw", "news@example.com", 5)
    monkeypatch.setattr(Mailer, "session", lambda self: session())
    result = send.run(users_dir=users, public_dir=tmp_path, mailer=mailer, identity_key="x" * 32, moment=NOW)
    assert result["sent"] == 1 and result["briefing"] == "ok"
    generate.assert_called_once()
    assert len(delivered) == 1


def test_deepseek_backend_turns_thinking_off_and_qwen_does_not():
    from services.web.llm.backends import CloudflareWorkersAIBackend

    for model, expected in (("@cf/deepseek-ai/deepseek-v4.1-flash-1017", True), ("@cf/google/gemma-4-26b-a4b-it", True), ("@cf/qwen/qwen3-30b-a3b-fp8", False)):
        session = Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = {
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}
        backend = CloudflareWorkersAIBackend(account_id="a", api_token="t", model=model, session=session)
        try:
            backend.generate(system_prompt="s", user_prompt="u", max_tokens=10, temperature=0)
        except Exception:
            pass
        body = session.post.call_args.kwargs["json"]
        assert ("chat_template_kwargs" in body) is expected
