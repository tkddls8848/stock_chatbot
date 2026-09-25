"""봇 진입점: 서비스 구성, 핸들러 등록, 스케줄러 구동."""

import asyncio
import logging
import os
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, ContextTypes

from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.config import (
    DEFAULT_REQUESTS_TIMEOUT_SECONDS,
    FEATURES_ENABLED,
    RUNTIME_LOCK_FILE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_CONCURRENT_UPDATES,
    TELEGRAM_POLL_TIMEOUT_SECONDS,
    TELEGRAM_POOL_TIMEOUT_SECONDS,
    TELEGRAM_READ_TIMEOUT_SECONDS,
    TELEGRAM_WRITE_TIMEOUT_SECONDS,
)
from services.telegram_bot.core.http_timeout import install_default_requests_timeout
from services.telegram_bot.core.workers import (
    ShutdownThreadPool, drain_workers, is_stopping, request_shutdown, reset_shutdown,
)
from services.telegram_bot.features import build_feature_registry
from services.telegram_bot.handlers.commands import configure_telegram_menu

logger = logging.getLogger(__name__)

# 등록된 CommandHandler·MessageHandler가 읽는 모든 effective_message 유형과
# CallbackQueryHandler가 읽는 콜백만 받는다. 결제·투표·멤버십 같은 미사용
# 업데이트는 Telegram 서버에서 걸러 update queue와 JSON 파싱 비용을 만들지 않는다.
_ALLOWED_UPDATES = (
    Update.MESSAGE,
    Update.EDITED_MESSAGE,
    Update.CHANNEL_POST,
    Update.EDITED_CHANNEL_POST,
    Update.BUSINESS_MESSAGE,
    Update.EDITED_BUSINESS_MESSAGE,
    Update.CALLBACK_QUERY,
)
_RUNTIME_STOPPED_KEY = "_runtime_stopped"


class BotApplication(Application):
    """PTB가 수동 작업 완료를 기다리기 *전에* 생산자와 작업을 멈춘다."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._runtime_tasks = set()

    def create_task(self, coroutine, update=None, *, name=None):
        task = super().create_task(coroutine, update=update, name=name)
        self._runtime_tasks.add(task)
        task.add_done_callback(self._runtime_tasks.discard)
        return task

    async def process_update(self, update):
        # 정지 시 이미 큐에 있던 명령이 작업을 다시 시작하지 않게 한다.
        if not is_stopping():
            await super().process_update(update)

    async def stop(self):
        await _stop_scheduler(self)
        tasks = set(self._runtime_tasks)
        for key in ("shorts_tasks", "research_tasks"):
            tasks.update(self.bot_data.get(key, ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("수동 작업 %d개를 정리했습니다. Telegram 연결을 종료합니다.", len(tasks))
        await super().stop()
        await drain_workers()
        logger.info("봇 종료 정리를 마쳤습니다.")


def _acquire_single_instance_lock(lock_file: Path):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        handle = lock_file.open("a+b")
        handle.seek(0)
        if not handle.read(1):
            handle.write(b"0")
            handle.flush()
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        if handle is not None:
            handle.close()
        return None
    return handle


# ── 진입점 ────────────────────────────────────────────

async def _handle_update_error(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("[TELEGRAM] update processing failed: %s", context.error, exc_info=context.error)


def build_scheduler() -> AsyncIOScheduler:
    """cron 작업을 전부 JST로 읽는 스케줄러.

    시간대를 주지 않으면 호스트 시간대를 따르는데 공유 호스트가 UTC라,
    2026-09-24까지 브리핑(08:50)·종목 DB 갱신(08:30)이 9시간 늦게 돌았다.
    작업마다 timezone을 붙이면 새 작업이 또 빠뜨리므로 기본값을 여기서 정한다.
    """
    return AsyncIOScheduler(timezone=JST)


async def _start_application(app: Application) -> None:
    reset_shutdown()
    asyncio.get_running_loop().set_default_executor(ShutdownThreadPool(thread_name_prefix="collection"))
    app.bot_data.pop(_RUNTIME_STOPPED_KEY, None)
    await configure_telegram_menu(app)
    scheduler = app.bot_data["scheduler"]
    scheduler.start()


async def _stop_scheduler(app: Application) -> None:
    if app.bot_data.get(_RUNTIME_STOPPED_KEY):
        return

    # PTB.stop의 수동 작업 대기보다 먼저 생산자를 중단한다.
    logger.info("종료 정리를 시작합니다.")
    request_shutdown()

    scheduler = app.bot_data.get("scheduler")
    scheduler_was_running = scheduler is not None and scheduler.running
    if scheduler_was_running:
        scheduler.pause()
        scheduler.shutdown(wait=False)

    if scheduler_was_running:
        # AsyncIOScheduler.shutdown() schedules cleanup and task cancellation on the
        # event loop. Drain those callbacks before Python starts tearing imports down.
        for _ in range(3):
            await asyncio.sleep(0)
        logger.info("작업 스케줄러를 종료했습니다.")
    app.bot_data[_RUNTIME_STOPPED_KEY] = True
    logger.info("스케줄러 종료 정리를 마쳤습니다.")


def main() -> None:
    # 타임아웃 없이 나가는 requests 호출(akshare 등)이 무한정 붙잡지 않게 한다.
    install_default_requests_timeout(DEFAULT_REQUESTS_TIMEOUT_SECONDS)
    single_instance_lock = _acquire_single_instance_lock(RUNTIME_LOCK_FILE)
    if single_instance_lock is None:
        logger.error("이미 실행 중인 봇 인스턴스가 있어 시작하지 않습니다.")
        return

    try:
        feature_registry = build_feature_registry(FEATURES_ENABLED)
        app = (
            Application.builder()
            .application_class(BotApplication)
            .token(TELEGRAM_BOT_TOKEN)
            .connect_timeout(TELEGRAM_CONNECT_TIMEOUT_SECONDS)
            .read_timeout(TELEGRAM_READ_TIMEOUT_SECONDS)
            .write_timeout(TELEGRAM_WRITE_TIMEOUT_SECONDS)
            .pool_timeout(TELEGRAM_POOL_TIMEOUT_SECONDS)
            .concurrent_updates(TELEGRAM_CONCURRENT_UPDATES)
            .post_init(_start_application)
            .post_stop(_stop_scheduler)
            .post_shutdown(_stop_scheduler)
            .build()
        )

        app.bot_data["feature_registry"] = feature_registry
        feature_registry.install_services(app)
        app.add_error_handler(_handle_update_error)

        feature_registry.install_telegram_handlers(app)

        scheduler = build_scheduler()
        app.bot_data["scheduler"] = scheduler
        feature_registry.install_jobs(scheduler, app)

        logger.info(
            "봇 시작됨. 활성 기능: %s",
            ", ".join(sorted(feature_registry.enabled_keys)),
        )
        logger.info(
            "명령어: %s",
            " ".join(
                f"/{command.command}"
                for command in feature_registry.telegram_commands()
            ),
        )
        app.run_polling(
            timeout=TELEGRAM_POLL_TIMEOUT_SECONDS,
            allowed_updates=_ALLOWED_UPDATES,
        )
    finally:
        single_instance_lock.close()


if __name__ == "__main__":
    main()
