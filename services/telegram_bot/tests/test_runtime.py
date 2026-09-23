import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.telegram_bot import main as bot_main
from services.telegram_bot.main import _acquire_single_instance_lock
from services.telegram_bot.research.state import MarketViewManager
from services.telegram_bot.watchlist.manager import WatchlistManager
from services.telegram_bot.core import workers
from services.telegram_bot.llm.market_view import MarketViewAnalyzer


def test_second_instance_returns_none_instead_of_raising(tmp_path):
    first = _acquire_single_instance_lock(tmp_path / "bot.lock")
    assert first is not None
    try:
        assert _acquire_single_instance_lock(tmp_path / "bot.lock") is None
    finally:
        first.close()


def test_new_watchlist_starts_empty(tmp_path):
    state_file = tmp_path / "watchlist.json"

    manager = WatchlistManager(state_file)

    assert asyncio.run(manager.get_all()) == {}
    assert json.loads(state_file.read_text(encoding="utf-8")) == {}


def test_watchlist_add_and_remove_use_injected_code_resolver(tmp_path):
    state_file = tmp_path / "watchlist.json"
    aliases = {"aapl": "US:NASDAQ:AAPL"}
    manager = WatchlistManager(state_file, code_resolver=aliases.get)

    async def exercise():
        await manager.add("aapl", "Apple")
        added = await manager.get_all()
        removed = await manager.remove("aapl")
        return added, removed, await manager.get_all()

    added, removed, remaining = asyncio.run(exercise())

    assert added == {"US:NASDAQ:AAPL": "Apple"}
    assert removed == "Apple"
    assert remaining == {}


def test_scheduler_uses_application_lifecycle(monkeypatch):
    menu_configured = False
    shutdown_calls = 0

    async def configure_menu(_app):
        nonlocal menu_configured
        menu_configured = True

    monkeypatch.setattr(bot_main, "configure_telegram_menu", configure_menu)

    async def exercise():
        nonlocal shutdown_calls
        scheduler = AsyncIOScheduler()
        original_shutdown = scheduler.shutdown

        def counted_shutdown(*args, **kwargs):
            nonlocal shutdown_calls
            shutdown_calls += 1
            return original_shutdown(*args, **kwargs)

        scheduler.shutdown = counted_shutdown
        registry = SimpleNamespace(is_enabled=lambda _key: False)
        app = SimpleNamespace(
            bot_data={
                "feature_registry": registry,
                "scheduler": scheduler,
            }
        )

        await bot_main._start_application(app)
        assert menu_configured
        assert scheduler.running

        await bot_main._stop_scheduler(app)
        assert not scheduler.running

        # The shutdown hook is registered for both stop and shutdown phases.
        await bot_main._stop_scheduler(app)
        assert shutdown_calls == 1

    asyncio.run(exercise())


def test_main_releases_instance_lock_when_startup_fails(monkeypatch):
    lock = SimpleNamespace(closed=False)

    def close():
        lock.closed = True

    lock.close = close
    monkeypatch.setattr(bot_main, "_acquire_single_instance_lock", lambda _path: lock)

    def fail_to_build_registry(_enabled):
        raise RuntimeError("startup failed")

    monkeypatch.setattr(bot_main, "build_feature_registry", fail_to_build_registry)

    with pytest.raises(RuntimeError, match="startup failed"):
        bot_main.main()

    assert lock.closed


def test_blocked_non_urgent_job_does_not_block_later_analysis():
    """종목 수집이 멈춰도 워커 수보다 많은 후속 분석을 계속 처리한다."""
    release = threading.Event()

    async def exercise():
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def blocked_fetch():
            loop.call_soon_threadsafe(started.set)
            release.wait()

        blocked = asyncio.create_task(workers.run_non_urgent(blocked_fetch))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            for index in range(workers.NON_URGENT_WORKER_COUNT * 2):
                result = await asyncio.wait_for(
                    workers.run_non_urgent(lambda value=index: value), timeout=2
                )
                assert result == index
            assert not blocked.done()
        finally:
            release.set()
            await blocked

    asyncio.run(exercise())


def test_analysis_request_passes_prompt_and_output_budget():
    """분석 요청이 프롬프트·출력 상한·요청별 타임아웃을 백엔드로 넘기는지 확인한다."""
    captured = {}

    class _Backend:
        name = "cloudflare"
        model = "model"

        def generate(self, **kwargs):
            captured.update(kwargs)
            return '{"summary":"ok"}'

    analyzer = object.__new__(MarketViewAnalyzer)
    analyzer._backend = _Backend()
    analyzer._prompt = "prompt"
    analyzer._num_predict = 4096
    analyzer._timeout = 600

    analyzer._request_analysis({"market_view": "AI"})

    assert captured["max_tokens"] == 4096
    assert captured["timeout"] == 600
    assert captured["system_prompt"] == "prompt"
    assert "AI" in captured["user_prompt"]


def test_analysis_payload_includes_new_action_cap():
    analyzer = object.__new__(MarketViewAnalyzer)
    analyzer._timeout = 60
    analyzer._remove_relevance_threshold = 0.35
    analyzer._max_new_actions = 4
    analyzer._max_actions = 6
    captured = {}

    def request(payload, **kwargs):
        captured.update(payload)
        return '{"summary":"요약","actions":[],"risks":[]}'

    analyzer._request_analysis = request
    analyzer.analyze("AI", {}, [], [])
    assert captured["max_new_actions"] == 4
    assert captured["max_actions"] == 6


def test_market_view_change_and_clear_remove_previous_analysis_context(tmp_path):
    state_file = tmp_path / "market_research.json"
    manager = MarketViewManager(state_file, history_limit=3)
    manager.set_sight("AI")
    manager.save_result(
        {
            "generated_at": "2026-07-19T10:00:00",
            "summary": "분석",
            "actions": [{"ticker": "AAPL", "action": "add"}],
            "risks": [],
        }
    )

    manager.set_sight("반도체")
    assert manager.get_last_result() is None
    assert manager.get_history_summaries() == []

    manager.save_result(
        {
            "generated_at": "2026-07-19T11:00:00",
            "summary": "새 분석",
            "actions": [],
            "risks": [],
        }
    )
    manager.clear_sight()
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted == {
        "sight": None,
        "updated_at": None,
        "history": [],
        # 전체 결과도 함께 지운다 — 주제가 없어졌는데 이전 분석이 /research show에
        # 남아 있으면 다른 주제의 결과를 현재 것으로 오해한다.
        "last_result": None,
    }
