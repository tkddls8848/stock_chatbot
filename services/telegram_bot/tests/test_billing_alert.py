"""잔량·요금·실패 알림 계약. HTTP와 Telegram은 전부 mock이다."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.storage import write_json_atomic
from services.telegram_bot.features.billing_alert import service as s


def usage(*rows):
    return {"data": {"viewer": {"accounts": [{"aiInferenceAdaptiveGroups": [
        {"dimensions": {"date": day}, "sum": {"totalNeurons": neurons}}
        for day, neurons in rows
    ]}]}}}


@pytest.fixture
def env(monkeypatch, tmp_path):
    current = datetime(2026, 11, 18, 18, 30, tzinfo=JST)
    get = Mock(return_value=Mock(json=Mock(return_value={
        "character_limit": 10_000, "character_count": 0,
        "next_character_count_reset_unix": int(datetime(2026, 11, 25, tzinfo=JST).timestamp()),
    })))
    post = Mock(return_value=Mock(json=Mock(return_value=usage())))
    monkeypatch.setattr(s.requests, "get", get)
    monkeypatch.setattr(s.requests, "post", post)
    monkeypatch.setattr(s, "TELEGRAM_CHAT_ID", "operator")
    monkeypatch.setattr(s, "ELEVENLABS_API_KEY", "test-eleven-key")
    monkeypatch.setattr(s, "CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setattr(s, "CLOUDFLARE_WORKER_AI_API_TOKEN", "test-cloudflare-token")
    item = SimpleNamespace(current=current, get=get, post=post, path=tmp_path / "state.json",
                           app=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())))
    monkeypatch.setattr(s, "now", lambda: item.current)
    return item


def run(env):
    asyncio.run(s.poll(env.app, path=env.path))


def test_elevenlabs_threshold_and_reset_cycle(env, monkeypatch):
    payload = env.get.return_value.json.return_value
    payload["character_count"] = 6_700  # 정확히 3,300자는 아직 경고하지 않는다.
    run(env)
    env.app.bot.send_message.assert_not_awaited()
    payload["character_count"] = 7_900
    run(env)
    text = env.app.bot.send_message.call_args.kwargs["text"]
    assert "2,100자" in text and "약 1편" in text and "리셋 11-25" in text
    assert "기본 음성" in text
    run(env)
    assert env.app.bot.send_message.await_count == 1
    payload["next_character_count_reset_unix"] += 30 * 86400
    run(env)
    assert env.app.bot.send_message.await_count == 2
    assert env.get.call_args.kwargs == {
        "headers": {"xi-api-key": "test-eleven-key"}, "timeout": s.HTTP_TIMEOUT_SECONDS,
    }
    monkeypatch.setattr(s, "ELEVENLABS_API_KEY", "")
    env.get.reset_mock()
    run(env)
    env.get.assert_not_called()


def test_cloudflare_daily_allowance_boundary_and_month(env, monkeypatch):
    monkeypatch.setattr(s, "ELEVENLABS_API_KEY", "")
    # JST 12/1이지만 UTC는 11/30이다. 같은 날짜의 행은 무료분 차감 전에 합친다.
    env.current = datetime(2026, 12, 1, 0, 30, tzinfo=JST)
    env.post.return_value.json.return_value = usage(
        ("2026-11-01", 5_000), ("2026-11-02", 50_000), ("2026-11-02", 50_909),
    )
    run(env)  # 90,909 × .011 / 1,000 < $1; 안 쓴 무료분은 이월하지 않는다.
    env.app.bot.send_message.assert_not_awaited()
    env.post.return_value.json.return_value = usage(
        ("2026-11-01", 5_000), ("2026-11-02", 50_000), ("2026-11-02", 50_910),
    )
    run(env)
    text = env.app.bot.send_message.call_args.kwargs["text"]
    assert "$1.00" in text and "90,910 Neurons" in text and "청구 주기" in text
    assert env.post.call_args.kwargs["json"]["variables"] == {
        "accountTag": "test-account", "start": "2026-11-01", "end": "2026-11-30",
    }
    assert env.post.call_args.args == (s.CLOUDFLARE_AI_BASE_URL + "/graphql",)
    assert env.post.call_args.kwargs["headers"] == {"Authorization": "Bearer test-cloudflare-token"}
    assert env.post.call_args.kwargs["timeout"] == s.HTTP_TIMEOUT_SECONDS
    run(env)
    assert env.app.bot.send_message.await_count == 1
    env.current += timedelta(hours=9)
    env.post.return_value.json.return_value = usage(("2026-12-01", 122_000))
    run(env)
    assert env.app.bot.send_message.await_count == 2
    assert "112,000 Neurons" in env.app.bot.send_message.call_args.kwargs["text"]
    assert json.loads(env.path.read_text())["cloudflare_cycle"] == "2026-12"


def test_check_failures_daily_permissions_and_independence(env, caplog):
    env.get.return_value.raise_for_status.side_effect = requests.HTTPError(
        "secret response body", response=Mock(status_code=401),
    )
    env.post.return_value.json.return_value = usage(("2026-11-01", 122_000))
    run(env)
    text = env.app.bot.send_message.call_args.kwargs["text"]
    assert "잔량을 확인하지 못했다" in text and "API 키에 user_read 권한 필요" in text
    assert "HTTP 401" in text and "$1.23" in text  # 한쪽 실패가 다른 쪽 경고를 막지 않는다.
    run(env)
    assert env.app.bot.send_message.await_count == 1
    env.post.return_value.raise_for_status.side_effect = requests.HTTPError(
        "secret response body", response=Mock(status_code=403),
    )
    run(env)
    assert "토큰에 Account Analytics 읽기 권한 필요" in env.app.bot.send_message.call_args.kwargs["text"]
    run(env)
    assert env.app.bot.send_message.await_count == 2
    env.current = env.current.replace(hour=23, minute=59)
    run(env)
    assert env.app.bot.send_message.await_count == 2
    env.current += timedelta(minutes=1)
    run(env)
    assert env.app.bot.send_message.await_count == 3
    assert env.app.bot.send_message.call_args.kwargs["text"].count("잔량을 확인하지 못했다") == 2
    env.get.return_value.raise_for_status.side_effect = None
    env.post.return_value.raise_for_status.side_effect = None
    # GraphQL 오류·잘못된 envelope/숫자·네트워크도 모두 실패 알림 대상이다.
    for payload in ({"errors": [{"message": "secret response body"}]}, {},
                    usage(("2026-11-01", float("nan")))):
        env.current += timedelta(days=1)
        env.post.return_value.json.return_value = payload
        run(env)
        text = env.app.bot.send_message.call_args.kwargs["text"]
        assert "Cloudflare Workers AI 잔량을 확인하지 못했다" in text
        assert ("Account Analytics" in text) == ("errors" in payload)  # GraphQL 권한 부족은 HTTP 200으로 온다.
    env.current += timedelta(days=1)
    env.get.side_effect = requests.Timeout("secret response body")
    run(env)
    assert "ElevenLabs 잔량을 확인하지 못했다" in env.app.bot.send_message.call_args.kwargs["text"]
    assert "secret" not in caplog.text
    assert all("secret" not in call.kwargs["text"] for call in env.app.bot.send_message.call_args_list)


def test_send_failure_does_not_record_state(env):
    env.get.return_value.json.return_value["character_count"] = 9_000
    env.post.return_value.raise_for_status.side_effect = requests.HTTPError(response=Mock(status_code=401))
    env.app.bot.send_message.side_effect = RuntimeError("secret telegram error")
    run(env)
    assert not env.path.exists()
    write_json_atomic(env.path, {"elevenlabs_cycle": "previous"})
    before = env.path.read_bytes()
    run(env)
    assert env.path.read_bytes() == before
    env.app.bot.send_message.side_effect = None
    run(env)
    state = json.loads(env.path.read_text())
    assert state["elevenlabs_cycle"] != "previous"
    assert state["cloudflare_failure_day"] == "2026-11-18"
    assert env.app.bot.send_message.call_args.kwargs["chat_id"] == "operator"


def test_daily_cron_and_immediate_start(env, monkeypatch):
    scheduler = AsyncIOScheduler(timezone=JST)
    s.install_jobs(scheduler, env.app)
    job = scheduler.get_job("billing_alert")
    assert job.next_run_time == env.current
    assert job.misfire_grace_time is None  # Telegram 초기화가 늦어져도 기동 확인을 실행한다.
    assert job.max_instances == 1 and job.coalesce is True
    assert job.args == (env.app,)
    assert str(job.trigger.timezone) == str(JST)
    following = job.trigger.get_next_fire_time(env.current, env.current)
    assert following == datetime(2026, 11, 19, 18, 30, tzinfo=JST)
    monkeypatch.setattr(s, "TELEGRAM_CHAT_ID", "")
    empty = Mock()
    s.install_jobs(empty, env.app)
    empty.add_job.assert_not_called()
    run(env)
    env.get.assert_not_called()
    env.post.assert_not_called()
    env.app.bot.send_message.assert_not_awaited()
