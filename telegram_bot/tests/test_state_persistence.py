"""상태 파일 저장 실패가 성공으로 보고되지 않는지 검증한다.

이 파일이 지키는 규칙은 하나다. **저장하지 못했으면 저장했다고 말하지 않는다.**
반환값(스냅숏을 남겼는가)과 메모리 상태(그 날을 계산했는가)가 디스크와 어긋나면,
데이터가 사라진 것보다 나쁘다 — 사라진 줄 모르는 상태가 된다.

디스크 고장은 `os.replace`를 실패시켜 흉내 낸다. 임시 파일까지는 정상적으로
쓰이고 마지막 교체에서만 실패하므로, 원자적 교체가 실제로 원본을 지키는지도
같은 자리에서 확인된다.
"""

import asyncio
import json
from telegram_bot.core.clock import today as kst_today
from types import SimpleNamespace

import pytest

from telegram_bot.core import storage
from telegram_bot.core.storage import write_json_atomic
from telegram_bot.state.market_digest import MarketDigestStore
from telegram_bot.watchlist.manager import WatchlistManager


def _break_replace(monkeypatch):
    def full_disk(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(storage.os, "replace", full_disk)


def test_failed_write_keeps_the_previous_file_and_leaves_no_debris(
    tmp_path, monkeypatch
):
    target = tmp_path / "state.json"
    write_json_atomic(target, {"keep": 1})

    _break_replace(monkeypatch)
    with pytest.raises(OSError):
        write_json_atomic(target, {"lost": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"keep": 1}
    # 임시 파일이 남으면 다음 실행이 남의 잔해를 보고 판단하게 된다.
    assert sorted(path.name for path in tmp_path.iterdir()) == ["state.json"]


def test_digest_day_is_not_remembered_when_it_was_never_written(tmp_path, monkeypatch):
    store = MarketDigestStore(tmp_path / "daily_digest.json", 30)
    day = kst_today()

    _break_replace(monkeypatch)
    with pytest.raises(OSError):
        asyncio.run(store.put("KR", day, sentiment=0.5, article_count=3, final=True))
    monkeypatch.undo()

    # 디스크에 없는 날을 확정으로 기억하면 /market이 영영 그 날을 건너뛴다.
    assert day in asyncio.run(store.missing_digest_days({"KR"}, 2))["KR"]


def test_watchlist_change_is_dropped_when_it_cannot_be_saved(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.json"
    manager = WatchlistManager(path)
    asyncio.run(manager.add("600519", "귀주모태주"))

    _break_replace(monkeypatch)
    with pytest.raises(OSError):
        asyncio.run(manager.add("00700", "텐센트"))
    monkeypatch.undo()

    # 실패라고 알린 편입이 메모리에 남아 있으면, 다음 저장 때 함께 적힌다.
    assert asyncio.run(manager.get_all()) == {"600519": "귀주모태주"}
    assert json.loads(path.read_text(encoding="utf-8")) == {"600519": "귀주모태주"}


def test_watchlist_removal_is_dropped_when_it_cannot_be_saved(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.json"
    manager = WatchlistManager(path)
    asyncio.run(manager.add("600519", "귀주모태주"))

    _break_replace(monkeypatch)
    with pytest.raises(OSError):
        asyncio.run(manager.remove("600519"))
    monkeypatch.undo()

    assert asyncio.run(manager.get_all()) == {"600519": "귀주모태주"}


def test_stock_db_build_keeps_the_previous_cache_when_saving_fails(
    tmp_path, monkeypatch
):
    import pandas as pd

    from telegram_bot.stocks.database import StockDatabase

    cache = tmp_path / "stock_db.json"
    write_json_atomic(cache, {"600519": {"display_name": "이전 DB"}})
    database = StockDatabase(cache)

    a_shares = pd.DataFrame({"code": ["000001"], "name": ["평안은행"]})
    monkeypatch.setattr("telegram_bot.stocks.database._fetch_a_code_name", lambda: a_shares)
    monkeypatch.setattr("telegram_bot.stocks.database._fetch_hk_spot", lambda: None)
    monkeypatch.setattr("telegram_bot.stocks.database.fetch_kr_listed", lambda: [])
    monkeypatch.setattr("telegram_bot.stocks.database.fetch_us_listed", lambda: [])
    _break_replace(monkeypatch)

    with pytest.raises(OSError):
        database.build()
    monkeypatch.undo()

    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "600519": {"display_name": "이전 DB"}
    }


def test_manual_briefing_reports_a_failed_send_to_the_caller(monkeypatch):
    """`/briefing morning`이 한 글자도 못 보내고 "처리 완료"로 끝나면 안 된다."""
    from telegram_bot.briefing import service

    async def no_quant(app, include_fund_flow):
        return {}, ""

    async def no_news(app):
        return []

    async def no_comment(app, payload):
        return ""

    monkeypatch.setattr(service, "_build_quant_section", no_quant)
    monkeypatch.setattr(service, "_collect_briefing_news", no_news)
    monkeypatch.setattr(service, "_write_llm_comment", no_comment)

    class Bot:
        async def send_message(self, **kwargs):
            raise RuntimeError("telegram down")

    app = SimpleNamespace(
        bot=Bot(),
        bot_data={"market_view_manager": SimpleNamespace(get_sight=lambda: "")},
    )

    with pytest.raises(RuntimeError, match="telegram down"):
        asyncio.run(service.send_morning_briefing(app, force=True))


def test_scheduled_briefing_contains_the_same_failure(caplog):
    """예약 실행에는 결과를 받을 사람이 없으므로 여기서만 삼킨다."""
    from telegram_bot.features.briefing.feature import _run_scheduled

    async def boom(app):
        raise RuntimeError("telegram down")

    with caplog.at_level("ERROR"):
        asyncio.run(_run_scheduled(boom, None, "모닝 브리핑"))

    assert "모닝 브리핑 예약 실행 실패" in caplog.text
