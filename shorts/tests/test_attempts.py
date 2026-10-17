"""예약 제작은 하루 두 번까지만 시도하고, 포기한 날은 봇이 한 번 알리도록 남긴다."""

from datetime import datetime
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polymarket_shorts import attempts
from polymarket_shorts.review import ReviewError

SEOUL = ZoneInfo("Asia/Seoul")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(attempts, "now", lambda: datetime(2026, 10, 13, 20, tzinfo=SEOUL))
    monkeypatch.setattr(attempts, "datetime", SimpleNamespace(now=lambda tz: datetime(2026, 10, 13, 20, tzinfo=tz)))
    return SimpleNamespace(output_dir=tmp_path, timezone=SEOUL)


def _record(settings):
    return json.loads((settings.output_dir / "2026-10-13" / attempts.ATTEMPTS_FILE).read_text(encoding="utf-8"))


def test_second_failure_gives_up_and_stops_retrying(settings):
    def broken():
        raise RuntimeError("403 음성 서버")

    # 첫 실패는 원래 예외로 끝나 systemd가 15분 뒤 다시 띄운다.
    with pytest.raises(RuntimeError, match="403"):
        attempts.run(settings, broken)
    assert attempts.failures(settings) == []
    # 두 번째 실패는 정상 종료로 재시작을 끊는다.
    assert attempts.run(settings, broken)["status"] == "gave_up"
    calls = []
    assert attempts.run(settings, lambda: calls.append(1) or {})["status"] == "gave_up"
    assert calls == []
    assert [row["attempts"] for row in attempts.failures(settings)] == [2]
    assert attempts.failures(settings)[0]["stage"] == "쇼츠"
    assert "403" in attempts.failures(settings)[0]["error"]

    attempts.acknowledge(settings, "2026-10-13")
    assert attempts.failures(settings) == []
    assert _record(settings)["notified_at"]


def test_attempts_killed_without_a_result_still_count(settings):
    root = settings.output_dir / "2026-10-13"
    root.mkdir()
    (root / attempts.ATTEMPTS_FILE).write_text(json.dumps({"date": "2026-10-13", "attempts": [
        {"started_at": "x", "error": "a"}, {"started_at": "y"}]}), encoding="utf-8")
    calls = []
    assert attempts.run(settings, lambda: calls.append(1) or {})["status"] == "gave_up"
    assert calls == [] and "제한 시간" in attempts.failures(settings)[0]["error"]


def test_a_successful_day_is_never_cut_off_by_later_runs(settings):
    calls = []
    for _ in range(3):
        attempts.run(settings, lambda: calls.append(1) or {"status": "already_produced"})
    assert len(calls) == 3 and len(_record(settings)["attempts"]) == 1


def test_acknowledge_only_takes_a_production_date(settings):
    with pytest.raises(ReviewError):
        attempts.acknowledge(settings, "../state")
