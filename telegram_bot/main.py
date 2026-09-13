"""봇 진입점: 서비스 구성, 핸들러 등록, 스케줄러 구동."""

import asyncio
import logging
import os
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Update
from telegram.ext import Application, ContextTypes

from telegram_bot.core.config import (
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
from telegram_bot.features import build_feature_registry
from telegram_bot.handlers.commands import configure_telegram_menu
from telegram_bot.webadmin.server import start_web_admin, stop_web_admin

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


async def _start_application(app: Application) -> None:
    app.bot_data.pop(_RUNTIME_STOPPED_KEY, None)
    await configure_telegram_menu(app)
    scheduler = app.bot_data["scheduler"]
    scheduler.start()
    # 웹 서버는 Application 기동 후에만 시작할 수 있어 install_services가
    # 아닌 여기서 기능 활성 여부를 보고 띄운다.
    registry = app.bot_data["feature_registry"]
    if registry.is_enabled("web_admin"):
        await start_web_admin(app)


async def _stop_scheduler(app: Application) -> None:
    if app.bot_data.get(_RUNTIME_STOPPED_KEY):
        return

    scheduler = app.bot_data.get("scheduler")
    scheduler_was_running = scheduler is not None and scheduler.running
    if scheduler_was_running:
        # 웹 서버 종료가 최대 5초 걸릴 수 있으므로 새 예약 작업부터 막는다.
        scheduler.pause()
        scheduler.shutdown(wait=False)

    await stop_web_admin(app)

    if scheduler_was_running:
        # AsyncIOScheduler.shutdown() schedules cleanup and task cancellation on the
        # event loop. Drain those callbacks before Python starts tearing imports down.
        for _ in range(3):
            await asyncio.sleep(0)
        logger.info("작업 스케줄러를 종료했습니다.")
    app.bot_data[_RUNTIME_STOPPED_KEY] = True


def main() -> None:
    single_instance_lock = _acquire_single_instance_lock(RUNTIME_LOCK_FILE)
    if single_instance_lock is None:
        logger.error("이미 실행 중인 봇 인스턴스가 있어 시작하지 않습니다.")
        return

    try:
        feature_registry = build_feature_registry(FEATURES_ENABLED)
        app = (
            Application.builder()
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

        scheduler = AsyncIOScheduler()
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
