import asyncio
import json

import pytest

from services.telegram_bot.llm.market_view import MarketViewAnalyzer, MarketViewError
from services.telegram_bot.research import job


class _StockDatabaseStub:
    def get_display_name(self, code: str) -> str | None:
        return None

    def resolve_code(self, code: str) -> str | None:
        return None


def _parser() -> MarketViewAnalyzer:
    analyzer = object.__new__(MarketViewAnalyzer)
    analyzer._max_new_actions = 4
    analyzer._max_actions = 6
    return analyzer


def test_market_view_parser_normalizes_relevance():
    analyzer = _parser()
    result = analyzer._parse_analysis(
        json.dumps(
            {
                "summary": "요약",
                "actions": [
                    {
                        "ticker": "600001",
                        "name": "종목",
                        "action": "remove",
                        "confidence": 0.8,
                        "relevance": -0.1,
                        "evidence": [],
                    }
                ],
                "risks": [],
            },
            ensure_ascii=False,
        ),
        news_items=[],
    )

    assert result["actions"][0]["relevance"] == 0.0


def test_market_view_parser_scopes_and_caps_new_actions():
    analyzer = _parser()
    analyzer._max_new_actions = 1
    result = analyzer._parse_analysis(
        json.dumps(
            {
                "summary": "요약",
                "actions": [
                    {
                        "ticker": "NOT-A-CANDIDATE",
                        "action": "add",
                        "confidence": 0.9,
                        "relevance": 0.8,
                    },
                    {
                        "ticker": "US:NASDAQ:MSFT",
                        "action": "add",
                        "confidence": 0.9,
                        "relevance": 0.8,
                    },
                    {
                        "ticker": "US:NASDAQ:AAPL",
                        "action": "ADD",
                        "confidence": 1.4,
                        "relevance": -0.2,
                    },
                    {
                        "ticker": "KR:KOSPI:005930",
                        "action": "watch",
                        "confidence": 0.7,
                        "relevance": 0.7,
                    },
                    {
                        "ticker": "00700",
                        "action": "remove",
                        "confidence": -0.2,
                        "relevance": 1.4,
                    },
                    {
                        "ticker": "US:NASDAQ:AAPL",
                        "action": "buy",
                        "confidence": 1,
                    },
                ],
                "risks": [],
            }
        ),
        news_items=[],
        watchlist={"US:NASDAQ:MSFT": "Microsoft", "00700": "Tencent"},
        candidate_universe=[
            {"code": "US:NASDAQ:AAPL"},
            {"code": "US:NASDAQ:MSFT"},
            {"code": "KR:KOSPI:005930"},
        ],
    )

    assert result["actions"] == [
        {
            "ticker": "US:NASDAQ:AAPL",
            "name": "",
            "action": "add",
            "confidence": 1.0,
            "relevance": 0.0,
            "reason": "",
            "evidence": [],
        },
        {
            "ticker": "00700",
            "name": "",
            "action": "remove",
            "confidence": 0.0,
            "relevance": 1.0,
            "reason": "",
            "evidence": [],
        },
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", float("nan")),
        ("confidence", float("inf")),
        ("relevance", float("-inf")),
    ],
)
def test_market_view_parser_rejects_non_finite_action_scores(field, value):
    analyzer = _parser()
    action = {
        "ticker": "600001",
        "action": "remove",
        "confidence": 0.8,
        "relevance": 0.5,
    }
    action[field] = value

    with pytest.raises(MarketViewError, match=rf"{field} must be finite"):
        analyzer._parse_analysis(
            json.dumps(
                {
                    "summary": "요약",
                    "actions": [action],
                    "risks": [],
                }
            ),
            news_items=[],
        )


class _WatchlistManagerStub:
    def __init__(self):
        self.items = {"600001": "삭제 종목", "600002": "유지 종목"}

    async def get_all(self):
        return self.items.copy()

    async def remove(self, code):
        return self.items.pop(code, None)

    async def add(self, code, name):
        self.items[code] = name


def test_apply_removes_candidate_and_reports_only_real_changes(monkeypatch):
    async def no_event(*args, **kwargs):
        return None

    monkeypatch.setattr(job, "record_watchlist_event", no_event)
    manager = _WatchlistManagerStub()
    pending = {"add": [], "remove": [{"code": "600001", "name": "삭제 종목"}, {"code": "999999"}]}

    applied = asyncio.run(job.apply_actions({"watchlist_manager": manager}, pending))

    assert manager.items == {"600002": "유지 종목"}
    assert applied == {"add": [], "remove": ["삭제 종목(600001)"]}
