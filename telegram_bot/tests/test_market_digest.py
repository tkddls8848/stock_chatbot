import asyncio
import json
from datetime import timedelta

import pytest

from telegram_bot.core.clock import today as kst_today
from telegram_bot.state.market_digest import MarketDigestStore, digest_key


def _store(tmp_path, retention_days=30):
    return MarketDigestStore(tmp_path / "daily_digest.json", retention_days)


def test_final_days_are_not_recomputed(tmp_path):
    """확정된 날은 다시 계산 대상이 되지 않아야 값이 고정된다."""
    store = _store(tmp_path)
    today = kst_today()
    yesterday = today - timedelta(days=1)

    async def exercise():
        await store.put(
            "KR", yesterday, sentiment=0.4, article_count=20, final=True
        )
        await store.put("KR", today, sentiment=0.1, article_count=6, final=False)
        return await store.missing_digest_days({"KR"}, 3)

    missing = asyncio.run(exercise())

    # 어제는 확정됐으므로 빠지고, 오늘과 그제는 남는다.
    assert yesterday not in missing["KR"]
    assert today in missing["KR"]
    assert today - timedelta(days=2) in missing["KR"]


def test_sentiment_is_stable_across_reloads(tmp_path):
    """프로세스를 다시 띄워도 확정 구간의 값이 같아야 한다."""
    today = kst_today()
    day = today - timedelta(days=2)

    async def write():
        store = _store(tmp_path)
        await store.put("US", day, sentiment=-0.35, article_count=20, final=True)

    asyncio.run(write())

    reopened = _store(tmp_path)
    series = asyncio.run(reopened.series({"US"}, 7))

    assert series["US"]["daily"][0]["avg_sentiment"] == -0.35
    assert asyncio.run(reopened.missing_digest_days({"US"}, 7))["US"] != []
    assert day not in asyncio.run(reopened.missing_digest_days({"US"}, 7))["US"]


def test_retention_drops_entries_beyond_window(tmp_path):
    store = _store(tmp_path, retention_days=30)
    today = kst_today()

    async def exercise():
        await store.put("CN", today - timedelta(days=5), sentiment=0.2, article_count=20)
        await store.put("CN", today - timedelta(days=40), sentiment=0.9, article_count=20)
        return await store.stats()

    stats = asyncio.run(exercise())

    assert stats["entries"] == 1
    saved = json.loads((tmp_path / "daily_digest.json").read_text(encoding="utf-8"))
    assert digest_key("CN", today - timedelta(days=5)) in saved
    assert digest_key("CN", today - timedelta(days=40)) not in saved


def test_days_outside_retention_are_not_queued(tmp_path):
    """30일 보존인데 그보다 오래된 날을 계산하면 저장하자마자 버려진다."""
    store = _store(tmp_path, retention_days=30)

    missing = asyncio.run(store.missing_digest_days({"KR"}, 60))

    assert len(missing["KR"]) == 30
    assert min(missing["KR"]) == kst_today() - timedelta(days=29)


def test_series_shape_matches_chart_contract(tmp_path):
    """차트·게이트가 쓰던 aggregate_market_sentiment와 같은 모양이어야 한다."""
    store = _store(tmp_path)
    today = kst_today()

    async def exercise():
        await store.put(
            "KR", today - timedelta(days=1), sentiment=0.5, article_count=20, final=True
        )
        await store.put(
            "KR", today - timedelta(days=2), sentiment=-0.1, article_count=18, final=True
        )
        return await store.series({"KR"}, 7)

    series = asyncio.run(exercise())

    assert series["KR"]["avg_sentiment"] == pytest.approx(0.2)
    assert series["KR"]["count"] == 38
    assert [point["date"] for point in series["KR"]["daily"]] == [
        (today - timedelta(days=2)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    ]
    assert series["KR"]["daily"][0]["count"] == 18


def test_sentiment_is_clamped(tmp_path):
    store = _store(tmp_path)
    today = kst_today()

    asyncio.run(store.put("US", today, sentiment=4.2, article_count=20))
    series = asyncio.run(store.series({"US"}, 2))

    assert series["US"]["daily"][0]["avg_sentiment"] == 1.0


def test_corrupt_cache_starts_empty(tmp_path):
    path = tmp_path / "daily_digest.json"
    path.write_text("{not json", encoding="utf-8")

    store = MarketDigestStore(path, 30)

    assert asyncio.run(store.stats())["entries"] == 0
