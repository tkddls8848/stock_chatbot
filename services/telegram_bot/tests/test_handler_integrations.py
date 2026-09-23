"""브리핑·리서치 핸들러의 성공 및 외부 실패 경계 통합 테스트."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from services.telegram_bot.briefing import service as briefing_service
from services.telegram_bot.core.clock import JST
from services.telegram_bot.research import handlers as research_handlers


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
    context = SimpleNamespace(args=[], application=object())

    asyncio.run(briefing_service.cmd_briefing(update, context))

    assert calls == [("intraday", True)]


class _MarketViewManager:
    def __init__(self):
        self.saved = []

    def get_history_summaries(self):
        return []

    def save_result(self, result, **metadata):
        self.saved.append((result, metadata))


def _research_context(collector, analyzer):
    manager = _MarketViewManager()
    return SimpleNamespace(
        bot_data={
            "watchlist_manager": SimpleNamespace(get_all=_empty_watchlist),
            "stock_db": SimpleNamespace(),
            "market_view_analyzer": analyzer,
            "market_view_manager": manager,
            "research_news_collector": collector,
            "quote_service": None,
        }
    ), manager


async def _empty_watchlist():
    return {}


def _research_result():
    return {
        "summary": "시장 요약",
        "actions": [],
        "risks": ["변동성"],
        "view_critique": [],
    }


def test_research_job_success_saves_and_delivers_result(monkeypatch):
    async def collector():
        return [{"title": "headline", "content": "body", "source": "wire"}]

    analyzer = SimpleNamespace(analyze=lambda *args: _research_result())
    context, manager = _research_context(collector, analyzer)
    message = _Message()
    update = SimpleNamespace(effective_message=message)
    monkeypatch.setattr(research_handlers, "build_research_candidate_universe", lambda *a, **k: [])
    monkeypatch.setattr(research_handlers, "collect_extra_candidates", lambda *a, **k: [])

    asyncio.run(
        research_handlers._run_research_job(
            update,
            context,
            "금리와 기술주",
        )
    )

    assert len(manager.saved) == 1
    assert any("시장 요약" in text for text, _ in message.replies)


@pytest.mark.xfail(
    strict=True,
    reason="research news collector exceptions currently escape without a user-facing failure",
)
def test_research_news_api_failure_is_reported_to_user(monkeypatch):
    async def collector():
        raise RuntimeError("news provider down")

    analyzer = SimpleNamespace(analyze=lambda *args: _research_result())
    context, _ = _research_context(collector, analyzer)
    message = _Message()
    update = SimpleNamespace(effective_message=message)

    asyncio.run(
        research_handlers._run_research_job(
            update,
            context,
            "금리와 기술주",
        )
    )

    assert any("뉴스" in text and "실패" in text for text, _ in message.replies)


def test_research_result_send_failure_propagates(monkeypatch):
    async def collector():
        return [{"title": "headline", "content": "body", "source": "wire"}]

    analyzer = SimpleNamespace(analyze=lambda *args: _research_result())
    context, _ = _research_context(collector, analyzer)
    update = SimpleNamespace(effective_message=_Message(fail=True))
    monkeypatch.setattr(research_handlers, "build_research_candidate_universe", lambda *a, **k: [])
    monkeypatch.setattr(research_handlers, "collect_extra_candidates", lambda *a, **k: [])

    with pytest.raises(RuntimeError, match="telegram down"):
        asyncio.run(
            research_handlers._run_research_job(
                update,
                context,
                "금리와 기술주",
            )
        )
