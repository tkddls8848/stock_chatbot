"""의미 검증 실패는 사유를 붙여 한 번만 다시 묻고, 잘린 응답은 다시 묻지 않는다."""

import pytest

from polymarket_shorts import highlights
from polymarket_shorts.config import Settings
from polymarket_shorts.highlights import HighlightError
from polymarket_shorts.llm import TruncatedError


def _run(monkeypatch, replies):
    calls = []

    def chat(settings, **kwargs):
        calls.append(kwargs["user"])
        reply = replies[len(calls) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(highlights, "chat_json", chat)

    def check(payload):
        if not payload.get("ok"):
            raise HighlightError("watch_point에 원문에 없는 숫자가 있습니다")
        return payload

    result = highlights._ask_checked(Settings.from_env(), system="s", user="u", max_tokens=10, check=check)
    return result, calls


def test_first_valid_answer_costs_one_call(monkeypatch):
    result, calls = _run(monkeypatch, [{"ok": True}])
    assert result == {"ok": True} and len(calls) == 1


def test_invalid_answer_is_corrected_once_with_the_reason(monkeypatch):
    result, calls = _run(monkeypatch, [{"ok": False}, {"ok": True}])
    assert result == {"ok": True} and len(calls) == 2
    assert "watch_point에 원문에 없는 숫자" in calls[1] and '"ok": false' in calls[1]


def test_second_failure_stops(monkeypatch):
    with pytest.raises(HighlightError):
        _run(monkeypatch, [{"ok": False}, {"ok": False}, {"ok": True}])


def test_truncated_answer_is_not_retried(monkeypatch):
    with pytest.raises(TruncatedError):
        _run(monkeypatch, [TruncatedError("cut"), {"ok": True}])


def test_watch_point_may_cite_a_number_from_the_description():
    issue = {"id": "1", "title": "Strait traffic normal?", "description": "Resolves on the 7-day moving average.",
             "markets": [], "news": []}
    row = {"id": "1", "headline": "해협 통행 정상화", "question": "해협 통행이 정상화될까요?",
           "context": "해협 교통량은 국제 통상에 영향을 줍니다.", "watch_point": "포트워치의 7일 이동 평균을 확인하세요.",
           "market_labels": [], "news_ids": []}
    assert highlights.validate_scripts({"scripts": [row]}, [issue])[0]["watch_point"].startswith("포트워치")
    with pytest.raises(HighlightError):
        highlights.validate_scripts({"scripts": [{**row, "watch_point": "포트워치의 30일 평균을 확인하세요."}]}, [issue])
