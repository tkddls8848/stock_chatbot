"""번역 전 로컬 뉴스 사건 메모리·사전선별 기능 선언."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from shared.core.clock import now
from shared.core.config import (
    NEWS_GLOBAL_LIMIT,
    NEWS_PREFILTER_CALIBRATION_DAILY_BUDGET_SECONDS,
    NEWS_PREFILTER_CPU_STATE_FILE,
    NEWS_PREFILTER_EVENT_FILE,
    NEWS_PREFILTER_EVENT_WINDOW_HOURS,
    NEWS_PREFILTER_EXPLORATION_SLOTS,
    NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS,
    NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES,
    NEWS_PREFILTER_LIGHTSAIL_VCPUS,
    NEWS_PREFILTER_MAX_EVENTS,
    NEWS_PREFILTER_MAX_LOAD_AVERAGE,
    NEWS_PREFILTER_MODE,
    NEWS_PREFILTER_MODEL_FILE,
    NEWS_PREFILTER_OBSERVATION_FILE,
    NEWS_PREFILTER_OBSERVATION_RETENTION_DAYS,
    NEWS_PREFILTER_SIMILARITY_THRESHOLD,
    NEWS_PREFILTER_TARGET_CPU_UTILIZATION,
    NEWS_PREFILTER_TRANSLATED_EVENT_COOLDOWN_HOURS,
)
from shared.core.workers import is_burst_active, wait_for_urgent_idle
from telegram_bot.features.base import FeatureSpec, StatusReportSpec
from telegram_bot.features.news_prefilter.report import render_prefilter_status
from telegram_bot.features.news_prefilter.service import NewsPrefilter

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
        translate_limit=NEWS_GLOBAL_LIMIT,
        translated_event_cooldown_hours=NEWS_PREFILTER_TRANSLATED_EVENT_COOLDOWN_HOURS,
        daily_cpu_budget_seconds=NEWS_PREFILTER_CALIBRATION_DAILY_BUDGET_SECONDS,
    )


def _load_average_too_high() -> bool:
    if not hasattr(os, "getloadavg"):
        return False
    try:
        return os.getloadavg()[0] >= NEWS_PREFILTER_MAX_LOAD_AVERAGE
    except OSError:
        return False


def _maintenance_slice_seconds(foreground_cpu_seconds: float) -> float:
    """직전 주기의 필수 CPU를 뺀 뒤 9% 목표 안에서 쓸 수 있는 보정 CPU초."""
    cycle_seconds = NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES * 60
    target = (
        cycle_seconds
        * NEWS_PREFILTER_LIGHTSAIL_VCPUS
        * NEWS_PREFILTER_TARGET_CPU_UTILIZATION
    )
    return max(0.0, target - max(0.0, foreground_cpu_seconds))


async def run_prefilter_maintenance(app) -> None:
    """짧은 CPU 조각 사이마다 긴급 뉴스·부하·일일 예산을 다시 확인한다."""
    service: NewsPrefilter | None = app.bot_data.get("news_prefilter")
    if service is None:
        return
    foreground_cpu = service.account_foreground_cpu()
    if is_burst_active():
        logger.info("[PREFILTER] 버스트 우선 작업 진행 중 · 보정 양보")
        return
    slice_limit = _maintenance_slice_seconds(foreground_cpu)
    slice_used = 0.0
    trials = 0
    labels = 0
    reason = ""
    while slice_used < slice_limit:
        if is_burst_active():
            reason = "burst"
            break
        if not await wait_for_urgent_idle("뉴스 사전선별 보정", timeout=0):
            reason = "urgent"
            break
        if _load_average_too_high():
            reason = "load"
            break
        remaining = service.remaining_cpu_seconds
        if remaining <= 0:
            reason = "budget"
            break
        chunk = min(
            NEWS_PREFILTER_MAINTENANCE_CHUNK_SECONDS,
            slice_limit - slice_used,
            remaining,
        )
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

    if slice_used or reason not in {"insufficient_labels", "load"}:
        status = service.cpu_status()
        logger.info(
            "[PREFILTER] 보정 CPU %.1fs · foreground %.1fs · trial %d · label %d · 남은 예산 %.2fh%s",
            slice_used,
            foreground_cpu,
            trials,
            labels,
            float(status["remaining_seconds"]) / 3600,
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
        StatusReportSpec("prefilter", "뉴스 사전선별 섀도 비교", render_prefilter_status),
    ),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=(
        "data/news_prefilter/event_memory.json",
        "data/news_prefilter/observations.jsonl",
        "data/news_prefilter/model.json",
        "data/news_prefilter/cpu_budget.json",
    ),
)

