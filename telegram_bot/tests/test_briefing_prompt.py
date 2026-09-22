from telegram_bot.core.config import BRIEFING_PROMPT_FILE
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio

import pytest

from telegram_bot.briefing import service


def test_briefing_prompt_supports_all_automatic_session_kinds():
    prompt = BRIEFING_PROMPT_FILE.read_text(encoding="utf-8")

    assert "morning(장전)" in prompt
    assert "intraday(장중)" in prompt
    assert "evening(장후)" in prompt
    assert "남은 장에서 확인할 포인트" in prompt
    assert "일본" in prompt and "JP" in prompt


def test_briefing_requests_japan_in_balanced_news_selection(monkeypatch):
    collector = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "collect_global_market_news_items", collector)
    asyncio.run(service._collect_briefing_news(SimpleNamespace(bot_data={"news_registry": object()})))
    assert collector.call_args.kwargs["markets"] == ("CN", "HK", "US", "KR", "JP")


@pytest.mark.parametrize("kind", ["morning", "intraday", "evening"])
def test_japanese_news_reaches_every_briefing_session(monkeypatch, kind):
    news = [{"market": "JP", "title": "日銀の政策発表", "source": "일본 증시 뉴스"}]
    monkeypatch.setattr(service, "_collect_briefing_news", AsyncMock(return_value=news))
    monkeypatch.setattr(service, "_build_sector_summary_section", AsyncMock(return_value=({}, "")))
    monkeypatch.setattr(service, "_build_sentiment_section", AsyncMock(return_value=({}, "")))
    writer, deliver = AsyncMock(return_value="일본은행의 발표를 확인합니다."), AsyncMock()
    monkeypatch.setattr(service, "_write_llm_comment", writer)
    monkeypatch.setattr(service, "_deliver", deliver)
    app = SimpleNamespace(bot_data={
        "watchlist_manager": SimpleNamespace(get_all=AsyncMock(return_value={})),
        "market_view_manager": SimpleNamespace(get_sight=lambda: ""),
    })
    asyncio.run(getattr(service, f"send_{kind}_briefing")(app, force=True))
    assert writer.call_args.args[1]["news_headlines"][0]["market"] == "JP"
    assert "[JP]" in "\n".join(deliver.call_args.args[1])
