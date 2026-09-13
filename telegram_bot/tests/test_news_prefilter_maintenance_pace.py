"""평시 9% CPU 예산과 고가치 버스트 우선순위 회귀 테스트."""

import asyncio

import pytest

from telegram_bot.core import workers
from telegram_bot.core.config import (
    NEWS_PREFILTER_LIGHTSAIL_VCPUS,
    NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES,
    NEWS_PREFILTER_TARGET_CPU_UTILIZATION,
)
from telegram_bot.features.news_prefilter import feature as prefilter_feature


def test_idle_cycle_uses_nine_percent_of_instance_capacity():
    expected = (
        NEWS_PREFILTER_MAINTENANCE_INTERVAL_MINUTES
        * 60
        * NEWS_PREFILTER_LIGHTSAIL_VCPUS
        * NEWS_PREFILTER_TARGET_CPU_UTILIZATION
    )

    assert NEWS_PREFILTER_TARGET_CPU_UTILIZATION == 0.09
    assert prefilter_feature._maintenance_slice_seconds(0.0) == pytest.approx(expected)


def test_foreground_cpu_is_deducted_from_calibration_slice():
    full = prefilter_feature._maintenance_slice_seconds(0.0)

    assert prefilter_feature._maintenance_slice_seconds(2.5) == pytest.approx(full - 2.5)
    assert prefilter_feature._maintenance_slice_seconds(full + 1.0) == 0.0


def test_burst_phase_marks_high_value_work_until_it_finishes():
    async def exercise():
        assert not workers.is_burst_active()
        async with workers.burst_phase("test"):
            assert workers.is_burst_active()
            async with workers.burst_phase("nested"):
                assert workers.is_burst_active()
            assert workers.is_burst_active()
        assert not workers.is_burst_active()

    asyncio.run(exercise())


def test_prefilter_yields_without_optimizing_during_burst(monkeypatch):
    class _Service:
        def __init__(self):
            self.optimized = False

        def account_foreground_cpu(self):
            return 1.0

        async def optimize_chunk(self, _seconds):
            self.optimized = True

    service = _Service()
    app = type("App", (), {"bot_data": {"news_prefilter": service}})()
    monkeypatch.setattr(prefilter_feature, "is_burst_active", lambda: True)

    asyncio.run(prefilter_feature.run_prefilter_maintenance(app))

    assert not service.optimized
