"""소진 알림은 UTC 날짜별 한 번이며 재기동에도 반복하지 않는다."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from weakref import WeakSet

import pytest

from services.telegram_bot.features import build_feature_registry
from services.telegram_bot.features.system_admin import handlers, llm_status
from services.telegram_bot.llm import backends


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(backends, "_backends", WeakSet())
    monkeypatch.setattr(backends, "_last_quota_exhaustion", None)
    monkeypatch.setattr(llm_status, "NOTICE_FILE", tmp_path / "notice.json")
    stamp = [datetime(2026, 9, 25, 7, tzinfo=timezone.utc).timestamp()]
    app = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})
    return stamp, app


def exhaust(stamp):
    provider = SimpleNamespace(name="cloudflare", model="model")
    provider.generate = lambda **kw: (_ for _ in ()).throw(backends.LLMBackendError(
        "cloudflare", "quota_exhausted", quota_exhausted=True, detail="private response",
    ))
    backend = backends.ResilientBackend(backend=provider, clock=lambda: stamp[0])
    with pytest.raises(backends.LLMBackendError):
        backend.generate(system_prompt="", user_prompt="", max_tokens=1, temperature=0)
    return backend


def test_notice_once_across_backends_and_restart_then_next_utc_day(runtime):
    stamp, app = runtime
    first = exhaust(stamp)
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    text = app.bot.send_message.call_args.kwargs["text"]
    assert "2026-09-25 16:00 한국 시간" in text
    assert "2026-09-26 09:00 한국 시간" in text
    assert "보고서·브리핑·리서치·시장 감성" in text
    assert "Cloudflare 유료 전환 또는 같은 계정의 다른 사용처 확인" in text
    assert "private response" not in text
    assert app.bot.send_message.call_args.kwargs["chat_id"] == llm_status.TELEGRAM_CHAT_ID
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    # 한국 날짜가 바뀌어도 같은 UTC 날짜이면 다른 용도의 회로도 알리지 않는다.
    stamp[0] += 12 * 3600
    second = exhaust(stamp)
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    assert app.bot.send_message.await_count == 1
    # 새 앱과 새 백엔드: 메모리가 아니라 저장 파일로 중복을 차단한다.
    restarted = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()), bot_data={})
    third = exhaust(stamp)
    asyncio.run(llm_status.notify_quota_exhaustion(restarted))
    restarted.bot.send_message.assert_not_awaited()
    stamp[0] += 6 * 3600
    fourth = exhaust(stamp)
    asyncio.run(llm_status.notify_quota_exhaustion(restarted))
    restarted.bot.send_message.assert_awaited_once()
    assert json.loads(llm_status.NOTICE_FILE.read_text()) == {"utc_date": "2026-09-26"}
    assert len((first, second, third, fourth)) == 4


def test_no_notice_for_normal_or_nonquota_failure(runtime):
    _, app = runtime
    backend = backends.ResilientBackend(backend=SimpleNamespace(name="cloudflare"))
    backend._record_failure(backends.LLMBackendError("cloudflare", "auth_error", fatal=True))
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    app.bot.send_message.assert_not_awaited()
    assert not llm_status.NOTICE_FILE.exists()
    text = asyncio.run(llm_status.render_llm_status({}))
    assert "인증 실패" in text
    assert "재시작 필요" in text


def test_system_screen_uses_registered_llm_status_and_expiry(runtime):
    stamp, app = runtime
    registry = build_feature_registry(["system_admin"])
    context = SimpleNamespace(args=[], bot_data={"feature_registry": registry})
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message)
    asyncio.run(handlers.cmd_system(update, context))
    assert "LLM 회로: 정상" in message.reply_text.call_args.args[0]
    backend = exhaust(stamp)
    asyncio.run(handlers.cmd_system(update, context))
    assert "LLM 회로: 열림 · 할당량 소진 · 해제: 2026-09-26 09:00 한국 시간" in message.reply_text.call_args.args[0]
    context.args = ["llm"]
    asyncio.run(handlers.cmd_system(update, context))
    assert message.reply_text.call_args.args[0].startswith("LLM 회로: 열림")
    stamp[0] = datetime(2026, 9, 26, tzinfo=timezone.utc).timestamp()
    assert backend.circuit_state() is None
    assert asyncio.run(llm_status.render_llm_status({})) == "LLM 회로: 정상"
    # 자정 직전 소진을 감시 주기가 놓치지 않는다.
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    app.bot.send_message.assert_awaited_once()


def test_failed_send_is_retried_on_the_next_cycle(runtime):
    """운영 알림은 중복보다 누락이 비싸다 — 발송이 실패하면 기록하지 않고 다시 보낸다."""
    stamp, app = runtime
    backend = exhaust(stamp)
    app.bot.send_message.side_effect = TimeoutError("delivery failed")
    with pytest.raises(TimeoutError):
        asyncio.run(llm_status.notify_quota_exhaustion(app))
    assert not llm_status.NOTICE_FILE.exists()
    app.bot.send_message.side_effect = None
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    assert app.bot.send_message.await_count == 2
    assert backend.circuit_state().reason == "quota_exhausted"


def test_broken_notice_file_counts_as_not_sent(runtime):
    stamp, app = runtime
    exhaust(stamp)
    llm_status.NOTICE_FILE.write_text("{broken", encoding="utf-8")
    asyncio.run(llm_status.notify_quota_exhaustion(app))
    assert app.bot.send_message.await_count == 1
    assert json.loads(llm_status.NOTICE_FILE.read_text()) == {"utc_date": "2026-09-25"}


def test_quota_monitor_is_installed_once_without_llm_calls(runtime):
    from services.telegram_bot.main import build_scheduler
    _, app = runtime
    registry = build_feature_registry(["system_admin"])
    scheduler = build_scheduler()
    registry.install_jobs(scheduler, app)
    job = scheduler.get_job("llm_quota_notice")
    assert job.func is llm_status.notify_quota_exhaustion
    assert job.trigger.interval.total_seconds() == 30
    assert job.max_instances == 1
    assert registry.status_report("llm").render is llm_status.render_llm_status
