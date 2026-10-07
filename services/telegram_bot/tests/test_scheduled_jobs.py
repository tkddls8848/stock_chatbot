"""예약 작업의 시간대와 관리 패널의 웹 상태.

**cron 작업은 전부 JST로 읽는다.** 스케줄러에 시간대를 주지 않으면 호스트 시간대를
따르는데 공유 호스트가 UTC라, 2026-09-24까지 브리핑(08:50)과 종목 DB 갱신(08:30)이
9시간 늦게 돌았다(모닝 브리핑이 18:24 KST에 도착). 기본 기능 조합의 cron 작업을
전부 설치해 시간대를 확인한다 — 새 작업이 빠뜨려도 여기서 걸린다.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import requests
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
        "/api/forecast/health": {"freshness": {"state": "normal", "last_success_at": "2026-09-24T06:01:00+09:00"},
                                   "last_result": "success"},
        "/api/forecast/sector-brief": {"written_at": "2026-09-24T06:03:00+09:00"},
        "/api/forecast/trending": {"written_at": "2026-09-24T06:02:00+09:00"},
        "/api/forecast/events?page_size=1": {"search_index": {"annotated": 1400, "total": 19070}},
    }

    def fetch(url, timeout, **_):
        if not url.startswith(web_status.WEB_STATUS_BASE_URL):
            raise requests.ConnectionError("외부 자료는 이 시험에서 묻지 않는다")
        return _Response(payloads[url.removeprefix(web_status.WEB_STATUS_BASE_URL)])

    text = web_status.build_web_status(fetch)

    # 표의 맞춤 칸(기호·시각·건수)은 ASCII만, 한글 항목은 맨 끝 칸이다(2026-10-08).
    assert "🟢 09/24 07:40  시장 감성\n" in text
    assert "🟢 09/24 08:21  리서치\n" in text
    assert "자료 없음</pre>" in text   # 뉴스 검색 자료 시각이 없다
    # 정상 결과(success)는 "마지막 시도"를 덧붙이지 않는다.
    assert "🟢 09/24 06:01   수집 · 제때 갱신\n" in text and "마지막 시도" not in text
    assert "🔵 1,400/19,070  질문 한국어 검색 준비</pre>" in text


def test_web_status_shows_when_the_brief_fell_back(monkeypatch):
    """줄글이 써진 시각만으로는 해설이 하나도 안 나온 실행을 구분하지 못한다(2026-10-03 검수)."""
    payloads = {
        "/api/meta": {},
        "/api/forecast/health": {},
        "/api/forecast/sector-brief": {"written_at": "2026-10-03T08:01:00+09:00",
                                       "group_counts": {"ok": 1, "stale": 2, "facts_only": 2, "empty": 0}},
        "/api/forecast/trending": {},
        "/api/forecast/events?page_size=1": {},
    }

    def fetch(url, timeout, **_):
        if not url.startswith(web_status.WEB_STATUS_BASE_URL):
            raise requests.ConnectionError("외부 자료는 이 시험에서 묻지 않는다")
        return _Response(payloads[url.removeprefix(web_status.WEB_STATUS_BASE_URL)])

    text = web_status.build_web_status(fetch)
    assert "🟡 10/03 08:01  줄글 · 해설 1개" in text
    assert "</pre>\n· 줄글 예외: 직전 단락 2 · 확률만 2" in text


def test_web_status_says_when_the_web_is_down():
    import requests

    def fetch(url, timeout):
        raise requests.ConnectionError("refused")

    assert "웹이 응답하지 않습니다" in web_status.build_web_status(fetch)


def test_web_status_asks_the_asset_sources_now_instead_of_reading_an_old_record():
    """상태 패널은 앱 기록이 아니라 지금 도는 시스템을 본다. 9월 24일 조언 기록의 "한국은행 키 없음"이
    키를 넣은 뒤에도 계속 보였다(운영자 지적 2026-10-08)."""
    class Reply:
        def __init__(self, status_code=200, body=None, text=""):
            self.status_code, self._body, self.text = status_code, body or {}, text

        def json(self):
            return self._body

    seen = []

    def fetch(url, timeout, params=None):
        seen.append((url, params))
        if "finlife" in url:
            return Reply(body={"result": {"err_cd": "000"}})
        if "ecos" in url:
            return Reply(body={"RESULT": {"CODE": "INFO-100"}})
        raise requests.ConnectionError("https://apis.data.go.kr/...serviceKey=SECRET")

    rows = web_status.source_status_rows(fetch, {"FSS_API_KEY": "f", "ECOS_API_KEY": "SECRET", "MOLIT_API_KEY": "m"})
    assert rows == [("🟢", "OK", "금감원"), ("🔴", "INFO-100", "한국은행 · 응답 실패"), ("🔴", "-", "국토부 · 연결 실패")]
    assert "SECRET" not in str(rows) and len(seen) == 3

    seen.clear()
    rows = web_status.source_status_rows(fetch, {"FSS_API_KEY": "f"})
    assert [label for _, _, label in rows[1:]] == ["한국은행 · 키 없음", "국토부 · 키 없음"]
    assert len(seen) == 1   # 키가 없으면 묻지 않는다


def test_public_data_portal_key_rejection_shows_its_reason():
    class Reply:
        status_code = 403
        text = ("<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>"
                "<returnAuthMsg>등록되지 않은 서비스키</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")

    rows = web_status.source_status_rows(lambda url, timeout, params=None: Reply(), {"MOLIT_API_KEY": "m"})
    assert rows[-1] == ("🔴", "HTTP 403", "국토부 · 등록되지 않은 서비스키")
