"""관리 웹 기동 실패가 봇 이벤트 루프를 무너뜨리지 않는지 확인한다."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from services.telegram_bot.webadmin import server as web_server


class _ExitingServer:
    """바인드 실패 시 sys.exit()을 부르는 uvicorn 동작을 재현한다."""

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        self.install_signal_handlers = None

    async def serve(self):
        raise SystemExit(3)


def _run_start(monkeypatch, server_cls):
    import uvicorn

    monkeypatch.setattr(web_server, "WEB_ADMIN_PASSWORD", "pw")
    monkeypatch.setattr(uvicorn, "Server", server_cls)

    bot_app = SimpleNamespace(bot_data={})

    async def exercise():
        await web_server.start_web_admin(bot_app)
        task = bot_app.bot_data["web_admin_task"]
        await task
        # 루프가 살아 있어야 봇 폴링이 계속된다.
        await asyncio.sleep(0)
        return task

    return asyncio.run(exercise()), bot_app


def test_bind_failure_does_not_kill_event_loop(monkeypatch, caplog):
    with caplog.at_level(logging.WARNING, logger=web_server.logger.name):
        task, bot_app = _run_start(monkeypatch, _ExitingServer)

    assert task.done()
    assert task.exception() is None
    assert "바인드에 실패" in caplog.text
    assert str(web_server.WEB_ADMIN_PORT) in caplog.text
    # 실패해도 종료 경로는 정상 동작해야 한다.
    asyncio.run(web_server.stop_web_admin(bot_app))
    assert "web_admin_server" not in bot_app.bot_data


def test_runtime_error_does_not_kill_event_loop(monkeypatch, caplog):
    class _BrokenServer(_ExitingServer):
        async def serve(self):
            raise RuntimeError("boom")

    with caplog.at_level(logging.WARNING, logger=web_server.logger.name):
        task, _ = _run_start(monkeypatch, _BrokenServer)

    assert task.exception() is None
    assert "boom" in caplog.text


def test_cancellation_still_propagates(monkeypatch):
    class _HangingServer(_ExitingServer):
        async def serve(self):
            await asyncio.Event().wait()

    import uvicorn

    monkeypatch.setattr(web_server, "WEB_ADMIN_PASSWORD", "pw")
    monkeypatch.setattr(uvicorn, "Server", _HangingServer)

    bot_app = SimpleNamespace(bot_data={})

    async def exercise():
        await web_server.start_web_admin(bot_app)
        task = bot_app.bot_data["web_admin_task"]
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return task

    assert asyncio.run(exercise()).cancelled()
