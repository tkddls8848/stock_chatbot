"""예약 작업의 시간대와 관리 패널의 웹 상태.

**cron 작업은 전부 JST로 읽는다.** 스케줄러에 시간대를 주지 않으면 호스트 시간대를
따르는데 공유 호스트가 UTC라, 2026-09-24까지 브리핑(08:50)과 종목 DB 갱신(08:30)이
9시간 늦게 돌았다(모닝 브리핑이 18:24 KST에 도착). 기본 기능 조합의 cron 작업을
전부 설치해 시간대를 확인한다 — 새 작업이 빠뜨려도 여기서 걸린다.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

from apscheduler.triggers.cron import CronTrigger

from services.telegram_bot import main as bot_main
from services.telegram_bot.core.clock import JST
from services.telegram_bot.core.config import FEATURES_ENABLED
from services.telegram_bot.features import build_feature_registry
from services.telegram_bot.features.web_status import handlers as web_status


def _installed_jobs(monkeypatch):
    monkeypatch.setattr("services.telegram_bot.stocks.StockDatabase.load_or_build", lambda self: None)
    registry = build_feature_registry(FEATURES_ENABLED)
    app = SimpleNamespace(bot_data={})
    registry.install_services(app)
    scheduler = bot_main.build_scheduler()
    registry.install_jobs(scheduler, app)
    return {job.id: job for job in scheduler.get_jobs()}


def _next_fire_jst(job) -> datetime:
    start = datetime(2026, 9, 24, 0, 0, tzinfo=JST)
    return job.trigger.get_next_fire_time(None, start).astimezone(JST)


def test_every_cron_job_fires_on_jst(monkeypatch):
    jobs = _installed_jobs(monkeypatch)
    cron_jobs = {job_id: job for job_id, job in jobs.items() if isinstance(job.trigger, CronTrigger)}

    assert cron_jobs, "cron 작업이 하나도 없다"
    for job_id, job in cron_jobs.items():
        assert job.trigger.timezone.utcoffset(None) == timedelta(hours=9), job_id


def test_scheduled_times_are_the_documented_jst_times(monkeypatch):
    jobs = _installed_jobs(monkeypatch)

    fire = {job_id: _next_fire_jst(jobs[job_id]).strftime("%H:%M") for job_id in (
        "morning_briefing", "evening_briefing", "refresh_stock_db",
        "scheduled_research", "market_sentiment_refresh",
    )}

    assert fire == {
        "morning_briefing": "08:50",
        "evening_briefing": "17:40",
        "refresh_stock_db": "08:30",
        # 모닝 브리핑보다 앞이어야 브리핑이 그날 결과를 쓴다.
        "scheduled_research": "08:20",
        "market_sentiment_refresh": "07:40",
    }


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def test_web_status_reads_the_public_api(monkeypatch):
    payloads = {
        "/api/meta": {"market_generated_at": "2026-09-24T07:40:03+09:00",
                      "research_generated_at": "2026-09-24T08:21:10+09:00"},
        "/api/polymarket/health": {"freshness": {"state": "normal", "last_success_at": "2026-09-24T06:01:00+09:00"},
                                   "last_result": "ok"},
        "/api/polymarket/sector-brief": {"written_at": "2026-09-24T06:03:00+09:00"},
        "/api/polymarket/trending": {"written_at": "2026-09-24T06:02:00+09:00"},
        "/api/polymarket/events?page_size=1": {"search_index": {"annotated": 1400, "total": 19070}},
    }

    def fetch(url, timeout):
        return _Response(payloads[url.removeprefix(web_status.WEB_STATUS_BASE_URL)])

    text = web_status.build_web_status(fetch)

    assert "시장 감성: 2026-09-24 07:40" in text
    assert "리서치: 2026-09-24 08:21" in text
    assert "폴리마켓 수집: 2026-09-24 06:01 · 제때 갱신" in text
    assert "한국어 검색 준비: 1,400/19,070건" in text


def test_web_status_says_when_the_web_is_down():
    import requests

    def fetch(url, timeout):
        raise requests.ConnectionError("refused")

    assert "웹이 응답하지 않습니다" in web_status.build_web_status(fetch)
