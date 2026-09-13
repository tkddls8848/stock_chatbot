import asyncio
import json
from datetime import timedelta

from telegram_bot.core.clock import now
from telegram_bot.state.prediction_log import PredictionLog, aggregate_stock_views


def test_aggregate_stock_views_verdicts():
    watchlist = {"600519": "귀주모태주", "09988": "알리바바"}
    entries = [
        {"ts": "2026-07-15T10:00:00", "title": "a", "sentiment": 0.5, "codes": ["600519"]},
        {"ts": "2026-07-15T11:00:00", "title": "b", "sentiment": 0.3, "codes": ["600519"]},
        {"ts": "2026-07-15T12:00:00", "title": "c", "sentiment": -0.9, "codes": ["09988"]},
        {"ts": "2026-07-15T13:00:00", "title": "d", "sentiment": 0.9, "codes": ["999999"]},
    ]
    views = aggregate_stock_views(entries, watchlist)
    assert views["600519"]["verdict"] == "up"
    assert views["600519"]["count"] == 2
    assert abs(views["600519"]["avg_sentiment"] - 0.4) < 1e-9
    assert views["09988"]["verdict"] == "down"
    assert "999999" not in views  # 관심종목 외 코드는 집계하지 않는다


def test_aggregate_neutral_band_and_invalid_sentiment():
    watchlist = {"600519": "귀주모태주"}
    entries = [
        {"ts": "2026-07-15T10:00:00", "title": "a", "sentiment": 0.3, "codes": ["600519"]},
        {"ts": "2026-07-15T11:00:00", "title": "b", "sentiment": -0.1, "codes": ["600519"]},
        {"ts": "2026-07-15T12:00:00", "title": "c", "sentiment": None, "codes": ["600519"]},
        {"ts": "2026-07-15T13:00:00", "title": "d", "sentiment": "높음", "codes": ["600519"]},
    ]
    views = aggregate_stock_views(entries, watchlist)
    assert views["600519"]["count"] == 2  # 감성이 숫자가 아닌 항목은 제외
    assert views["600519"]["verdict"] == "neutral"


def test_aggregate_recent_keeps_latest_five_newest_first():
    watchlist = {"600519": "귀주모태주"}
    entries = [
        {"ts": f"2026-07-15T1{i}:00:00", "title": f"t{i}", "sentiment": 0.5, "codes": ["600519"]}
        for i in range(7)
    ]
    view = aggregate_stock_views(entries, watchlist)["600519"]
    assert view["count"] == 7
    assert len(view["recent"]) == 5
    assert view["recent"][0]["title"] == "t6"  # 최신이 먼저


def test_record_and_snapshot_roundtrip(tmp_path):
    log = PredictionLog(tmp_path / "p.jsonl")

    async def run():
        await log.record(
            source="futu", title="제목", sentiment=0.5, impact="high", codes=["600519", ""]
        )
        return await log.snapshot(3)

    entries = asyncio.run(run())
    assert len(entries) == 1
    assert entries[0]["codes"] == ["600519"]  # 빈 코드는 저장하지 않는다
    assert entries[0]["sentiment"] == 0.5
    assert entries[0]["source"] == "futu"


def test_snapshot_skips_corrupt_lines(tmp_path):
    path = tmp_path / "p.jsonl"
    path.write_text(
        '{"ts": "2026-07-15T10:00:00", "sentiment": 0.5, "codes": ["600519"], "title": "ok"}\n'
        "깨진 줄\n"
        '{"sentiment": 0.5}\n',
        encoding="utf-8",
    )
    log = PredictionLog(path)
    entries = asyncio.run(log.snapshot(365))
    assert len(entries) == 1
    assert entries[0]["title"] == "ok"


def test_large_history_applies_retention_window_and_keeps_recent_rows(tmp_path):
    """큰 파일에서도 기간 밖 행을 반환하지 않고 최근 행을 빠뜨리지 않는다."""
    path = tmp_path / "large.jsonl"
    old_ts = (now() - timedelta(days=60)).isoformat(timespec="seconds")
    recent_ts = (now() - timedelta(hours=1)).isoformat(timespec="seconds")
    old = {
        "ts": old_ts,
        "source": "old",
        "title": "expired",
        "sentiment": -0.1,
        "impact": "low",
        "codes": ["600519"],
    }
    lines = [json.dumps(old) for _ in range(20_000)]
    for index in range(10):
        lines.append(
            json.dumps(
                {
                    **old,
                    "ts": recent_ts,
                    "source": "recent",
                    "title": f"recent-{index}",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    entries = asyncio.run(PredictionLog(path).snapshot(7))

    assert len(entries) == 10
    assert [entry["title"] for entry in entries] == [
        f"recent-{index}" for index in range(10)
    ]
