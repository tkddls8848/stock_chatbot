import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.telegram_bot.stocks import StockDatabase
from services.telegram_bot.webadmin.server import build_app
from services.telegram_bot.watchlist.handlers import cmd_list


class _Watchlist:
    def __init__(self):
        self.added = []

    async def add(self, code: str, name: str) -> None:
        self.added.append((code, name))


def _post_watchlist_endpoint(bot_data):
    api = build_app(SimpleNamespace(bot_data=bot_data))
    return next(
        route.endpoint
        for route in api.routes
        if route.path == "/api/watchlist" and "POST" in route.methods
    )


def _stock_db(tmp_path):
    stock_db = StockDatabase(tmp_path / "stock_db.json")
    stock_db._db = {
        "600519": {"display_name": "Moutai", "market": "SH"},
        "KR:KOSPI:005930": {
            "display_name": "Samsung Electronics",
            "market": "KR",
        },
        "US:NASDAQ:AAPL": {"display_name": "Apple Inc.", "market": "US"},
    }
    return stock_db


def test_watchlist_add_canonicalizes_code_even_with_custom_name(tmp_path):
    manager = _Watchlist()
    endpoint = _post_watchlist_endpoint(
        {
            "watchlist_manager": manager,
            "stock_db": _stock_db(tmp_path),
        }
    )

    async def exercise():
        first = await endpoint(
            {"code": "aapl", "name": "My Apple"},
            "admin",
        )
        second = await endpoint(
            {"code": "005930", "name": "My Samsung"},
            "admin",
        )
        return first, second

    first, second = asyncio.run(exercise())

    assert first == {"code": "US:NASDAQ:AAPL", "name": "My Apple"}
    assert second == {"code": "KR:KOSPI:005930", "name": "My Samsung"}
    assert manager.added == [
        ("US:NASDAQ:AAPL", "My Apple"),
        ("KR:KOSPI:005930", "My Samsung"),
    ]


def test_watchlist_add_rejects_unknown_code_even_with_custom_name(tmp_path):
    manager = _Watchlist()
    endpoint = _post_watchlist_endpoint(
        {
            "watchlist_manager": manager,
            "stock_db": _stock_db(tmp_path),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            endpoint(
                {"code": "NOT-A-STOCK", "name": "Custom"},
                "admin",
            )
        )

    assert exc_info.value.status_code == 400
    assert manager.added == []


def test_watchlist_list_escapes_web_custom_name():
    class Message:
        async def reply_text(self, text, **kwargs):
            self.text = text
            self.kwargs = kwargs

    class Manager:
        async def get_all(self):
            return {"US:NASDAQ:AAPL": "<b>A&B</b>"}

    message = Message()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(bot_data={"watchlist_manager": Manager()})

    asyncio.run(cmd_list(update, context))

    assert "&lt;b&gt;A&amp;B&lt;/b&gt;" in message.text
    assert message.kwargs["parse_mode"] == "HTML"
