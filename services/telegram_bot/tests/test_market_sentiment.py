import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

from services.telegram_bot.core.clock import today as kst_today
from services.telegram_bot.state.market_digest import market_history_gaps
from services.telegram_bot.features.market_sentiment import handlers as commands
from services.telegram_bot.features.market_sentiment import refresh
from services.telegram_bot.features.market_sentiment.chart import _trend_series
from services.telegram_bot.news.backfill import _interleave_market_days


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

    selected = refresh._spread_backfill_days(days, 7)

    assert len(selected) == 7
    assert selected[0] == days[0]
    assert selected[-1] == days[-1]
    assert len(set(selected)) == 7


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


class _Chart:
    def getvalue(self):
        return b"chart"


def _app(store):
    return SimpleNamespace(bot_data={
        "market_digest_store": store,
        "market_digest_analyzer": object(),
        "market_digest_semaphore": object(),
    })


def _quiet(monkeypatch, published, *, gaps=None, render_error=None):
    """차트 렌더링과 웹 게시를 가짜로 바꾼다. 실제 storage/public에 쓰지 않는다."""
    from services.telegram_bot import publish as export

    async def fake_run_non_urgent(func, *args):
        if func is refresh.render_market_chart:
            if render_error is not None:
                raise render_error
            return _Chart()
        return func(*args)

    monkeypatch.setattr(refresh, "market_history_gaps", lambda *a, **k: gaps or {})
    monkeypatch.setattr(refresh, "run_non_urgent", fake_run_non_urgent)
    monkeypatch.setattr(export, "publish_market", lambda image, ready, days: published.append((image, set(ready), days)))


def test_refresh_backfills_missing_digest_days_and_publishes(monkeypatch):
    today = kst_today()
    target_day = today - timedelta(days=8)
    calls, published = [], []

    class StoreStub:
        async def series(self, markets, days):
            assert days == 14
            return _ready_series(("CN", "US"), today)

        async def missing_digest_days(self, markets, days):
            assert days == 14
            return {market: ([target_day] if market == "US" else []) for market in markets}

    async def fake_backfill(store, analyzer, semaphore, markets, queries, days_by_market, **kwargs):
        calls.append({"markets": markets, "days": days_by_market, "kwargs": kwargs})

    _quiet(monkeypatch, published)
    monkeypatch.setattr(refresh, "backfill_market_digests", fake_backfill)

    ready = asyncio.run(refresh.refresh_market_sentiment(_app(StoreStub()), days=14))

    assert set(ready) == {"CN", "US"}
    assert calls[0]["markets"] == {"US"}
    assert calls[0]["days"]["US"] == [target_day]
    # 설정한 하루치 표본 수가 그대로 넘어가는지만 본다(값 자체는 튜닝 대상).
    assert calls[0]["kwargs"]["articles_per_day"] == refresh.MARKET_DIGEST_ARTICLES_PER_DAY
    assert published == [(b"chart", {"CN", "US"}, 14)]


def test_thirty_day_refresh_targets_all_markets_and_full_range(monkeypatch):
    today = kst_today()
    missing = [today - timedelta(days=offset) for offset in range(8, 30)]
    captured, published = {}, []

    class StoreStub:
        async def series(self, markets, days):
            return _ready_series(("CN", "HK", "US", "KR", "JP", "EU"), today)

        async def missing_digest_days(self, markets, days):
            assert days == 30
            return {market: list(missing) for market in markets}

    async def fake_backfill(store, analyzer, semaphore, markets, queries, days_by_market, **kwargs):
        captured["markets"] = markets
        captured["days"] = days_by_market

    _quiet(monkeypatch, published)
    monkeypatch.setattr(refresh, "backfill_market_digests", fake_backfill)

    asyncio.run(refresh.refresh_market_sentiment(_app(StoreStub()), days=30))

    assert captured["markets"] == {"CN", "HK", "US", "KR", "JP", "EU"}
    assert all(len(days) == 7 for days in captured["days"].values())
    assert all(days[0] == missing[0] for days in captured["days"].values())
    assert all(days[-1] == missing[-1] for days in captured["days"].values())


def test_thin_series_keeps_the_previous_chart(monkeypatch):
    """불완전한 선이나 단일 점 차트는 굽지 않는다. 웹에는 직전 차트가 남는다."""
    today = kst_today()
    published = []

    class StoreStub:
        async def series(self, markets, days):
            return _ready_series(("CN", "US"), today)

        async def missing_digest_days(self, markets, days):
            return {market: [] for market in markets}

    _quiet(monkeypatch, published, gaps={"US": "표본 부족"})

    assert asyncio.run(refresh.refresh_market_sentiment(_app(StoreStub()))) is None
    assert published == []


class _Message:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def test_panel_refresh_reports_the_ranking(monkeypatch):
    async def fake_refresh(app):
        return {"US": {"avg_sentiment": 0.3}, "CN": {"avg_sentiment": -0.2}}

    monkeypatch.setattr(commands, "refresh_market_sentiment", fake_refresh)
    message = _Message()

    asyncio.run(commands.cmd_market(SimpleNamespace(effective_message=message), SimpleNamespace(application=object())))

    assert "갱신 완료" in message.replies[-1]
    assert message.replies[-1].index("미국") < message.replies[-1].index("중국")


def test_panel_refresh_failure_says_the_web_keeps_the_last_chart(monkeypatch, caplog):
    async def boom(app):
        raise RuntimeError("font")

    monkeypatch.setattr(commands, "refresh_market_sentiment", boom)
    message = _Message()

    with caplog.at_level("ERROR"):
        asyncio.run(commands.cmd_market(SimpleNamespace(effective_message=message), SimpleNamespace(application=object())))

    assert "직전 차트" in message.replies[-1]
    assert "수동 갱신 실패" in caplog.text


def test_cumulative_tone_turns_where_the_regime_turns_instead_of_flattening():
    """평균에 붙지 않는다(2026-10-04): 긍정 국면 15일 뒤 부정 국면 15일이면 선은 오르다 정확히 그 날 꺾인다."""
    from datetime import datetime

    from services.telegram_bot.features.market_sentiment.chart import _cumulative_tone

    dates = [datetime(2026, 9, 1) + timedelta(days=day) for day in range(30)]
    values = [0.2 + (0.05 if day % 2 else -0.05) for day in range(15)] + [-0.2] * 15
    _, curve = _cumulative_tone(dates, values, baseline=0.0)

    peak = max(range(30), key=curve.__getitem__)
    assert peak == 14                                  # 국면이 바뀐 날 꺾인다
    assert curve[14] > 5 and curve[-1] < curve[14] - 5  # 꺾인 뒤 뚜렷이 내려간다
    assert _cumulative_tone(dates[:1], values[:1], 0.0) is None


def test_cumulative_tone_uses_the_common_baseline_and_not_each_markets_own_mean():
    """기준선이 시장 자기 평균이면 끝이 늘 0으로 돌아온다. 전 시장 공통 평균은 그렇지 않다."""
    from datetime import datetime

    from services.telegram_bot.features.market_sentiment.chart import _common_baseline, _cumulative_tone

    dates = [datetime(2026, 9, 1) + timedelta(days=day) for day in range(10)]
    warm = [{"date": d.date().isoformat(), "avg_sentiment": 0.3 + (0.1 if i % 2 else 0), "count": 10}
            for i, d in enumerate(dates)]
    cold = [{"date": d.date().isoformat(), "avg_sentiment": -0.1 + (0.1 if i % 2 else 0), "count": 10}
            for i, d in enumerate(dates)]
    baseline = _common_baseline({"JP": {"daily": warm}, "US": {"daily": cold}})
    assert abs(baseline - 0.15) < 1e-9                 # 기사 수 가중 전 시장 평균(0.35와 −0.05)

    _, jp = _cumulative_tone(dates, [p["avg_sentiment"] for p in warm], baseline)
    _, us = _cumulative_tone(dates, [p["avg_sentiment"] for p in cold], baseline)
    assert jp[-1] > 10 and us[-1] < -10                # 평균보다 따뜻한 시장은 오르고 찬 시장은 내린다


def test_chart_renders_thirty_days_with_gaps():
    from services.telegram_bot.features.market_sentiment.chart import render_market_chart

    daily = [{"date": f"2026-09-{day:02d}", "avg_sentiment": (day % 7 - 3) / 5, "count": 12}
             for day in range(1, 29) if day % 5]
    image = render_market_chart({"US": {"avg_sentiment": 0.1, "daily": daily},
                                 "KR": {"avg_sentiment": -0.2, "daily": daily[:3]}}, 30)
    assert image.getvalue().startswith(b"\x89PNG")
