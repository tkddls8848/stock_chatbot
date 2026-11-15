"""운영자 전용 알림의 전송·중복 계약. HTTP와 Telegram은 mock이다."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from services.telegram_bot.core.storage import write_json_atomic
from services.telegram_bot.features.consensus_alert import service as s


def row(**changes):
    return {"id": "e", "title": "Will Taiwan sanctions end?", "summary": "대만 제재 해제?",
            "event_type": "binary", "leader": "No", "probability": .2, "day_change": .052,
            "news_count": 2, **changes}


def payload(generation="g", **changes):
    return {"generation_id": generation, "quiet_moves_state": "ok", "quiet_moves": [row()], **changes}


def app():
    return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))


def test_generation_processed_only_once(tmp_path):
    application = app()
    path = tmp_path / "state.json"
    asyncio.run(s.process(application, payload(), path=path))
    before = path.read_bytes()
    asyncio.run(s.process(application, payload(), path=path))
    application.bot.send_message.assert_awaited_once()
    assert path.read_bytes() == before


def test_cooldown_escalation_and_retention(tmp_path, monkeypatch):
    current = s.now()
    monkeypatch.setattr(s, "now", lambda: current)
    path = tmp_path / "state.json"
    write_json_atomic(path, {"last_generation": "old", "sent": {
        "e": {"at": current.isoformat(), "day_change": .1},
        "expired": {"at": (current - timedelta(days=8)).isoformat(), "day_change": .1}}})
    application = app()
    asyncio.run(s.process(application, payload("a", quiet_moves=[row(day_change=-.149)]), path=path))
    application.bot.send_message.assert_not_awaited()
    assert "expired" not in json.loads(path.read_text())["sent"]
    asyncio.run(s.process(application, payload("b", quiet_moves=[row(day_change=-.15)]), path=path))
    assert application.bot.send_message.await_count == 1
    current += timedelta(hours=24)
    asyncio.run(s.process(application, payload("c"), path=path))
    assert application.bot.send_message.await_count == 2


def test_non_ok_records_generation_without_sending(tmp_path):
    application = app()
    path = tmp_path / "state.json"
    for status in ("news_stale", "news_missing", "failed", None):
        asyncio.run(s.process(application, payload(str(status), quiet_moves_state=status), path=path))
        assert json.loads(path.read_text())["last_generation"] == str(status)
    application.bot.send_message.assert_not_awaited()


def test_send_failure_does_not_change_state(tmp_path):
    path = tmp_path / "state.json"
    write_json_atomic(path, {"last_generation": "old", "sent": {}})
    before = path.read_bytes()
    application = app()
    application.bot.send_message.side_effect = RuntimeError("send failed")
    with pytest.raises(RuntimeError):
        asyncio.run(s.process(application, payload(), path=path))
    assert path.read_bytes() == before
    application.bot.send_message.side_effect = None
    asyncio.run(s.process(application, payload(), path=path))
    assert json.loads(path.read_text())["last_generation"] == "g"


def test_message_probability_direction_and_search_link():
    text = s.message([row(), row(event_type="exclusive_multi", leader="Alice", probability=.625,
                                 day_change=-.061, summary=None)])
    assert "대만 제재 해제? — 지금 20.0% · 24시간 +5.2%p · 관련 뉴스 2건" in text
    assert "지금 Alice 62.5% · 24시간 -6.1%p" in text
    assert "No 20.0%" not in text
    url = text.splitlines()[2].strip()
    assert urlsplit(url).path == "/forecast"
    assert parse_qs(urlsplit(url).query)["q"] == [row()["title"]]


def test_http_schedule_and_operator_delivery(monkeypatch, tmp_path):
    response = Mock()
    response.json.return_value = payload()
    fetch = Mock(return_value=response)
    monkeypatch.setattr(s.requests, "get", fetch)
    result = s.fetch_trending()
    fetch.assert_called_once_with(s.WEB_STATUS_BASE_URL + "/api/forecast/trending", timeout=s.WEB_STATUS_TIMEOUT_SECONDS)
    scheduler = Mock()
    application = app()
    s.install_jobs(scheduler, application)
    assert scheduler.add_job.call_args.kwargs["minute"] == 25
    assert scheduler.add_job.call_args.kwargs["max_instances"] == 1
    asyncio.run(s.process(application, result, path=tmp_path / "state.json"))
    assert application.bot.send_message.call_args.kwargs["chat_id"] == s.TELEGRAM_CHAT_ID
