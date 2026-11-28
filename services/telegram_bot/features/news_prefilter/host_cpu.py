"""호스트 CPU 기록으로 Lightsail 버스트 크레딧 잔고를 추정해, 남는 크레딧을 주기적으로 쓴다.

Lightsail `medium_3_0`(2 vCPU)의 기준선은 vCPU당 20%다. 기준선 아래로 쓰면 시간당 4.17%씩
크레딧이 쌓이고 24시간이면 잔고가 100%로 가득 차 그 뒤로는 버려진다. 운영자 지시(2026-11-28)는
"계속 조금씩"이 아니라 **쌓인 잔고를 주기적으로 몰아 쓰는 것**이다 — 배경 작업은 잔고 추정이
하한(`floor_percent`) 위일 때 전속력으로 돌고, 하한에 닿으면 멈춰 다시 쌓일 때까지 기다린다.

추정은 `/proc/stat`의 지난 24시간 기록을 잔고 0에서 시작해 적분한다. 실제 잔고는 24시간 전에
0 이상이었으므로 이 값은 **하한**이다 — 틀려도 덜 쓰는 쪽으로 틀린다. 웹·폴리마켓 one-shot·쇼츠도
함께 센다. AWS 자격증명이 필요 없다.

steal은 하이퍼바이저가 CPU를 회수한 시간이다. 크레딧이 바닥나 기준선으로 묶이면 가장 먼저 여기서
드러난다(재기동 직후 2026-11-27 13:40에 62.7%를 기록했다). 직전 측정 구간의 steal이 상한을 넘으면
잔고 추정과 무관하게 양보한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from services.telegram_bot.core.clock import now
from services.telegram_bot.core.storage import write_json_atomic

_PROC_STAT = Path("/proc/stat")
# 잔고 100%는 기준선 24시간 적립분이다(시간당 4.17%).
_FULL_BALANCE_HOURS = 24.0
# 잔고 추정용 표본 간격. 1분마다 전부 남기면 상태 파일이 하루 1,440줄로 불어난다.
_SNAPSHOT_SPACING_SECONDS = 600


@dataclass(frozen=True)
class CpuCounters:
    busy: int
    total: int
    steal: int


@dataclass(frozen=True)
class HostCpuVerdict:
    reason: str  # "" = 진행 가능, "credit" = 잔고 추정이 하한 아래, "steal" = 크레딧 소진 징후
    balance_percent: float | None
    usage: float | None  # 직전 측정 구간의 호스트 사용률(0~1)
    steal: float | None
    window_hours: float


def read_proc_stat(path: Path = _PROC_STAT) -> CpuCounters | None:
    """첫 줄(전체 CPU) jiffies. Linux가 아니거나 읽을 수 없으면 None."""
    try:
        first = path.read_text(encoding="ascii").splitlines()[0].split()
    except (OSError, IndexError, UnicodeDecodeError):
        return None
    if not first or first[0] != "cpu":
        return None
    try:
        values = [int(value) for value in first[1:9]]
    except ValueError:
        return None
    values += [0] * (8 - len(values))
    user, nice, system, idle, iowait, irq, softirq, steal = values
    # guest 시간은 user에 이미 들어 있다. steal은 우리가 쓴 시간이 아니므로 busy에서 뺀다.
    busy = user + nice + system + irq + softirq
    return CpuCounters(busy=busy, total=busy + idle + iowait + steal, steal=steal)


def estimate_balance_percent(
    points: list[tuple[float, CpuCounters]], baseline: float,
) -> float:
    """시각순 표본을 잔고 0에서 적분한 잔고 하한(0~100). 구간마다 상·하한에서 자른다."""
    balance = 0.0
    for (at_old, old), (at_new, new) in zip(points, points[1:]):
        hours = (at_new - at_old) / 3600
        usage = _ratio(new, old, "busy")
        if hours <= 0 or usage is None:
            continue
        balance += (1.0 - usage / baseline) * hours / _FULL_BALANCE_HOURS * 100
        balance = min(100.0, max(0.0, balance))
    return balance


def _ratio(new: CpuCounters, old: CpuCounters, field: str) -> float | None:
    elapsed = new.total - old.total
    if elapsed <= 0:
        return None
    return max(0.0, (getattr(new, field) - getattr(old, field)) / elapsed)


class HostCpuBudget:
    def __init__(
        self,
        state_file: Path,
        *,
        baseline: float,
        floor_percent: float,
        steal_limit: float,
        reader=read_proc_stat,
        persist: bool = True,
    ):
        # 표본 파일의 쓰는 쪽은 봇 하나다. 임베딩 one-shot은 persist=False로 읽기만 한다.
        self._persist_enabled = persist
        self._state_file = state_file
        self._baseline = min(1.0, max(0.01, float(baseline)))
        self._floor = min(100.0, max(0.0, float(floor_percent)))
        self._steal_limit = max(0.0, float(steal_limit))
        self._window_seconds = _FULL_BALANCE_HOURS * 3600
        self._reader = reader
        self._snapshots: list[tuple[float, CpuCounters]] = self._load()
        self._previous: tuple[float, CpuCounters] | None = None
        self._last: HostCpuVerdict | None = None

    def _load(self) -> list[tuple[float, CpuCounters]]:
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            rows = raw["snapshots"]
            return [
                (float(row["at"]), CpuCounters(int(row["busy"]), int(row["total"]), int(row["steal"])))
                for row in rows
            ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return []

    def _persist(self) -> None:
        write_json_atomic(self._state_file, {
            "snapshots": [
                {"at": at, "busy": c.busy, "total": c.total, "steal": c.steal}
                for at, c in self._snapshots
            ],
        })

    def check(self) -> HostCpuVerdict | None:
        """매 예약 실행에서 한 번 부른다. 측정할 수 없는 환경이면 None(제한 없음)."""
        current = self._reader()
        if current is None:
            self._last = None
            return None
        at = now().timestamp()
        # 호스트 재부팅으로 카운터가 줄었으면 지난 표본은 쓸 수 없다.
        if self._snapshots and current.total < self._snapshots[-1][1].total:
            self._snapshots = []
        if self._previous is not None and current.total < self._previous[1].total:
            self._previous = None
        floor = at - self._window_seconds
        self._snapshots = [row for row in self._snapshots if row[0] >= floor]
        if not self._snapshots or at - self._snapshots[-1][0] >= _SNAPSHOT_SPACING_SECONDS:
            self._snapshots.append((at, current))
            if self._persist_enabled:
                self._persist()

        oldest_at = self._snapshots[0][0]
        points = list(self._snapshots)
        if points[-1][1] != current:
            points.append((at, current))
        balance = estimate_balance_percent(points, self._baseline)
        previous = self._previous
        usage = _ratio(current, previous[1], "busy") if previous else None
        steal = _ratio(current, previous[1], "steal") if previous else None
        self._previous = (at, current)

        reason = ""
        if steal is not None and steal > self._steal_limit:
            reason = "steal"
        elif balance < self._floor:
            reason = "credit"
        self._last = HostCpuVerdict(
            reason=reason, balance_percent=balance, usage=usage, steal=steal,
            window_hours=round((at - oldest_at) / 3600, 2),
        )
        return self._last

    def status(self) -> dict[str, float | str | None]:
        last = self._last
        return {
            "floor_percent": self._floor,
            "steal_limit": self._steal_limit,
            "balance_percent": None if last is None else last.balance_percent,
            "usage": None if last is None else last.usage,
            "steal": None if last is None else last.steal,
            "window_hours": None if last is None else last.window_hours,
            "reason": "" if last is None else last.reason,
        }
