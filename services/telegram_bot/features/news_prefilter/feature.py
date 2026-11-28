"""보고서용 로컬 뉴스 사건 메모리·사전선별 기능 선언."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.config import (
    NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
    NEWS_PREFILTER_CPU_STATE_FILE,
    NEWS_PREFILTER_EMBED_DIR,
    NEWS_PREFILTER_EVENT_FILE,
    NEWS_PREFILTER_EVENT_WINDOW_HOURS,
    NEWS_PREFILTER_EXPLORATION_SLOTS,
    NEWS_PREFILTER_BURST_BASELINE,
    NEWS_PREFILTER_BURST_FLOOR_PERCENT,
    NEWS_PREFILTER_HOST_CPU_FILE,
    NEWS_PREFILTER_HOST_STEAL_LIMIT,
    NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS,
    NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES,
    NEWS_PREFILTER_MAINTENANCE_MAX_SECONDS,
    NEWS_PREFILTER_MAX_EVENTS,
    NEWS_PREFILTER_MAX_LOAD_AVERAGE,
    NEWS_PREFILTER_MODE,
    NEWS_PREFILTER_MODEL_FILE,
    NEWS_PREFILTER_OBSERVATION_FILE,
    NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS,
    NEWS_PREFILTER_RECAP_BUCKET_HOURS,
    NEWS_PREFILTER_RECAP_MAX_AGE_HOURS,
    NEWS_PREFILTER_SIMILARITY_THRESHOLD,
    NEWS_PREFILTER_REPORTED_EVENT_COOLDOWN_HOURS,
)
from services.telegram_bot.core.workers import is_burst_active
from services.telegram_bot.features.base import FeatureSpec, StatusReportSpec
from services.telegram_bot.features.news_prefilter.host_cpu import HostCpuBudget
from services.telegram_bot.features.news_prefilter.report import render_prefilter_status
from services.telegram_bot.features.news_prefilter.service import NewsPrefilter

logger = logging.getLogger(__name__)


def _install_services(app) -> None:
    app.bot_data["news_prefilter"] = NewsPrefilter(
        stock_db=app.bot_data["stock_db"],
        event_file=NEWS_PREFILTER_EVENT_FILE,
        observation_file=NEWS_PREFILTER_OBSERVATION_FILE,
        model_file=NEWS_PREFILTER_MODEL_FILE,
        cpu_state_file=NEWS_PREFILTER_CPU_STATE_FILE,
        mode=NEWS_PREFILTER_MODE,
        event_window_hours=NEWS_PREFILTER_EVENT_WINDOW_HOURS,
        max_events=NEWS_PREFILTER_MAX_EVENTS,
        observation_retention_days=NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS,
        similarity_threshold=NEWS_PREFILTER_SIMILARITY_THRESHOLD,
        exploration_slots=NEWS_PREFILTER_EXPLORATION_SLOTS,
        selection_limit=NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
        reported_event_cooldown_hours=NEWS_PREFILTER_REPORTED_EVENT_COOLDOWN_HOURS,
        recap_bucket_hours=NEWS_PREFILTER_RECAP_BUCKET_HOURS,
        recap_max_age_hours=NEWS_PREFILTER_RECAP_MAX_AGE_HOURS,
        host_cpu=HostCpuBudget(
            NEWS_PREFILTER_HOST_CPU_FILE,
            baseline=NEWS_PREFILTER_BURST_BASELINE,
            floor_percent=NEWS_PREFILTER_BURST_FLOOR_PERCENT,
            steal_limit=NEWS_PREFILTER_HOST_STEAL_LIMIT,
        ),
        embed_dir=NEWS_PREFILTER_EMBED_DIR,
    )


def _load_average_too_high() -> bool:
    if not hasattr(os, "getloadavg"):
        return False
    try:
        return os.getloadavg()[0] >= NEWS_PREFILTER_MAX_LOAD_AVERAGE
    except OSError:
        return False


async def run_prefilter_maintenance(app) -> None:
    """새 자료 학습을 이어 수행하고 CPU 조각 사이에 우선 작업·부하를 확인한다."""
    service: NewsPrefilter | None = app.bot_data.get("news_prefilter")
    if service is None:
        return
    foreground_cpu = service.account_foreground_cpu()
    if is_burst_active():
        service.record_maintenance(reason="burst", cpu_seconds=0, trials=0, labels=0)
        logger.info("[PREFILTER] 버스트 우선 작업 진행 중 · 보정 양보")
        return
    # 예약 실행마다 한 번 잰다. 조각 사이에 다시 재면 1분 창의 steal이 0초 구간이 된다.
    host = service.check_host_cpu()
    if host is not None and host.reason:
        if host.reason == "steal":
            logger.warning("[PREFILTER] steal %.1f%% — 크레딧 소진 징후 · 보정 양보", (host.steal or 0) * 100)
        service.record_maintenance(reason=host.reason, cpu_seconds=0, trials=0, labels=0)
        return
    slice_limit = NEWS_PREFILTER_MAINTENANCE_MAX_SECONDS
    slice_used = 0.0
    trials = 0
    labels = 0
    reason = ""
    while slice_used < slice_limit:
        if is_burst_active():
            reason = "burst"
            break
        if _load_average_too_high():
            reason = "load"
            break
        chunk = min(NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS, slice_limit - slice_used)
        result = await service.optimize_chunk(chunk)
        service.record_background_cpu(result.cpu_seconds)
        slice_used += result.cpu_seconds
        trials += result.trials
        labels = result.label_count
        if result.reason:
            reason = result.reason
            break
        if result.cpu_seconds <= 0.001:
            reason = "no_work"
            break

    service.record_maintenance(
        reason=reason or "slice_complete", cpu_seconds=slice_used, trials=trials, labels=labels,
    )
    if slice_used or reason not in {"insufficient_labels", "load"}:
        status = service.cpu_status()
        logger.info(
            "[PREFILTER] 보정 CPU %.1fs · foreground %.1fs · trial %d · label %d · 오늘 보정 %.2fh%s",
            slice_used,
            foreground_cpu,
            trials,
            labels,
            float(status["used_seconds"]) / 3600,
            f" · 중단={reason}" if reason else "",
        )


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        run_prefilter_maintenance,
        trigger="interval",
        minutes=NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES,
        args=[app],
        next_run_time=now() + timedelta(minutes=5),
        id="news_prefilter_maintenance",
        max_instances=1,
        coalesce=True,
    )


FEATURE = FeatureSpec(
    key="news_prefilter",
    label="로컬 뉴스 사건 메모리·사전선별",
    requires=frozenset({"instruments", "watchlist"}),
    status_reports=(
        StatusReportSpec("prefilter", "뉴스 사전선별·학습 상태", render_prefilter_status),
    ),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=(
        "storage/bot/news_prefilter/event_memory.json",
        "storage/bot/news_prefilter/observations.jsonl",
        "storage/bot/news_prefilter/model.json",
        "storage/bot/news_prefilter/cpu_budget.json",
        "storage/bot/news_prefilter/host_cpu.json",
        "storage/bot/news_prefilter/embedding/",
    ),
)

