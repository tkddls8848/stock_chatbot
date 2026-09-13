import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from telegram_bot.core.clock import today as kst_today
from telegram_bot.state.market_digest import market_history_gaps
from telegram_bot.features.market_sentiment import handlers as commands
from telegram_bot.features.market_sentiment.chart import _trend_series
from telegram_bot.news.backfill import _interleave_market_days


def test_market_history_gaps_rejects_thin_series():
    markets = {
        "US": {"count": 6, "daily": [{"count": 2}, {"count": 2}, {"count": 2}]},
        "KR": {"count": 1, "daily": [{"count": 1}]},
    }

    gaps = market_history_gaps(markets, {"US", "KR"}, minimum_articles=6, minimum_days=3)

    assert gaps == {"KR": "1/6 scored articles"}


def test_market_history_gaps_rejects_single_article_days():
    """창 합계만 보면 통과하지만 매일 1건뿐인 시리즈는 출렁이기만 한다."""
    markets = {
        "KR": {"count": 7, "daily": [{"count": 1} for _ in range(7)]},
        "US": {"count": 7, "daily": [{"count": 3}, {"count": 3}, {"count": 1}]},
    }

    gaps = market_history_gaps(
        markets,
        {"US", "KR"},
        minimum_articles=6,
        minimum_days=2,
        minimum_articles_per_day=3,
    )

    assert gaps == {"KR": "0/2 days with 3+ articles"}


def test_market_history_gaps_per_day_gate_defaults_to_off():
    markets = {"KR": {"count": 6, "daily": [{"count": 1} for _ in range(6)]}}

    assert market_history_gaps(
        markets, {"KR"}, minimum_articles=6, minimum_days=3
    ) == {}


def test_trend_series_uses_sorted_real_dates():
    dates, values = _trend_series(
        [
            {"date": "2026-07-18", "avg_sentiment": 0.1},
            {"date": "2026-07-11", "avg_sentiment": -0.2},
            {"date": "2026-07-15", "avg_sentiment": 0.4},
        ]
    )

    assert [day.date().isoformat() for day in dates] == [
        "2026-07-11",
        "2026-07-15",
        "2026-07-18",
    ]
    assert values == [-0.2, 0.4, 0.1]


def test_digest_budget_is_shared_across_markets():
    """호출 예산이 정렬 순서대로 소진되면 CN이 다 먹고 KR·US 구간이 빈다."""
    days = [date(2026, 7, 4) - timedelta(days=offset) for offset in range(3)]

    ordered = _interleave_market_days({"CN", "US", "KR"}, None, days)

    assert [market for market, _ in ordered[:3]] == ["CN", "KR", "US"]
    assert len(ordered) == 9
    assert ordered[0][1] == days[0]
    assert ordered[3][1] == days[1]


def test_uneven_market_queues_do_not_drop_days():
    ordered = _interleave_market_days(
        {"CN", "US"},
        {"CN": [date(2026, 7, 4), date(2026, 7, 3)], "US": [date(2026, 7, 4)]},
        [],
    )

    assert sorted(ordered) == [
        ("CN", date(2026, 7, 3)),
        ("CN", date(2026, 7, 4)),
        ("US", date(2026, 7, 4)),
    ]


def test_backfill_day_limit_spreads_across_full_window():
    days = [date(2026, 7, 17) - timedelta(days=offset) for offset in range(23)]

    selected = commands._spread_backfill_days(days, 7)

    assert len(selected) == 7
    assert selected[0] == days[0]
    assert selected[-1] == days[-1]
    assert len(set(selected)) == 7


class _Status:
    async def edit_text(self, text):
        self.text = text

    async def delete(self):
        self.deleted = True


class _Message:
    async def reply_text(self, text):
        return _Status()

    async def reply_photo(self, **kwargs):
        self.photo = kwargs


def _ready_series(markets, today):
    return {
        market: {
            "avg_sentiment": 0.1,
            "count": 40,
            "daily": [
                {"date": today.isoformat(), "avg_sentiment": 0.1, "count": 20},
                {
                    "date": (today - timedelta(days=1)).isoformat(),
                    "avg_sentiment": 0.1,
                    "count": 20,
                },
            ],
        }
        for market in markets
    }


def test_market_command_backfills_missing_digest_days(monkeypatch):
    today = kst_today()
    target_day = today - timedelta(days=8)
    calls = []

    class StoreStub:
        async def series(self, markets, days):
            assert days == 14
            return _ready_series(("CN", "US"), today)

        async def missing_digest_days(self, markets, days):
            assert days == 14
            return {
                market: ([target_day] if market == "US" else [])
                for market in markets
            }

    async def fake_backfill(store, analyzer, semaphore, markets, queries, days_by_market, **kwargs):
        calls.append({"markets": markets, "days": days_by_market, "kwargs": kwargs})
        return {"US": 1}

    async def fake_run_non_urgent(func, *args):
        return b"chart"

    monkeypatch.setattr(commands, "market_history_gaps", lambda *a, **k: {})
    monkeypatch.setattr(commands, "backfill_market_digests", fake_backfill)
    monkeypatch.setattr(commands, "run_non_urgent", fake_run_non_urgent)

    message = _Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(
        args=["14"],
        bot_data={
            "market_digest_store": StoreStub(),
            "market_digest_analyzer": object(),
            "market_digest_semaphore": object(),
        },
    )

    asyncio.run(commands.cmd_market(update, context))

    assert len(calls) == 1
    assert calls[0]["markets"] == {"US"}
    assert calls[0]["days"]["US"] == [target_day]
    # 설정한 하루치 표본 수가 그대로 넘어가는지만 본다(값 자체는 튜닝 대상).
    assert calls[0]["kwargs"]["articles_per_day"] == commands.MARKET_DIGEST_ARTICLES_PER_DAY
    assert message.photo["caption"].startswith("국가·증시별 뉴스 감성 — 최근 14일")


def test_thirty_day_request_targets_all_markets_and_full_range(monkeypatch):
    today = kst_today()
    missing = [today - timedelta(days=offset) for offset in range(8, 30)]
    captured = {}

    class StoreStub:
        async def series(self, markets, days):
            assert days == 30
            return _ready_series(("CN", "HK", "US", "KR"), today)

        async def missing_digest_days(self, markets, days):
            assert days == 30
            return {market: list(missing) for market in markets}

    async def fake_backfill(store, analyzer, semaphore, markets, queries, days_by_market, **kwargs):
        captured["markets"] = markets
        captured["days"] = days_by_market
        return {market: 7 for market in markets}

    async def fake_run_non_urgent(func, *args):
        return b"chart"

    monkeypatch.setattr(commands, "market_history_gaps", lambda *a, **k: {})
    monkeypatch.setattr(commands, "backfill_market_digests", fake_backfill)
    monkeypatch.setattr(commands, "run_non_urgent", fake_run_non_urgent)

    message = _Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(
        args=["30"],
        bot_data={
            "market_digest_store": StoreStub(),
            "market_digest_analyzer": object(),
            "market_digest_semaphore": object(),
        },
    )

    asyncio.run(commands.cmd_market(update, context))

    assert captured["markets"] == {"CN", "HK", "US", "KR"}
    assert all(len(days) == 7 for days in captured["days"].values())
    assert all(days[0] == missing[0] for days in captured["days"].values())
    assert all(days[-1] == missing[-1] for days in captured["days"].values())


def test_caption_reports_daily_sample_size(monkeypatch):
    """표본이 얕으면 선이 출렁이므로 값만 보여 주면 신뢰도를 오해한다."""
    today = kst_today()

    class StoreStub:
        async def series(self, markets, days):
            return _ready_series(("CN", "US"), today)

        async def missing_digest_days(self, markets, days):
            return {market: [] for market in markets}

    async def fake_run_non_urgent(func, *args):
        return b"chart"

    monkeypatch.setattr(commands, "market_history_gaps", lambda *a, **k: {})
    monkeypatch.setattr(commands, "run_non_urgent", fake_run_non_urgent)

    message = _Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(
        args=["7"],
        bot_data={"market_digest_store": StoreStub()},
    )

    asyncio.run(commands.cmd_market(update, context))

    assert "하루 평균 표본 20건" in message.photo["caption"]


class _CapturingMessage:
    """마지막 상태 메시지를 남겨 실패 문구를 확인할 수 있게 한다."""

    def __init__(self, photo_error=None):
        self._photo_error = photo_error
        self.status = None
        self.photo = None

    async def reply_text(self, text):
        self.status = _Status()
        return self.status

    async def reply_photo(self, **kwargs):
        self.photo = kwargs
        if self._photo_error is not None:
            raise self._photo_error


def _run_chart(monkeypatch, message, *, render_error=None):
    today = kst_today()

    class StoreStub:
        async def series(self, markets, days):
            return _ready_series(("CN", "US"), today)

        async def missing_digest_days(self, markets, days):
            return {market: [] for market in markets}

    async def fake_run_non_urgent(func, *args):
        if render_error is not None:
            raise render_error
        return b"chart"

    monkeypatch.setattr(commands, "market_history_gaps", lambda *a, **k: {})
    monkeypatch.setattr(commands, "run_non_urgent", fake_run_non_urgent)

    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(
        args=["7"], bot_data={"market_digest_store": StoreStub()}
    )
    asyncio.run(commands.cmd_market(update, context))


def test_upload_failure_is_not_reported_as_a_rendering_failure(monkeypatch, caplog):
    """차트를 다 만든 뒤 끊긴 것을 렌더링 실패로 찍으면 matplotlib을 보게 된다.

    운영에서 실제로 발생했다. reply_photo의 ReadTimeout이 'chart rendering
    failed'로 남아 원인이 네트워크라는 사실이 로그에서 지워졌다.
    """
    message = _CapturingMessage(photo_error=TimeoutError("Timed out"))

    with caplog.at_level("ERROR"):
        _run_chart(monkeypatch, message)

    assert "chart upload failed" in caplog.text
    assert "chart rendering failed" not in caplog.text
    assert "전송하지 못했습니다" in message.status.text


def test_rendering_failure_still_says_rendering(monkeypatch, caplog):
    message = _CapturingMessage()

    with caplog.at_level("ERROR"):
        _run_chart(monkeypatch, message, render_error=RuntimeError("font"))

    assert "chart rendering failed" in caplog.text
    assert "chart upload failed" not in caplog.text
    assert "만들지 못했습니다" in message.status.text
    assert message.photo is None


def test_chart_upload_outlives_the_five_second_default(monkeypatch):
    """기본 5초로는 Lightsail에서 올린 뒤 응답을 기다리다 끊긴다."""
    message = _CapturingMessage()

    _run_chart(monkeypatch, message)

    assert message.photo["read_timeout"] >= 20
    assert message.photo["write_timeout"] >= 20


def test_missing_store_reports_instead_of_crashing():
    message = _Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(args=[], bot_data={})

    asyncio.run(commands.cmd_market(update, context))
