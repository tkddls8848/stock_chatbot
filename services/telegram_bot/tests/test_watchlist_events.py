import asyncio

from services.telegram_bot.watchlist.events import WatchlistEventLog


def test_event_log_record_and_snapshot(tmp_path):
    log = WatchlistEventLog(tmp_path / "events.json")

    async def run():
        await log.record("add", "600519", "귀주모태주", 1700.0, "리서치 적용")
        await log.record("remove", "09988", "알리바바", 80.0, "수동 삭제")
        return await log.snapshot(lookback_days=7)

    events = asyncio.run(run())
    assert len(events) == 2
    assert events[0]["event"] == "add"
    assert events[1]["price"] == 80.0

    # 파일에서 다시 읽어도 유지된다.
    reloaded = WatchlistEventLog(tmp_path / "events.json")
    events = asyncio.run(reloaded.snapshot(lookback_days=7))
    assert len(events) == 2
