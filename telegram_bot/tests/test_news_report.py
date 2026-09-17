"""매시간 원문 수집과 3시간 시장상황 보고서."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from telegram_bot.core.clock import JST
from telegram_bot.features.news import feature as news_feature
from telegram_bot.llm.news_report import NewsReportAnalyzer, NewsReportError
from telegram_bot.news.report import (
    collect_report_source,
    format_market_section,
    group_by_market,
    send_news_report,
)
from telegram_bot.news import report as news_report
from telegram_bot.news.registry import SourceSpec
from telegram_bot.news.sources import GlobalArticle
from telegram_bot.state import NewsReportQueue, SentNewsTracker


class _RecordingBot:
    def __init__(self, fail=False):
        self.messages = []
        self._fail = fail

    async def send_message(self, chat_id, text, parse_mode=None):
        if self._fail:
            raise RuntimeError("telegram down")
        self.messages.append(text)


class _FailOnCallBot:
    def __init__(self, fail_on: int):
        self.calls = 0
        self.messages = []
        self._fail_on = fail_on

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("telegram down")
        self.messages.append(text)


class _RecordingTracker:
    def __init__(self, reservable=True):
        self.confirmed = []
        self.released = []
        self.reserved = []
        self._reservable = reservable

    async def unavailable_ids(self):
        return set(self.confirmed) | set(self.reserved) - set(self.released)

    async def reserve(self, article_id):
        if not self._reservable:
            return False
        self.reserved.append(article_id)
        return True

    async def confirm(self, article_id):
        self.confirmed.append(article_id)

    async def release(self, article_id):
        self.released.append(article_id)

    async def persist(self):
        return None


class _RecordingLog:
    def __init__(self):
        self.records = []

    async def record(self, **kwargs):
        self.records.append(kwargs)


class _FakeBackend:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def generate(self, *, system_prompt, user_prompt, max_tokens, temperature):
        if self.error is not None:
            raise self.error
        self.calls.append(json.loads(user_prompt))
        return json.dumps(self.payload, ensure_ascii=False)


class _SequenceBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, *, system_prompt, user_prompt, max_tokens, temperature):
        self.calls.append(json.loads(user_prompt))
        return next(self.responses)


class _App:
    def __init__(self, **bot_data):
        self.bot = bot_data.pop("bot", _RecordingBot())
        self.bot_data = bot_data


class _RecordingScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, **kwargs):
        self.jobs.append((func, kwargs))


def _analyzer(tmp_path, payload=None, error=None, max_highlights=8):
    return NewsReportAnalyzer(
        backend=_FakeBackend(payload, error),
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=max_highlights,
    )


def _prompt_file():
    from telegram_bot.core.config import NEWS_REPORT_PROMPT_FILE

    return NEWS_REPORT_PROMPT_FILE


def _queue(tmp_path, per_source_limit=12, max_items=600):
    return NewsReportQueue(
        tmp_path / "news_report_queue.json",
        per_source_limit=per_source_limit,
        max_items=max_items,
    )


def _item(index, *, market="US", event_id=""):
    return {
        "article_id": f"gnews_us-{index}",
        "event_id": event_id or f"event-{index}",
        "source": "gnews_us",
        "label": "구글뉴스",
        "market": market,
        "title": f"Headline {index}",
        "url": "",
        "published_at": f"2026-08-18 0{index}:10:00",
        "published_date": "",
        "prefilter_candidate_id": "",
    }


def _payload(analysis="현재 시장상황 요약이다.", indexes=(0,)):
    return {
        "analysis": analysis,
        "highlights": [
            {
                "index": index,
                "title": f"한국어 제목 {index}",
                "sentiment": 0.4,
                "impact": "high",
                "mentioned_stocks": ["AAPL"],
            }
            for index in indexes
        ],
    }


# ── 큐 ────────────────────────────────────────────────

def test_queue_rejects_duplicate_articles_and_events(tmp_path):
    queue = _queue(tmp_path)

    accepted = asyncio.run(queue.enqueue([_item(0), _item(0), _item(1, event_id="event-0")]))

    # 같은 기사도, 같은 사건을 옮겨 적은 다른 기사도 한 번만 담는다.
    assert [row["article_id"] for row in accepted] == ["gnews_us-0"]


def test_queue_caps_one_source_per_cycle(tmp_path):
    queue = _queue(tmp_path, per_source_limit=2)

    accepted = asyncio.run(queue.enqueue([_item(index) for index in range(5)]))

    assert len(accepted) == 2


def test_queue_survives_a_restart(tmp_path):
    asyncio.run(_queue(tmp_path).enqueue([_item(0), _item(1)]))

    opened_at, items = asyncio.run(_queue(tmp_path).snapshot())

    assert opened_at
    assert [row["article_id"] for row in items] == ["gnews_us-0", "gnews_us-1"]


def test_queue_drops_the_oldest_when_full(tmp_path):
    queue = _queue(tmp_path, max_items=2)

    asyncio.run(queue.enqueue([_item(0), _item(1)]))
    asyncio.run(queue.enqueue([_item(2)]))

    _, items = asyncio.run(queue.snapshot())
    assert [row["article_id"] for row in items] == ["gnews_us-1", "gnews_us-2"]


@pytest.mark.xfail(
    strict=True,
    reason="queue overflow does not release or confirm the evicted tracker reservation",
)
def test_queue_overflow_does_not_leave_evicted_article_pending(tmp_path):
    """큐에서 밀려난 기사는 tracker의 처리 중 상태에도 남으면 안 된다."""
    tracker = SentNewsTracker(tmp_path / "sent.json")
    queue = _queue(tmp_path, max_items=2)

    class _Registry:
        def record_success(self, key):
            return None

    def article(index):
        return GlobalArticle(
            article_id=f"overflow-{index}",
            title=f"Headline {index}",
            content="본문",
            published_at=datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        )

    first = SourceSpec(
        key="first",
        label="첫 소스",
        fetch=lambda: [article(0), article(1)],
        market="US",
    )
    second = SourceSpec(
        key="second",
        label="두 번째 소스",
        fetch=lambda: [article(2)],
        market="US",
    )

    async def run():
        await collect_report_source(first, _Registry(), tracker, queue, {})
        await collect_report_source(second, _Registry(), tracker, queue, {})

    asyncio.run(run())

    _, queued = asyncio.run(queue.snapshot())
    assert [row["article_id"] for row in queued] == ["overflow-1", "overflow-2"]
    assert "overflow-0" not in tracker._pending


def test_queue_clear_empties_the_file(tmp_path):
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0)]))

    asyncio.run(queue.clear())

    assert json.loads((tmp_path / "news_report_queue.json").read_text(encoding="utf-8"))["items"] == []


# ── 매시간 원문 수집 ──────────────────────────────────

def test_report_collection_reserves_and_queues_without_translating(tmp_path):
    """수집 주기는 LLM을 호출하지 않고 원문만 큐에 저장한다."""
    articles = [
        GlobalArticle(
            article_id=f"a-{index}",
            title=f"Fed holds rates {index}",
            content="본문",
            published_at=datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
        )
        for index in range(3)
    ]
    spec = SourceSpec(key="gnews_us", label="구글뉴스", fetch=lambda: articles, market="US")

    class _Registry:
        def record_success(self, key):
            self.ok = key

        def record_failure(self, key, reason):
            raise AssertionError("소스가 실패하지 않았다")

    tracker = _RecordingTracker()
    queue = _queue(tmp_path)

    count = asyncio.run(
        collect_report_source(spec, _Registry(), tracker, queue, {}, None, "cycle-0")
    )

    assert count == 3
    assert sorted(tracker.reserved) == ["a-0", "a-1", "a-2"]
    assert tracker.released == []
    _, items = asyncio.run(queue.snapshot())
    assert {row["market"] for row in items} == {"US"}


def test_report_collection_releases_articles_the_queue_did_not_take(tmp_path):
    """큐가 받지 않은 기사는 예약을 해제해 다음 주기에 다시 볼 수 있게 한다."""
    article = GlobalArticle(
        article_id="a-0",
        title="Fed holds rates",
        content="본문",
        published_at=datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"),
    )
    spec = SourceSpec(key="gnews_us", label="구글뉴스", fetch=lambda: [article], market="US")

    class _Registry:
        def record_success(self, key):
            return None

    tracker = _RecordingTracker()
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([{**_item(0), "article_id": "a-0", "event_id": ""}]))

    count = asyncio.run(
        collect_report_source(spec, _Registry(), tracker, queue, {}, None, "cycle-0")
    )

    assert count == 0
    assert tracker.released == ["a-0"]


# ── 요약 파싱 ─────────────────────────────────────────

def test_analyzer_returns_analysis_and_highlights(tmp_path):
    analyzer = _analyzer(tmp_path, _payload())

    result = analyzer.analyze("US", "00:00~03:00 UTC +9", [{"index": 0, "title": "t"}])

    assert result["analysis"] == "현재 시장상황 요약이다."
    assert result["highlights"][0]["title"] == "한국어 제목 0"


def test_analyzer_uses_first_complete_json_object_and_ignores_trailing_text(tmp_path):
    backend = _SequenceBackend(
        [json.dumps(_payload(), ensure_ascii=False) + "\n추가 설명입니다."]
    )
    analyzer = NewsReportAnalyzer(
        backend=backend,
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=8,
    )

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "t"}])

    assert result["analysis"] == "현재 시장상황 요약이다."
    assert len(backend.calls) == 1


def test_analyzer_retries_once_when_response_validation_fails(tmp_path):
    backend = _SequenceBackend(
        [
            '{"analysis":"깨진 응답",',
            json.dumps(_payload(), ensure_ascii=False),
        ]
    )
    analyzer = NewsReportAnalyzer(
        backend=backend,
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=8,
    )

    result = analyzer.analyze("KR", "창", [{"index": 0, "title": "t"}])

    assert result["analysis"] == "현재 시장상황 요약이다."
    assert len(backend.calls) == 2


def test_analyzer_stops_after_one_validation_retry(tmp_path):
    backend = _SequenceBackend(['{"analysis":', '{"analysis":'])
    analyzer = NewsReportAnalyzer(
        backend=backend,
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=8,
    )

    with pytest.raises(NewsReportError):
        analyzer.analyze("KR", "창", [{"index": 0, "title": "t"}])

    assert len(backend.calls) == 2


def test_analyzer_rejects_an_index_that_was_not_sent(tmp_path):
    """없는 index를 받아들이면 엉뚱한 기사에 감성이 붙는다.

    두 번 다 어긋나면 그 행을 버린다. 보고서를 통째로 버리지는 않지만,
    **어긋난 행이 결과에 남지 않는다**는 것이 이 테스트가 지키는 규칙이다.
    """
    analyzer = _analyzer(tmp_path, _payload(indexes=(7,)))

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "t"}])

    assert result["highlights"] == []
    assert result["analysis"] == "현재 시장상황 요약이다."


def test_analyzer_rejects_a_repeated_index(tmp_path):
    """같은 기사를 두 번 세면 감성이 이중으로 기록된다."""
    analyzer = _analyzer(tmp_path, _payload(indexes=(0, 0)))

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "t"}])

    assert [row["index"] for row in result["highlights"]] == [0]


def test_analyzer_caps_highlights_at_the_configured_limit(tmp_path):
    analyzer = _analyzer(tmp_path, _payload(indexes=(0, 1, 2)), max_highlights=2)

    result = analyzer.analyze(
        "US", "창", [{"index": index, "title": "t"} for index in range(3)]
    )

    assert len(result["highlights"]) == 2


# ── 시장 분류와 섹션 ──────────────────────────────────

def test_markets_are_grouped_in_display_order():
    grouped = group_by_market([_item(0, market="KR"), _item(1, market="CN")])

    assert [market for market, _ in grouped] == ["CN", "KR"]


def test_failed_market_still_shows_its_headlines():
    """분석이 실패해도 그 시장의 수집 뉴스를 통째로 잃지 않는다."""
    section = format_market_section("US", [_item(0)], None)

    assert "요약 생성 실패" in section
    assert "Headline 0" in section


# ── 전송 ──────────────────────────────────────────────

def _send_app(tmp_path, *, bot=None, analyzer=None):
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0), _item(1)]))
    tracker = _RecordingTracker()
    news_log = _RecordingLog()
    app = _App(
        bot=bot or _RecordingBot(),
        news_report_queue=queue,
        news_report_analyzer=analyzer or _analyzer(tmp_path, _payload()),
        sent_tracker=tracker,
        news_log=news_log,
    )
    return app, queue, tracker, news_log


def test_report_sends_confirms_and_clears_the_queue(tmp_path):
    app, queue, tracker, news_log = _send_app(tmp_path)

    asyncio.run(send_news_report(app))

    assert "3시간 시장상황 보고서" in app.bot.messages[0]
    assert "UTC +9" in app.bot.messages[0]
    assert "JST" not in app.bot.messages[0]
    assert "한국어 제목 0" in app.bot.messages[0]
    # 큐에 있던 기사는 전부 확정된다 — release하면 주간 주기가 다시 번역한다.
    assert sorted(tracker.confirmed) == ["gnews_us-0", "gnews_us-1"]
    assert asyncio.run(queue.snapshot())[1] == []
    # 주요 기사는 주간 번역과 같은 로그로 들어간다.
    assert len(news_log.records) == 1


def test_report_keeps_the_queue_when_telegram_fails(tmp_path):
    app, queue, tracker, _ = _send_app(tmp_path, bot=_RecordingBot(fail=True))

    asyncio.run(send_news_report(app))

    assert tracker.confirmed == []
    assert len(asyncio.run(queue.snapshot())[1]) == 2


@pytest.mark.xfail(
    strict=True,
    reason="partial Telegram success currently confirms and clears every queued article",
)
def test_report_keeps_only_the_second_chunk_when_that_send_fails(tmp_path, monkeypatch):
    """부분 성공이면 성공한 chunk만 확정하고 실패한 chunk만 재시도해야 한다."""
    monkeypatch.setattr("telegram_bot.news.report.NEWS_DIGEST_MESSAGE_MAX_CHARS", 240)
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0, market="CN")]))
    asyncio.run(queue.enqueue([_item(1, market="US")]))
    tracker = _RecordingTracker()
    bot = _FailOnCallBot(fail_on=2)
    app = _App(
        bot=bot,
        news_report_queue=queue,
        news_report_analyzer=_analyzer(tmp_path, _payload()),
        sent_tracker=tracker,
        news_log=_RecordingLog(),
    )

    asyncio.run(send_news_report(app))

    _, remaining = asyncio.run(queue.snapshot())
    assert bot.calls == 2
    assert len(tracker.confirmed) == 1
    assert len(remaining) == 1
    assert remaining[0]["article_id"] not in tracker.confirmed


@pytest.mark.xfail(
    strict=True,
    reason="report highlights are logged before Telegram delivery succeeds",
)
def test_repeated_total_send_failure_does_not_duplicate_news_log(tmp_path):
    """전송되지 않은 근거는 재시도 횟수만큼 append되면 안 된다."""
    app, queue, tracker, news_log = _send_app(
        tmp_path,
        bot=_RecordingBot(fail=True),
    )

    asyncio.run(send_news_report(app))
    asyncio.run(send_news_report(app))

    assert tracker.confirmed == []
    assert len(asyncio.run(queue.snapshot())[1]) == 2
    assert news_log.records == []


def test_report_uses_one_llm_call_per_market(tmp_path):
    """기사 수와 무관하게 보고서 한 번에 시장당 한 번만 호출한다."""
    analyzer = _analyzer(tmp_path, _payload())
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0, market="US"), _item(1, market="US")]))
    asyncio.run(queue.enqueue([_item(2, market="CN")]))
    app = _App(
        news_report_queue=queue,
        news_report_analyzer=analyzer,
        sent_tracker=_RecordingTracker(),
        news_log=None,
    )

    asyncio.run(send_news_report(app))

    assert len(analyzer._backend.calls) == 2


def test_report_survives_a_market_whose_summary_failed(tmp_path):
    app, queue, tracker, _ = _send_app(
        tmp_path, analyzer=_analyzer(tmp_path, error=RuntimeError("cloudflare down"))
    )

    asyncio.run(send_news_report(app))

    assert "요약 생성 실패" in app.bot.messages[0]
    assert sorted(tracker.confirmed) == ["gnews_us-0", "gnews_us-1"]


def test_empty_queue_sends_nothing(tmp_path):
    app = _App(
        news_report_queue=_queue(tmp_path),
        news_report_analyzer=_analyzer(tmp_path, _payload()),
        sent_tracker=_RecordingTracker(),
    )

    asyncio.run(send_news_report(app))

    assert app.bot.messages == []


def test_jobs_collect_hourly_and_report_every_three_hours_utc_plus_9():
    scheduler = _RecordingScheduler()

    news_feature._install_jobs(scheduler, object())

    jobs = {kwargs["id"]: kwargs for _, kwargs in scheduler.jobs}
    assert jobs["news_collection"]["trigger"] == "interval"
    assert jobs["news_collection"]["minutes"] == 60
    assert jobs["market_situation_report"]["trigger"] == "cron"
    assert jobs["market_situation_report"]["hour"] == "*/3"
    assert jobs["market_situation_report"]["minute"] == 0
    assert jobs["market_situation_report"]["timezone"] is JST


def test_initial_collection_runs_after_telegram_startup_delay(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from telegram_bot.core.clock import now

    async def exercise():
        collected = asyncio.Event()

        async def collect(_app):
            collected.set()

        monkeypatch.setattr(news_feature, "run_news_collection", collect)
        monkeypatch.setattr(news_feature, "now", lambda: now() - timedelta(seconds=3))
        scheduler = AsyncIOScheduler()
        news_feature._install_jobs(scheduler, object())
        scheduler.remove_job("market_situation_report")
        scheduler.start()
        try:
            await asyncio.wait_for(collected.wait(), timeout=2)
        finally:
            scheduler.shutdown(wait=False)
            await asyncio.sleep(0)

    asyncio.run(exercise())


def test_report_prompt_requires_market_inference_instead_of_article_translation():
    prompt = _prompt_file().read_text(encoding="utf-8")

    assert "최근 3시간" in prompt
    assert "현재 시장상황" in prompt
    assert "다음 3시간" in prompt
    assert "UTC +9" in prompt
    assert "기사를 차례로 번역하거나 나열하지 않는다" in prompt
    assert "야간" not in prompt


# ── 마지막 시도의 근거 기사 건져내기 ──────────────────

def _bad_highlight_payload(bad):
    """정상 highlight 하나와 어긋난 하나를 섞은 응답."""
    payload = _payload(indexes=(0,))
    payload["highlights"].append(bad)
    return json.dumps(payload, ensure_ascii=False)


def _two_attempt_analyzer(responses):
    return NewsReportAnalyzer(
        backend=_SequenceBackend(responses),
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=8,
    )


def test_a_broken_highlight_no_longer_throws_the_whole_report_away(tmp_path):
    """실측(2026-09-02 CN): 8건 중 하나가 title이 없어 400자 본문이 통째로
    버려지고 원문 제목만 남았다. 비싼 것은 analysis이고 highlight는 근거다."""
    broken = {"index": 1, "sentiment": 0.1, "impact": "low", "mentioned_stocks": []}
    analyzer = _two_attempt_analyzer([_bad_highlight_payload(broken)] * 2)

    result = analyzer.analyze(
        "CN", "창", [{"index": 0, "title": "a"}, {"index": 1, "title": "b"}]
    )

    assert result["analysis"] == "현재 시장상황 요약이다."
    assert [row["index"] for row in result["highlights"]] == [0]


def test_a_repeated_highlight_index_is_dropped_not_fatal(tmp_path):
    duplicate = {
        "index": 0,
        "title": "같은 기사를 두 번",
        "sentiment": 0.2,
        "impact": "low",
        "mentioned_stocks": [],
    }
    analyzer = _two_attempt_analyzer([_bad_highlight_payload(duplicate)] * 2)

    result = analyzer.analyze("CN", "창", [{"index": 0, "title": "a"}])

    assert [row["index"] for row in result["highlights"]] == [0]


def test_the_first_attempt_still_retries_instead_of_salvaging(tmp_path):
    """건져내기는 마지막 시도에서만 한다. 먼저 8건을 온전히 받을 기회를 준다."""
    broken = {"index": 1, "sentiment": 0.1, "impact": "low", "mentioned_stocks": []}
    backend = _SequenceBackend(
        [_bad_highlight_payload(broken), json.dumps(_payload(indexes=(0, 1)), ensure_ascii=False)]
    )
    analyzer = NewsReportAnalyzer(
        backend=backend, prompt_file=_prompt_file(), num_predict=2048, max_highlights=8
    )

    result = analyzer.analyze(
        "CN", "창", [{"index": 0, "title": "a"}, {"index": 1, "title": "b"}]
    )

    assert len(backend.calls) == 2
    assert [row["index"] for row in result["highlights"]] == [0, 1]


def test_an_empty_report_still_falls_back_to_raw_titles(tmp_path):
    """본문도 없고 근거도 다 버렸으면 빈 섹션보다 제목 나열이 낫다."""
    broken = {"index": 9, "title": "범위 밖", "sentiment": 0, "impact": "low",
              "mentioned_stocks": []}
    raw = json.dumps({"analysis": "  ", "highlights": [broken]}, ensure_ascii=False)
    analyzer = _two_attempt_analyzer([raw, raw])

    with pytest.raises(NewsReportError, match="neither analysis nor highlights"):
        analyzer.analyze("CN", "창", [{"index": 0, "title": "a"}])


def test_envelope_errors_are_still_fatal_after_the_retry(tmp_path):
    """봉투가 깨진 것은 건져낼 대상이 아니다. 두 번 다 실패하면 실패다."""
    analyzer = _two_attempt_analyzer(['{"analysis":"깨진', '{"analysis":"깨진'])

    with pytest.raises(NewsReportError, match="JSON parse failed"):
        analyzer.analyze("CN", "창", [{"index": 0, "title": "a"}])


# ── 근거 기사 수는 수집량에 비례한다 ──────────────────

def _ratio_analyzer(responses, *, ratio=0.25, minimum=3, maximum=8):
    return NewsReportAnalyzer(
        backend=_SequenceBackend(responses),
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=maximum,
        min_highlights=minimum,
        highlight_ratio=ratio,
    )


@pytest.mark.parametrize(
    ("articles", "expected"),
    [
        (1, 3),    # 최소값이 받친다
        (11, 3),   # 실측(한국) — 고정 8이면 전체의 73%를 고르라는 요구였다
        (20, 5),
        (32, 8),
        (93, 8),   # 최대값이 막는다
    ],
)
def test_highlight_count_scales_with_the_articles_collected(articles, expected):
    analyzer = _ratio_analyzer([])

    assert analyzer._highlight_limit(articles) == expected


def test_the_prompt_carries_the_scaled_count_not_the_ceiling():
    """프롬프트가 '최대'라고 말해도 제시된 숫자가 모델의 목표가 된다."""
    analyzer = _ratio_analyzer([json.dumps(_payload(), ensure_ascii=False)])

    analyzer.analyze("KR", "창", [{"index": i, "title": "t"} for i in range(11)])

    prompt = analyzer._prompt_template.replace("{max_highlights}", "3")
    assert "최대 3건" in prompt
    assert "{max_highlights}" not in prompt


def test_extra_highlights_beyond_the_scaled_count_are_cut():
    """모델이 상한을 넘겨 보내면 비례 수량까지만 남긴다."""
    analyzer = _ratio_analyzer(
        [json.dumps(_payload(indexes=(0, 1, 2, 3, 4)), ensure_ascii=False)]
    )

    result = analyzer.analyze(
        "KR", "창", [{"index": i, "title": "t"} for i in range(11)]
    )

    assert len(result["highlights"]) == 3


def test_display_time_relabels_without_changing_the_value():
    """`JST`도 옛 `KST`도 UTC +9다. 값이 아니라 표기만 바꾼다."""
    from telegram_bot.news.utils import display_time

    assert display_time("2026-09-05 14:30 JST") == "14:30 UTC +9"
    assert display_time("2026-09-05 14:30 KST") == "14:30 UTC +9"
    # 형식이 다르면 손대지 않는다.
    assert display_time("알 수 없음") == "알 수 없음"


# ── 사전선별 라벨 공급 ────────────────────────────────

class _RecordingPrefilter:
    """`record_outcome` 호출만 받아 적는 최소 대역."""

    def __init__(self):
        self.outcomes = []

    async def record_outcome(self, *, candidate_id, impact, sentiment):
        self.outcomes.append((candidate_id, impact, sentiment))


def _highlight_result():
    return {
        "analysis": "분석",
        "highlights": [
            {
                "index": 0,
                "title": "한국어 제목",
                "sentiment": 0.4,
                "impact": "medium",
                "mentioned_stocks": ["600519"],
            }
        ],
    }


def _queued(candidate_id="cand-1"):
    return [{
        "article_id": "a1",
        "source": "gnews",
        "title": "raw title",
        "published_at": "2026-09-12 09:00:00",
        "published_date": "",
        "prefilter_candidate_id": candidate_id,
    }]


def test_report_highlights_feed_the_prefilter_label():
    """사전선별의 **유일한** 라벨 공급원이다.

    보고서가 이미 만든 impact를 넘기는 것이라 추가 LLM 호출이 없다. 이 선이
    끊기면 사전선별은 점수만 쌓고 영원히 학습하지 못한다 — 실제로 13일 동안
    그 상태였다.
    """
    prefilter = _RecordingPrefilter()

    asyncio.run(
        news_report._log_highlights(
            "CN", _queued(), _highlight_result(), None, prefilter
        )
    )

    assert prefilter.outcomes == [("cand-1", "medium", 0.4)]


def test_an_article_without_a_candidate_id_is_not_reported_as_a_label():
    """사전선별이 순위를 매기지 못한 주기의 기사는 이을 곳이 없다."""
    prefilter = _RecordingPrefilter()

    asyncio.run(
        news_report._log_highlights(
            "CN", _queued(candidate_id=""), _highlight_result(), None, prefilter
        )
    )

    assert prefilter.outcomes == []


def test_logging_still_works_when_the_prefilter_is_off():
    """사전선별은 선택 기능이다. 꺼져 있어도 보고서 근거 로그는 남아야 한다."""
    asyncio.run(
        news_report._log_highlights("CN", _queued(), _highlight_result(), None, None)
    )


def test_learning_evaluations_are_bounded_and_do_not_change_highlights(tmp_path):
    payload = _payload(indexes=(0,))
    payload["evaluations"] = [
        {"index": 0, "impact": "low"},
        {"index": 1, "impact": "low"},
        {"index": 1, "impact": "high"},
        {"index": 99, "impact": "low"},
        {"index": True, "impact": "low"},
        {"index": 2, "impact": "unknown"},
    ]
    result = _analyzer(tmp_path, payload)._parse(
        json.dumps(payload), valid_indexes={0, 1, 2}, limit=3,
    )
    assert result["evaluations"] == [{"index": 1, "impact": "low"}]
    assert len(result["highlights"]) == 1


def test_unselected_evaluation_only_feeds_learning():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    prefilter = SimpleNamespace(record_outcome=AsyncMock())
    news_log = SimpleNamespace(record=AsyncMock())
    result = {"analysis": "분석", "highlights": [],
              "evaluations": [{"index": 0, "impact": "low"}]}
    asyncio.run(news_report._log_highlights(
        "CN", _queued(), result, news_log, prefilter,
    ))
    prefilter.record_outcome.assert_awaited_once_with(
        candidate_id="cand-1", impact="low", sentiment=None, selected=False,
    )
    news_log.record.assert_not_awaited()


def test_exploration_gets_evaluated_without_expanding_llm_calls_or_exposing_selection(tmp_path):
    analyzer = _analyzer(tmp_path, _payload(indexes=(0,)))
    headlines = [{"index": i, "title": f"기사 {i}", "exploration": i >= 25} for i in range(30)]
    analyzer.analyze("US", "00~03", headlines)
    assert len(analyzer._backend.calls) == 1
    request = analyzer._backend.calls[0]
    assert len(request["evaluation_indexes"]) == 10
    assert len(set(request["evaluation_indexes"])) == 10
    assert set(range(25, 30)) <= set(request["evaluation_indexes"])
    assert all("exploration" not in item for item in request["articles"])


def test_exploration_flag_survives_queue_and_report_input():
    from telegram_bot.news.models import SourceCandidate

    article = GlobalArticle(article_id="one", title="News", content="", published_at="2026-09-16")
    spec = SourceSpec(key="source", label="Source", fetch=lambda: [], market="US")
    candidate = SourceCandidate(spec=spec, article=article, prefilter_exploration=True)
    item = news_report._queue_item(candidate)
    assert news_report._headline_payload([item])[0]["exploration"] is True


def test_an_unescaped_quote_no_longer_throws_the_whole_body_away(tmp_path):
    """실측(2026-09-17 00시 US, 09시 US·KR): 제목 안 큰따옴표 하나가
    `Expecting ',' delimiter`를 내고 400~500자 본문까지 통째로 버려졌다.
    비싼 것은 analysis이므로 본문만이라도 건진다."""
    # 모델이 제목의 따옴표를 escape하지 않아 문자열이 일찍 닫힌 응답이다.
    broken = (
        '{"analysis":"현재 시장상황 요약이다. 반도체가 지배적 국면이다.",'
        '"highlights":[{"index":0,"title":"애플 "신제품" 발표","sentiment":0.4,'
        '"impact":"medium","mentioned_stocks":[]}],"evaluations":[]}'
    )
    analyzer = _two_attempt_analyzer([broken, broken])

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "a"}])

    assert result["analysis"] == "현재 시장상황 요약이다. 반도체가 지배적 국면이다."
    # 근거는 잃는다. 그래도 빈 섹션이나 제목 나열보다는 본문이 있는 쪽이 낫다.
    assert result["highlights"] == []
    assert result["evaluations"] == []


def test_a_truncated_body_keeps_only_whole_sentences(tmp_path):
    """뒤가 통째로 잘린 응답에서 반 토막 문장을 보고서에 싣지 않는다."""
    cut = '{"analysis":"첫 문장이다. 두 번째 문장이다. 세 번째 문장은 여기서 잘'
    analyzer = _two_attempt_analyzer([cut, cut])

    result = analyzer.analyze("KR", "창", [{"index": 0, "title": "a"}])

    assert result["analysis"] == "첫 문장이다. 두 번째 문장이다."


def test_a_body_that_cannot_be_salvaged_still_falls_back_to_raw_titles(tmp_path):
    """건질 것이 없으면 실패다. 빈 본문으로 보고서를 만들지 않는다."""
    analyzer = _two_attempt_analyzer(["{잘린 쓰레기", "{잘린 쓰레기"])

    with pytest.raises(NewsReportError):
        analyzer.analyze("CN", "창", [{"index": 0, "title": "a"}])


def test_the_first_attempt_still_retries_before_salvaging_the_body(tmp_path):
    """건져내기는 마지막 시도에서만 한다. 온전한 JSON을 받을 기회를 먼저 준다."""
    broken = '{"analysis":"본문","highlights":[{"index":0,"title":"따옴표 "안" 제목"}]}'
    backend = _SequenceBackend([broken, json.dumps(_payload(), ensure_ascii=False)])
    analyzer = NewsReportAnalyzer(
        backend=backend, prompt_file=_prompt_file(), num_predict=2048, max_highlights=8
    )

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "a"}])

    assert len(backend.calls) == 2
    assert result["analysis"] == "현재 시장상황 요약이다."
    assert result["highlights"][0]["title"] == "한국어 제목 0"
