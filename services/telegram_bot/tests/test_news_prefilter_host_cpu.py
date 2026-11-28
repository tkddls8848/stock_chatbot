"""버스트 크레딧 조절기: /proc/stat 기록으로 잔고 하한을 추정하고 주기적으로 몰아 쓴다."""

from datetime import datetime, timedelta, timezone

from services.telegram_bot.features.news_prefilter import host_cpu as host_cpu_module
from services.telegram_bot.features.news_prefilter.host_cpu import (
    CpuCounters,
    HostCpuBudget,
    estimate_balance_percent,
    read_proc_stat,
)

HOUR = 3600.0


def test_read_proc_stat_excludes_steal_from_busy(tmp_path):
    stat = tmp_path / "stat"
    # user nice system idle iowait irq softirq steal guest guest_nice
    stat.write_text("cpu  100 10 20 800 5 0 5 60 0 0\ncpu0 1 2 3\n", encoding="ascii")
    assert read_proc_stat(stat) == CpuCounters(busy=135, total=1000, steal=60)


def test_read_proc_stat_missing_file_means_unmeasured(tmp_path):
    assert read_proc_stat(tmp_path / "nope") is None


def _hourly(usages):
    """시간마다 주어진 사용률로 쓴 누적 카운터(시간당 total 1000)."""
    points, busy = [(0.0, CpuCounters(0, 0, 0))], 0
    for hour, usage in enumerate(usages, start=1):
        busy += int(usage * 1000)
        points.append((hour * HOUR, CpuCounters(busy, hour * 1000, 0)))
    return points


def test_idle_day_fills_the_balance_and_caps_at_full():
    assert abs(estimate_balance_percent(_hourly([0.0] * 12), 0.2) - 50.0) < 1e-9
    assert estimate_balance_percent(_hourly([0.0] * 30), 0.2) == 100.0


def test_running_at_baseline_keeps_balance_and_full_speed_drains_it():
    assert estimate_balance_percent(_hourly([0.2] * 24), 0.2) == 0.0
    # 하루 대부분 2%로 쉬다가 한 시간 100%로 몰아 쓰면 잔고가 약 16.7%p(4시간분) 준다.
    rested = estimate_balance_percent(_hourly([0.02] * 23), 0.2)
    burst = estimate_balance_percent(_hourly([0.02] * 23 + [1.0]), 0.2)
    assert abs((rested - burst) - (4 / 24 * 100)) < 1e-6


class _Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 28, tzinfo=timezone.utc)

    def __call__(self):
        return self.at


def _budget(tmp_path, monkeypatch, readings, clock=None):
    clock = clock or _Clock()
    monkeypatch.setattr(host_cpu_module, "now", clock)
    feed = iter(readings)
    budget = HostCpuBudget(
        tmp_path / "host_cpu.json", baseline=0.2, floor_percent=30.0, steal_limit=0.02,
        reader=lambda: next(feed),
    )
    return budget, clock


def test_waits_until_balance_accrues_then_bursts_until_floor(tmp_path, monkeypatch):
    readings = [CpuCounters(0, 0, 0)]
    for hour in range(1, 13):                       # 12시간 유휴 → 잔고 50%
        readings.append(CpuCounters(0, hour * 1000, 0))
    for step in range(1, 5):                        # 그 뒤 매시간 100%
        readings.append(CpuCounters(step * 1000, (12 + step) * 1000, 0))
    budget, clock = _budget(tmp_path, monkeypatch, readings)
    assert budget.check().reason == "credit"        # 기록 0시간: 잔고 하한 0
    verdicts = []
    for _ in range(16):
        clock.at += timedelta(hours=1)
        verdicts.append(budget.check())
    assert verdicts[6].reason == "credit"            # 7시간 ≈ 29%
    assert verdicts[7].reason == ""                  # 8시간 ≈ 33%: 몰아 쓰기 시작
    assert verdicts[12].reason == ""                 # 1시간 100% → 50 - 16.7 = 33%
    assert verdicts[13].reason == "credit"           # 2시간째에 하한 아래
    assert verdicts[13].usage == 1.0


def test_steal_spike_yields_even_with_full_balance(tmp_path, monkeypatch):
    readings = [CpuCounters(0, 0, 0)] + [CpuCounters(0, h * 1000, 0) for h in range(1, 25)]
    readings.append(CpuCounters(10, 26000, 50))      # 직전 구간 steal 5%
    budget, clock = _budget(tmp_path, monkeypatch, readings)
    for _ in range(25):
        budget.check()
        clock.at += timedelta(hours=1)
    verdict = budget.check()
    assert verdict.reason == "steal" and verdict.balance_percent > 90


def test_history_survives_bot_restart_and_drops_after_host_reboot(tmp_path, monkeypatch):
    clock = _Clock()
    budget, _ = _budget(tmp_path, monkeypatch, [CpuCounters(0, 0, 0)], clock)
    budget.check()
    clock.at += timedelta(hours=12)
    restarted, _ = _budget(tmp_path, monkeypatch, [CpuCounters(0, 12000, 0)], clock)
    verdict = restarted.check()
    assert verdict.reason == "" and abs(verdict.balance_percent - 50.0) < 1e-6

    clock.at += timedelta(minutes=1)
    rebooted, _ = _budget(tmp_path, monkeypatch, [CpuCounters(5, 10, 0)], clock)
    verdict = rebooted.check()                       # 카운터 감소 = 호스트 재부팅 → 기록 폐기
    assert verdict.reason == "credit" and verdict.balance_percent == 0.0
