"""학습 작업량과 고가치 버스트 우선순위 회귀 테스트."""

import asyncio


from telegram_bot.core import workers
from telegram_bot.features.news_prefilter import feature as prefilter_feature


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

        def record_maintenance(self, **status):
            self.status = status

        async def optimize_chunk(self, _seconds):
            self.optimized = True

    service = _Service()
    app = type("App", (), {"bot_data": {"news_prefilter": service}})()
    monkeypatch.setattr(prefilter_feature, "is_burst_active", lambda: True)

    asyncio.run(prefilter_feature.run_prefilter_maintenance(app))

    assert not service.optimized


def test_maintenance_is_driven_by_work_not_old_daily_or_foreground_caps(monkeypatch):
    from types import SimpleNamespace
    from telegram_bot.features.news_prefilter.optimizer import OptimizationResult

    class Service:
        calls = 0
        used = 20000.0  # 이전 일일 15,552초를 이미 넘겼어도 새 자료를 학습한다.

        def account_foreground_cpu(self):
            return 100.0

        async def optimize_chunk(self, seconds):
            self.calls += 1
            assert seconds <= 2.0
            return OptimizationResult(1.0, 1, 180, None,
                                      "search_complete" if self.calls == 3 else "")

        def record_background_cpu(self, seconds):
            self.used += seconds

        def cpu_status(self):
            return {"used_seconds": self.used}

        def record_maintenance(self, **status):
            self.status = status

    async def idle(*_args, **_kwargs):
        return True

    monkeypatch.setattr(prefilter_feature, "is_burst_active", lambda: False)
    monkeypatch.setattr(prefilter_feature, "_load_average_too_high", lambda: False)
    monkeypatch.setattr(prefilter_feature, "wait_for_urgent_idle", idle)
    service = Service()
    asyncio.run(prefilter_feature.run_prefilter_maintenance(SimpleNamespace(bot_data={"news_prefilter": service})))
    assert service.calls == 3
    assert service.status["reason"] == "search_complete"
    assert service.used == 20003
