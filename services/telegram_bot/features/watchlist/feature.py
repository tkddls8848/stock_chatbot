"""관심종목 기능 선언.

관심종목 편집 화면은 웹 개인 화면(`/portfolio`)으로 옮겼다. 텔레그램에는 추가·
삭제 명령·메뉴가 없고, 봇은 공유 파일을 읽어 수집·사전선별·리서치·브리핑에 쓴다.
`/system watchlist`가 파일 상태를 보여 준다.
"""

import html

from services.telegram_bot.core.config import WATCHLIST_EVENTS_FILE, WATCHLIST_FILE
from services.telegram_bot.features.base import FeatureSpec, StatusReportSpec
from services.telegram_bot.watchlist import WatchlistEventLog, WatchlistManager


def _install_services(app) -> None:
    stock_db = app.bot_data["stock_db"]
    app.bot_data["watchlist_manager"] = WatchlistManager(
        WATCHLIST_FILE,
        code_resolver=stock_db.resolve_code,
    )
    app.bot_data["watchlist_events"] = WatchlistEventLog(WATCHLIST_EVENTS_FILE)


async def render_watchlist_status(bot_data) -> str | None:
    manager = bot_data.get("watchlist_manager")
    if manager is None:
        return None
    items = await manager.get_all()
    lines = ["<b>⭐ 관심종목</b> (편집은 웹 /portfolio)", html.escape(manager.status_line())]
    lines.extend(f"· {html.escape(name)} ({html.escape(code)})" for code, name in list(items.items())[:30])
    if len(items) > 30:
        lines.append(f"… 외 {len(items) - 30}개")
    return "\n".join(lines)


FEATURE = FeatureSpec(
    key="watchlist",
    label="관심종목(웹과 공유)",
    requires=frozenset({"instruments"}),
    status_reports=(
        StatusReportSpec("watchlist", "관심종목 공유 파일 상태", render_watchlist_status),
    ),
    install_services=_install_services,
    data_files=("storage/portfolio/watchlist.json", "storage/bot/watchlist/watchlist_events.json"),
)
