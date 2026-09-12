"""관심종목 관리 기능 선언."""

from shared.core.config import WATCHLIST_EVENTS_FILE, WATCHLIST_FILE
from telegram_bot.features.base import CallbackSpec, CommandSpec, FeatureSpec, MenuSpec
from telegram_bot.watchlist import (
    WatchlistEventLog,
    WatchlistManager,
    cmd_add,
    cmd_list,
    cmd_menu,
    handle_watchlist_callback,
)


def _install_services(app) -> None:
    stock_db = app.bot_data["stock_db"]
    app.bot_data["watchlist_manager"] = WatchlistManager(
        WATCHLIST_FILE,
        code_resolver=stock_db.resolve_code,
    )
    app.bot_data["watchlist_events"] = WatchlistEventLog(WATCHLIST_EVENTS_FILE)

FEATURE = FeatureSpec(
    key="watchlist",
    label="관심종목",
    requires=frozenset({"instruments"}),
    commands=(
        CommandSpec("menu", "관심종목 관리", cmd_menu),
        CommandSpec("add", "관심종목 추가", cmd_add, usage="종목코드"),
        CommandSpec("list", "관심종목 목록", cmd_list),
    ),
    menus=(
        MenuSpec("⭐ 관심종목", "nav:watch", 0, "⭐ 관심종목", 1),
    ),
    callbacks=(
        CallbackSpec(("add_", "remove:", "close"), handle_watchlist_callback),
    ),
    install_services=_install_services,
    data_files=("data/watchlist/watchlist.json", "data/watchlist/watchlist_events.json"),
)
