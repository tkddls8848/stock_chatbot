"""브리핑·리서치 핸들러의 성공 및 외부 실패 경계 통합 테스트."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from services.telegram_bot.briefing import service as briefing_service
from services.telegram_bot.core.clock import JST
from services.telegram_bot.research import handlers as research_handlers
from services.telegram_bot.research import job as research_job


class _Message:
    def __init__(self, *, fail=False):
        self.replies = []
        self._fail = fail

    async def reply_text(self, text, **kwargs):
        if self._fail:
            raise RuntimeError("telegram down")
        self.replies.append((text, kwargs))


class _Bot:
    def __init__(self, *, fail=False):
        self.messages = []
        self._fail = fail

    async def send_message(self, **kwargs):
        if self._fail:
            raise RuntimeError("telegram down")
        self.messages.append(kwargs)


class _Application:
    def __init__(self):
        self.bot_data = {}
        self.tasks = []

    def create_task(self, coroutine, *, update, name):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


def test_morning_briefing_success_reaches_telegram(monkeypatch):
    async def summary(_app, include_fund_flow):
        assert include_fund_flow is False
        return {"market": "US"}, "시장 요약"

    async def news(_app):
        return [{"title": "Fed holds rates", "source": "wire", "sentiment": 0.2}]

    async def comment(_app, payload):
        assert payload["kind"] == "morning"
        return "변동성에 주의"

    monkeypatch.setattr(briefing_service, "_build_sector_summary_section", summary)
    monkeypatch.setattr(briefing_service, "_collect_briefing_news", news)
    monkeypatch.setattr(briefing_service, "_write_llm_comment", comment)
    bot = _Bot()
    app = SimpleNamespace(
        bot=bot,
        bot_data={"market_view_manager": SimpleNamespace(get_sight=lambda: "")},
    )

    asyncio.run(briefing_service.send_morning_briefing(app, force=True))

    assert len(bot.messages) == 1
    assert "시장 요약" in bot.messages[0]["text"]
    assert "Fed holds rates" in bot.messages[0]["text"]
    assert "변동성에 주의" in bot.messages[0]["text"]


def test_briefing_news_api_failure_falls_back_to_empty_news(monkeypatch, caplog):
    async def fail(*args, **kwargs):
        raise RuntimeError("news provider down")

    monkeypatch.setattr(briefing_service, "collect_global_market_news_items", fail)
    app = SimpleNamespace(bot_data={"news_registry": object()})

    with caplog.at_level("ERROR"):
        result = asyncio.run(briefing_service._collect_briefing_news(app))

    assert result == []
    assert "news provider down" in caplog.text


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (8, 59, "morning"),
        (9, 0, "intraday"),
        (16, 59, "intraday"),
        (17, 0, "evening"),
    ],
)
def test_briefing_kind_follows_jst_session_boundaries(hour, minute, expected):
    moment = datetime(2026, 8, 24, hour, minute, tzinfo=JST)

    assert briefing_service.select_briefing_kind(moment) == expected


def test_intraday_briefing_combines_live_market_evidence(monkeypatch):
    captured = {}

    async def summary(_app, include_fund_flow):
        assert include_fund_flow is True
        return {"fund_flow": "northbound"}, "장중 시장 요약"

    async def news(_app):
        return [{"title": "반도체 강세", "source": "wire", "sentiment": 0.4}]

    async def sentiment(_app, watchlist):
        assert watchlist == {"005930": "삼성전자"}
        return {"005930": {"count": 2, "avg_sentiment": 0.3}}, "감성 요약"

    async def comment(_app, payload):
        captured.update(payload)
        return "남은 장에서 수급 지속 여부 확인"

    monkeypatch.setattr(briefing_service, "_build_sector_summary_section", summary)
    monkeypatch.setattr(briefing_service, "_collect_briefing_news", news)
    monkeypatch.setattr(briefing_service, "_build_sentiment_section", sentiment)
    monkeypatch.setattr(briefing_service, "_write_llm_comment", comment)
    bot = _Bot()
    app = SimpleNamespace(
        bot=bot,
        bot_data={
            "watchlist_manager": SimpleNamespace(
                get_all=lambda: _watchlist_result()
            ),
            "market_view_manager": SimpleNamespace(get_sight=lambda: "반도체"),
        },
    )

    asyncio.run(briefing_service.send_intraday_briefing(app, force=True))

    text = bot.messages[0]["text"]
    assert captured["kind"] == "intraday"
    assert "장중 브리핑" in text
    assert "장중 시장 요약" in text
    assert "반도체 강세" in text
    assert "감성 요약" in text


async def _watchlist_result():
    return {"005930": "삼성전자"}


def test_briefing_without_argument_runs_automatically_selected_kind(monkeypatch):
    calls = []

    async def morning(_app, force=False):
        calls.append(("morning", force))

    async def intraday(_app, force=False):
        calls.append(("intraday", force))

    async def evening(_app, force=False):
        calls.append(("evening", force))

    monkeypatch.setattr(briefing_service, "select_briefing_kind", lambda: "intraday")
    monkeypatch.setattr(briefing_service, "send_morning_briefing", morning)
    monkeypatch.setattr(briefing_service, "send_intraday_briefing", intraday)
    monkeypatch.setattr(briefing_service, "send_evening_briefing", evening)
    message = _Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    app = _Application()
    context = SimpleNamespace(args=[], application=app)

    async def run():
        await briefing_service.cmd_briefing(update, context)
        await asyncio.gather(*app.tasks)

    asyncio.run(run())

    assert calls == [("intraday", True)]
    assert "생성을 시작" in message.replies[0][0]


def test_slow_briefing_leaves_menu_responsive_and_rejects_duplicate(monkeypatch):
    from services.telegram_bot.features import ALL_FEATURES, build_feature_registry
    from services.telegram_bot.handlers.navigation import handle_menu_text

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow_action(_app, force):
            calls.append(force)
            started.set()
            await release.wait()

        monkeypatch.setattr(briefing_service, "select_briefing_kind", lambda: "intraday")
        monkeypatch.setattr(briefing_service, "send_intraday_briefing", slow_action)
        app = _Application()
        app.bot_data["feature_registry"] = build_feature_registry(f.key for f in ALL_FEATURES)
        context = SimpleNamespace(args=[], application=app, bot_data=app.bot_data, user_data={})
        message = _Message()
        message.text = "📰 브리핑"
        update = SimpleNamespace(effective_message=message, callback_query=None)
        await asyncio.wait_for(handle_menu_text(update, context), 1)
        await asyncio.wait_for(started.wait(), 1)
        assert "생성을 시작" in message.replies[0][0]
        await asyncio.wait_for(handle_menu_text(update, context), 1)
        assert "이미 생성 중" in message.replies[-1][0]

        admin = _Message()
        admin.text = "⚙️ 관리"
        await asyncio.wait_for(handle_menu_text(SimpleNamespace(effective_message=admin), context), 1)
        assert "관리" in admin.replies[0][0]
        assert not app.tasks[0].done()
        assert calls == [True]
        release.set()
        await asyncio.gather(*app.tasks)
        assert app.bot_data["briefing_running"] is False

    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError("provider failed"), TimeoutError()])
def test_background_briefing_reports_failure_and_allows_retry(monkeypatch, failure):
    async def fail(_app, force):
        raise failure

    monkeypatch.setattr(briefing_service, "send_morning_briefing", fail)
    app = _Application()
    message = _Message()
    context = SimpleNamespace(args=["morning"], application=app)
    update = SimpleNamespace(effective_message=message, callback_query=SimpleNamespace(data="nav:briefing"))

    async def run():
        for _ in range(2):
            await briefing_service.cmd_briefing(update, context)
            await asyncio.gather(*app.tasks)
            assert app.bot_data["briefing_running"] is False

    asyncio.run(run())
    assert len(app.tasks) == 2
    assert "다시 시도" in message.replies[-1][0]


class _MarketViewManager:
    def __init__(self, topic="금리와 기술주"):
        self.saved = []
        self._topic = topic

    def get_sight(self):
        return self._topic

    def get_history_summaries(self):
        return []

    def save_result(self, result, **metadata):
        self.saved.append((result, metadata))


class _Watchlist:
    def __init__(self):
        self.items = {}

    async def get_all(self):
        return dict(self.items)

    async def add(self, code, name):
        self.items[code] = name

    async def remove(self, code):
        return self.items.pop(code, None)


class _StockDb:
    def resolve_code(self, code):
        return None

    def get_display_name(self, code):
        return None


def _research_app(collector, analyzer, *, bot=None):
    manager = _MarketViewManager()
    app = SimpleNamespace(
        bot=bot or _Bot(),
        bot_data={
            "watchlist_manager": _Watchlist(),
            "stock_db": _StockDb(),
            "market_view_analyzer": analyzer,
            "market_view_manager": manager,
            "research_news_collector": collector,
            "quote_service": None,
        },
    )
    return app, manager


@pytest.fixture
def _quiet_research(monkeypatch):
    """후보 발굴·웹 게시·관심종목 이벤트 기록을 막는다. 실제 data/에 쓰지 않는다."""
    from services.telegram_bot import publish as export

    monkeypatch.setattr(research_job, "build_research_candidate_universe", lambda *a, **k: [])
    monkeypatch.setattr(research_job, "collect_extra_candidates", lambda *a, **k: [])
    monkeypatch.setattr(export, "publish_research", lambda *a, **k: None)

    async def no_event(*args, **kwargs):
        return None

    monkeypatch.setattr(research_job, "record_watchlist_event", no_event)


async def _empty_watchlist():
    return {}


def _research_result():
    return {
        "summary": "시장 요약",
        "actions": [],
        "risks": ["변동성"],
        "view_critique": [],
    }


def test_research_run_saves_result_and_stays_quiet_without_changes(_quiet_research):
    async def collector():
        return [{"title": "headline", "content": "body", "source": "wire"}]

    analyzer = SimpleNamespace(analyze=lambda *args: _research_result())
    app, manager = _research_app(collector, analyzer)

    outcome = asyncio.run(research_job.run_research(app))

    assert len(manager.saved) == 1
    assert outcome["applied"] == {"add": [], "remove": []}
    # 바뀐 관심종목이 없으면 알림을 보내지 않는다.
    assert app.bot.messages == []


def test_research_run_applies_additions_and_sends_one_short_notice(_quiet_research):
    async def collector():
        return [{"title": "headline", "content": "body", "source": "wire"}]

    result = {
        **_research_result(),
        "actions": [{"action": "add", "ticker": "600519", "name": "귀주모태",
                     "confidence": 0.8, "relevance": 0.9, "reason": "근거"}],
    }
    analyzer = SimpleNamespace(analyze=lambda *args: result)
    app, _ = _research_app(collector, analyzer)

    asyncio.run(research_job.run_research(app))

    assert app.bot_data["watchlist_manager"].items == {"600519": "귀주모태"}
    assert len(app.bot.messages) == 1
    text = app.bot.messages[0]["text"]
    assert "➕ 귀주모태(600519)" in text and "근거" not in text


def test_research_run_without_topic_does_nothing(_quiet_research):
    async def collector():
        raise AssertionError("must not collect")

    app, manager = _research_app(collector, SimpleNamespace(analyze=None))
    manager._topic = None

    assert asyncio.run(research_job.run_research(app)) is None


def test_manual_research_run_reports_a_news_failure(_quiet_research):
    async def collector():
        raise RuntimeError("news provider down")

    app, _ = _research_app(collector, SimpleNamespace(analyze=lambda *args: _research_result()))
    message = _Message()
    context = SimpleNamespace(application=app, bot_data=app.bot_data)

    asyncio.run(research_handlers._run_and_report(message, context))

    assert any("리서치 실패" in text for text, _ in message.replies)


def test_research_notice_failure_propagates(_quiet_research):
    async def collector():
        return [{"title": "headline", "content": "body", "source": "wire"}]

    result = {
        **_research_result(),
        "actions": [{"action": "add", "ticker": "600519", "name": "귀주모태",
                     "confidence": 0.8, "relevance": 0.9}],
    }
    app, _ = _research_app(collector, SimpleNamespace(analyze=lambda *args: result), bot=_Bot(fail=True))

    with pytest.raises(RuntimeError, match="telegram down"):
        asyncio.run(research_job.run_research(app))
