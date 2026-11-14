"""웹이 굽는 비공개 숫자 요약을 텔레그램에서 읽는 계약."""
from datetime import timedelta
import json

from services.telegram_bot.core.clock import now, today
from services.telegram_bot.features.web_status import handlers


def test_activity_summary_and_staleness(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "STORAGE_DIR", tmp_path)
    assert "통계 자료 없음" in handlers.build_activity_status()
    path = tmp_path / "web" / "activity_summary.json"
    path.parent.mkdir()
    data = {"version": 1, "updated_at": now().isoformat(), "started_at": now().isoformat(),
            "accounts": 3, "legacy_accounts": 1,
            "periods": {key: {"new": 2, "active": 3, "views": 10, "errors": 0}
                        for key in ("today", "yesterday", "7d", "30d")},
            "days": {today().isoformat(): {"pages": {"/": 10}, "actions": {"news_search": 4}}}}
    path.write_text(json.dumps(data))
    text = handlers.build_activity_status()
    assert "현재 계정" in text and "최근 7일 활성 계정" in text
    assert "홈 10회" in text and "방문자 수가 아닙니다" in text
    assert "집계가 지연" not in text
    data["updated_at"] = (now() - timedelta(minutes=3)).isoformat()
    path.write_text(json.dumps(data))
    assert "집계가 지연" in handlers.build_activity_status()
    path.write_text('{"broken":')
    assert "기록 오류" in handlers.build_activity_status()
