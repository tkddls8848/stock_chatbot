"""하루 CPU·요청 예산이 실제로 순회를 막는지.

이 파일이 지키는 규칙은 하나다. **총량을 막는 것은 이 코드뿐이다.**

systemd의 `Nice=10`·`CPUWeight=20`은 **경쟁이 있을 때만** 양보시킨다. 새벽에
봇이 한가하면 순회 프로세스가 CPU를 100% 쓰고 그만큼 Lightsail 버스트
크레딧이 탄다 — 아끼려고 만든 그 크레딧이다. timer 주기는 시작 횟수만 정하지
한 번이 얼마나 오래 도는지는 정하지 않는다.

`POLYMARKET_WEB_MAX_DAILY_*`는 계획서가 예산이라고 적어 두고도 오래 아무
코드도 읽지 않는 상수였다. 이 테스트가 그 상태로 되돌아가는 것을 막는다.
"""

import json
from datetime import timedelta

import pytest

from shared.core.clock import now
from shared.core.config import (
    POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS,
    POLYMARKET_WEB_MAX_DAILY_REQUESTS,
)
from web.polymarket.refresh import over_budget, recent_usage


def _sample(hours_ago, *, cpu=100.0, requests=200):
    return {
        "at": (now() - timedelta(hours=hours_ago)).isoformat(),
        "cpu_seconds": cpu,
        "requests": requests,
    }


def test_usage_counts_only_the_last_24_hours():
    """개수가 아니라 시각으로 자른다 — 주기가 바뀌어도 하루는 하루다."""
    previous = {"cpu_samples": [_sample(1), _sample(23), _sample(25), _sample(100)]}

    usage = recent_usage(previous)

    assert len(usage["samples"]) == 2
    assert usage["cpu_seconds"] == 200.0
    assert usage["requests"] == 400


def test_usage_ignores_junk_rows():
    previous = {"cpu_samples": [_sample(1), "junk", {"cpu_seconds": 5}, {"at": "nope"}]}

    usage = recent_usage(previous)

    assert usage["cpu_seconds"] == 100.0


def test_a_missing_history_is_zero_not_a_crash():
    assert recent_usage({}) == {"samples": [], "cpu_seconds": 0.0, "requests": 0}


def test_naive_timestamps_are_still_compared_correctly():
    """전환 이전에 쓴 status에는 오프셋이 없다. 그냥 비교하면 TypeError로 죽는다."""
    stamp = (now() - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    previous = {"cpu_samples": [{"at": stamp, "cpu_seconds": 10.0, "requests": 5}]}

    assert recent_usage(previous)["cpu_seconds"] == 10.0


def test_under_budget_does_not_skip():
    assert over_budget({"cpu_seconds": 0.0, "requests": 0}) == ""
    assert over_budget(
        {
            "cpu_seconds": POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS - 1,
            "requests": POLYMARKET_WEB_MAX_DAILY_REQUESTS - 1,
        }
    ) == ""


@pytest.mark.parametrize(
    ("cpu", "requests", "expected"),
    [
        (POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS, 0, "cpu"),
        (POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS + 100, 0, "cpu"),
        (0.0, POLYMARKET_WEB_MAX_DAILY_REQUESTS, "requests"),
        (0.0, POLYMARKET_WEB_MAX_DAILY_REQUESTS + 1, "requests"),
    ],
)
def test_over_budget_names_which_limit_was_hit(cpu, requests, expected):
    assert over_budget({"cpu_seconds": cpu, "requests": requests}) == expected


def test_the_budget_skips_the_walk_and_leaves_current_alone(tmp_path, monkeypatch):
    """예산을 넘긴 주기는 API를 부르지 않고, current도 건드리지 않는다."""
    from web.polymarket import refresh as module

    root = tmp_path
    (root / "status.json").write_text(
        json.dumps(
            {"cpu_samples": [_sample(1, cpu=POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS + 1)]}
        ),
        encoding="utf-8",
    )
    (root / "current.json").write_text('{"generation_id": "keep-me"}', encoding="utf-8")

    def _explode(*_args, **_kwargs):
        raise AssertionError("예산을 넘겼는데 순회를 시작했다")

    monkeypatch.setattr(module, "EventsClient", _explode)

    result = module.refresh(root=root)

    assert result["state"] == "skipped_budget"
    assert result["reason"] == "cpu"
    # 직전 generation이 그대로 남아 화면이 산다.
    assert (root / "current.json").read_text(encoding="utf-8") == '{"generation_id": "keep-me"}'
    status = json.loads((root / "status.json").read_text(encoding="utf-8"))
    assert status["last_result"] == "skipped_budget"
    assert status["skipped_reason"] == "cpu"
