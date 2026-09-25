import asyncio
import json
import threading
import subprocess
import sys
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from services.telegram_bot import main as bot_main
from services.telegram_bot.main import _acquire_single_instance_lock
from services.telegram_bot.research.state import MarketViewManager
from services.telegram_bot.watchlist.manager import WatchlistManager
from services.telegram_bot.core import workers
from services.telegram_bot.llm.market_view import MarketViewAnalyzer


@pytest.fixture(autouse=True)
def isolated_shutdown_signal(monkeypatch):
    monkeypatch.setattr(workers, "_stopping", threading.Event())


def test_second_instance_returns_none_instead_of_raising(tmp_path):
    first = _acquire_single_instance_lock(tmp_path / "bot.lock")
    assert first is not None
    try:
        assert _acquire_single_instance_lock(tmp_path / "bot.lock") is None
    finally:
        first.close()


def test_new_watchlist_starts_empty_without_writing(tmp_path):
    # 파일의 주인은 웹 편집 화면이다. 봇이 읽기만 하다가 빈 파일을 만들지 않는다.
    state_file = tmp_path / "watchlist.json"

    manager = WatchlistManager(state_file)

    assert asyncio.run(manager.get_all()) == {}
    assert not state_file.exists()


def test_watchlist_sees_edits_made_by_the_web(tmp_path):
    state_file = tmp_path / "watchlist.json"
    manager = WatchlistManager(state_file)
    state_file.write_text(json.dumps({"600519": "귀주모태주"}), encoding="utf-8")
    assert asyncio.run(manager.get_all()) == {"600519": "귀주모태주"}


def test_broken_watchlist_keeps_last_good_and_reports_it(tmp_path):
    state_file = tmp_path / "watchlist.json"
    state_file.write_text(json.dumps({"600519": "귀주모태주"}), encoding="utf-8")
    manager = WatchlistManager(state_file)
    state_file.write_text("{broken", encoding="utf-8")
    assert asyncio.run(manager.get_all()) == {"600519": "귀주모태주"}
    assert manager.last_error and "실패" in manager.status_line()


def test_research_write_rereads_under_lock_and_keeps_web_edits(tmp_path):
    state_file = tmp_path / "watchlist.json"
    manager = WatchlistManager(state_file)
    asyncio.run(manager.get_all())
    # 봇이 마지막으로 읽은 뒤 웹이 한 종목을 더했다.
    state_file.write_text(json.dumps({"00700": "텐센트"}), encoding="utf-8")
    asyncio.run(manager.add("600519", "귀주모태주"))
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"00700": "텐센트", "600519": "귀주모태주"}
    assert not state_file.with_name("watchlist.json.lock").exists()


def test_stale_lock_from_a_dead_process_is_cleared(tmp_path):
    import os
    import time

    state_file = tmp_path / "watchlist.json"
    lock = state_file.with_name("watchlist.json.lock")
    lock.write_text("12345\n", encoding="utf-8")
    old = time.time() - 120
    os.utime(lock, (old, old))
    manager = WatchlistManager(state_file)
    asyncio.run(manager.add("600519", "귀주모태주"))
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"600519": "귀주모태주"}


def test_live_lock_times_out_instead_of_overwriting(tmp_path):
    from services.telegram_bot.core.storage import FileLockTimeout, file_lock

    lock = tmp_path / "x.lock"
    with file_lock(lock):
        try:
            with file_lock(lock, timeout=0.1):
                raise AssertionError("잠금을 두 번 잡았다")
        except FileLockTimeout:
            pass


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


def test_application_cancels_manual_tasks_before_ptb_waits(monkeypatch):
    order = []

    async def ptb_stop(app):
        assert all(task.done() for task in app.bot_data["shorts_tasks"])
        assert workers.is_stopping()
        order.append("ptb_stop")

    async def drain():
        order.append("drain")

    monkeypatch.setattr(bot_main.Application, "stop", ptb_stop)
    monkeypatch.setattr(bot_main, "drain_workers", drain)

    async def exercise():
        app = bot_main.Application.builder().token("123:fake").application_class(bot_main.BotApplication).build()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def job():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(job())
        app.bot_data["shorts_tasks"] = {task}
        await started.wait()
        await asyncio.wait_for(app.stop(), 1)
        assert cancelled.is_set()

    asyncio.run(exercise())
    assert order == ["ptb_stop", "drain"]


def test_shutdown_pool_preserves_concurrency_results_and_exceptions():
    barrier = threading.Barrier(3)
    release = threading.Event()
    started = []

    def work(value):
        started.append(value)
        barrier.wait(timeout=3)
        release.wait(timeout=3)
        return value * 2

    with workers.ShutdownThreadPool(max_workers=2) as pool:
        first, second = pool.submit(work, 1), pool.submit(work, 2)
        barrier.wait(timeout=3)
        pending = pool.submit(lambda: 6)
        assert sorted(started) == [1, 2] and not pending.done()
        release.set()
        assert [first.result(3), second.result(3), pending.result(3)] == [2, 4, 6]
        assert list(pool.map(lambda n: n * 2, range(5))) == [0, 2, 4, 6, 8]
        failed = pool.submit(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            failed.result(3)
    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)


def test_real_ptb_stop_does_not_wait_for_a_requested_briefing(monkeypatch):
    from telegram import User

    async def drain():
        pass

    monkeypatch.setattr(bot_main, "drain_workers", drain)

    async def exercise():
        app = (bot_main.Application.builder().token("123:fake")
               .application_class(bot_main.BotApplication).job_queue(None).build())
        # 네트워크 초기화만 생략하고 PTB의 실제 start/create_task/stop을 쓴다.
        app._initialized = True
        app.bot._bot_user = User(id=123, first_name="test", is_bot=True)
        await app.start()
        started = asyncio.Event()

        async def briefing():
            started.set()
            await asyncio.Event().wait()

        task = app.create_task(briefing(), name="requested-briefing")
        await started.wait()
        await asyncio.wait_for(app.stop(), timeout=2)
        assert task.cancelled() and not app.running

    asyncio.run(exercise())


def test_blocked_worker_does_not_hold_interpreter_exit():
    # 실제 프로세스 종료를 검사한다. daemon=True만 붙인 표준 실행기는
    # concurrent.futures의 atexit join 때문에 이 검사를 통과하지 못한다.
    code = '''
import asyncio
import threading
from services.telegram_bot.core.workers import ShutdownThreadPool, drain_workers
started = threading.Event()
release = threading.Event()
pool = ShutdownThreadPool(max_workers=1, thread_name_prefix="research")
def blocked():
    started.set()
    release.wait(60)
pool.submit(blocked)
assert started.wait(2)
pending = pool.submit(lambda: None)
asyncio.run(drain_workers(0.05))
assert pending.cancelled()
print("stopped")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert b"stopped" in result.stdout and b"research_0" in result.stderr


def test_main_releases_instance_lock_when_startup_fails(monkeypatch):
    monkeypatch.setattr(bot_main, "install_default_requests_timeout", lambda _timeout: None)
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
