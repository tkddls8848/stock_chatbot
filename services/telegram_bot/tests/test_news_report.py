"""매시간 원문 수집과 시장상황 보고서, 그리고 발행 판정."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

from services.telegram_bot.core.clock import JST
from services.telegram_bot.features.news_summary import feature as news_feature
from services.telegram_bot.llm import news_report as news_report_llm
from services.telegram_bot.llm.news_report import NewsReportAnalyzer, NewsReportError
from services.telegram_bot.news.report import (
    collect_report_source,
    format_market_section,
    group_by_market,
    send_news_report,
)
from services.telegram_bot.news import report as news_report
from services.telegram_bot.news.registry import SourceSpec
from services.telegram_bot.news.sources import GlobalArticle
from services.telegram_bot.state import NewsReportMemory, NewsReportQueue, SentNewsTracker


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
        self.response_formats = []

    def generate(self, *, system_prompt, user_prompt, max_tokens, temperature,
                 response_format=None):
        if self.error is not None:
            raise self.error
        self.response_formats.append(response_format)
        self.calls.append(json.loads(user_prompt))
        return json.dumps(self.payload, ensure_ascii=False)


class _SequenceBackend:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, *, system_prompt, user_prompt, max_tokens, temperature,
                 response_format=None):
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
    from services.telegram_bot.core.config import NEWS_REPORT_PROMPT_FILE

    return NEWS_REPORT_PROMPT_FILE


def _queue(tmp_path, per_source_limit=12, max_items=600):
    return NewsReportQueue(
        tmp_path / "news_report_queue.json",
        per_source_limit=per_source_limit,
        max_items=max_items,
    )


@pytest.fixture(autouse=True)
def _publish_every_window(monkeypatch, tmp_path):
    """발행 판정은 「발행 판정」 절에서 따로 세운다.

    기본 하한(8건)을 그대로 두면 전송·확정·근거 로그를 보는 테스트가 전부
    보류로 빠져 무엇을 지키는 테스트인지 알 수 없게 된다.
    """
    monkeypatch.setattr(news_report, "NEWS_REPORT_MIN_ARTICLES", 1)
    from services.telegram_bot import publish as export

    monkeypatch.setattr(export, "NEWS_JSON", tmp_path / "public" / "news.json")
    monkeypatch.setattr(export, "META_JSON", tmp_path / "public" / "meta.json")


def _memory(tmp_path, **entries):
    memory = NewsReportMemory(tmp_path / "news_report_memory.json")
    memory._markets = dict(entries)
    return memory


def _published_entry(hours_ago, analysis="직전 보고서 본문이다."):
    moment = datetime.now(JST) - timedelta(hours=hours_ago)
    return {
        "published_at": moment.isoformat(timespec="seconds"),
        "seen_at": moment.isoformat(timespec="seconds"),
        "window": "00:00~03:00 UTC +9",
        "analysis": analysis,
        "held_windows": 0,
    }


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
        "publish": True,
        "hold_reason": "",
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


def test_queue_drop_removes_only_the_published_articles(tmp_path):
    """시장별로 발행을 판정하므로 큐를 통째로 비울 수 없다."""
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0, market="US"), _item(1, market="CN")]))

    removed = asyncio.run(queue.drop({"gnews_us-0"}))

    assert removed == 1
    opened_at, items = asyncio.run(queue.snapshot())
    assert [row["article_id"] for row in items] == ["gnews_us-1"]
    # 보류한 기사가 남아 있는 동안은 구간이 계속 열려 있다.
    assert opened_at


def test_queue_drop_closes_the_window_when_nothing_is_left(tmp_path):
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue([_item(0)]))

    asyncio.run(queue.drop({"gnews_us-0"}))

    saved = json.loads((tmp_path / "news_report_queue.json").read_text(encoding="utf-8"))
    assert saved["items"] == []
    assert saved["opened_at"] == ""


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


# ── 응답 형식 강제 ────────────────────────────────────

def test_the_request_carries_the_response_schema(tmp_path):
    """스키마를 싣지 않으면 모델이 형식을 지킬 이유가 없다.

    실측(2026-09-21)에서 이 모델이 스키마를 지켰고 제목의 큰따옴표가
    이스케이프되어 살아왔다. 입력 토큰이 양쪽 모두 같아 비용은 0이었다.
    """
    analyzer = _analyzer(tmp_path, _payload())

    analyzer.analyze("US", "창", [{"index": 0, "title": "t"}])

    sent = analyzer._backend.response_formats[0]
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["schema"] is news_report_llm.RESPONSE_SCHEMA


def test_the_schema_declares_every_field_the_parser_requires():
    """스키마와 파서가 갈라지면 모델이 형식은 지키고 검증은 실패한다."""
    schema = news_report_llm.RESPONSE_SCHEMA
    assert set(schema["required"]) == {
        "publish", "hold_reason", "analysis", "highlights", "evaluations"
    }
    highlight = schema["properties"]["highlights"]["items"]
    assert set(highlight["required"]) == {
        "index", "title", "sentiment", "impact", "mentioned_stocks"
    }
    # 파서가 받는 값과 같은 범위·열거여야 한다(`_parse_highlight`).
    assert highlight["properties"]["sentiment"]["minimum"] == -1
    assert highlight["properties"]["sentiment"]["maximum"] == 1
    assert highlight["properties"]["impact"]["enum"] == ["high", "medium", "low"]
    evaluation = schema["properties"]["evaluations"]["items"]
    assert evaluation["properties"]["impact"]["enum"] == ["high", "medium", "low"]


def test_a_schema_shaped_response_passes_the_parser_untouched(tmp_path):
    """스키마가 허용하는 응답은 파서도 그대로 받아야 한다.

    스키마는 모양만 보장한다 — index가 이번에 보낸 것 중 하나인지 같은 의미
    검증은 여전히 파서 몫이고, 두 층이 어긋나면 형식이 맞는데도 버려진다.
    """
    payload = {
        "publish": True,
        "hold_reason": "",
        "analysis": "반도체 장비주가 국면을 이끈다.",
        "highlights": [{
            "index": 0,
            "title": '애플 "비전 프로" 수요가 예상을 넘었다',
            "sentiment": -1,
            "impact": "low",
            "mentioned_stocks": ["AAPL"],
        }],
        "evaluations": [{"index": 1, "impact": "high"}],
    }
    analyzer = _analyzer(tmp_path, payload)

    result = analyzer.analyze(
        "US", "창", [{"index": 0, "title": "a"}, {"index": 1, "title": "b"}]
    )

    assert result["publish"] is True
    # 큰따옴표가 든 제목이 그대로 실린다 — 구조화 출력으로 얻는 실익이다.
    assert result["highlights"][0]["title"] == '애플 "비전 프로" 수요가 예상을 넘었다'
    assert result["highlights"][0]["sentiment"] == -1.0


# ── 시장 분류와 섹션 ──────────────────────────────────

def test_markets_are_grouped_in_display_order():
    grouped = group_by_market([_item(0, market="KR"), _item(1, market="CN")])

    assert [market for market, _ in grouped] == ["CN", "KR"]


def test_failed_market_still_shows_its_headlines():
    """분석이 실패해도 그 시장의 수집 뉴스를 통째로 잃지 않는다."""
    section = format_market_section("US", [_item(0)], None)

    assert "요약 생성 실패" in section
    assert "Headline 0" in section


def test_highlights_show_only_title_and_sentiment():
    """근거 기사에는 링크·발행 시각 없이 제목과 감성만 붙는다."""
    item = {**_item(0), "url": "https://example.com/a"}
    result = {
        "analysis": "판단.",
        "highlights": [{"index": 0, "title": "제목", "sentiment": 0.4, "impact": "high"}],
    }

    section = format_market_section("US", [item], result)

    assert "href" not in section
    assert section.split("\n\n")[-1].splitlines() == [
        "• 제목",
        "- 감성 : 긍정 +0.40 · 영향 높음",
    ]


# ── 전송 ──────────────────────────────────────────────

def _send_app(tmp_path, *, bot=None, analyzer=None, memory=None):
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
        news_report_memory=memory if memory is not None else _memory(tmp_path),
    )
    return app, queue, tracker, news_log


def test_report_sends_confirms_and_clears_the_queue(tmp_path):
    app, queue, tracker, news_log = _send_app(tmp_path)

    asyncio.run(send_news_report(app))

    assert "시장상황 보고서" in app.bot.messages[0]
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
    monkeypatch.setattr("services.telegram_bot.news.report.NEWS_DIGEST_MESSAGE_MAX_CHARS", 240)
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


def test_a_failed_analysis_is_held_for_the_next_window(tmp_path):
    """분석 실패는 원문 제목 나열이 아니라 보류다. 다음 구간이 같은 기사로 다시 본다."""
    app, queue, tracker, _ = _send_app(
        tmp_path, analyzer=_analyzer(tmp_path, error=RuntimeError("cloudflare down"))
    )

    asyncio.run(send_news_report(app))

    assert app.bot.messages == []
    assert tracker.confirmed == []
    assert len(asyncio.run(queue.snapshot())[1]) == 2


def test_a_failed_analysis_still_shows_headlines_at_the_hold_ceiling(tmp_path):
    """상한까지 왔는데도 분석이 안 되면 그 시간의 뉴스를 통째로 잃지 않는다."""
    memory = _memory(tmp_path, US=_published_entry(hours_ago=13))
    app, queue, tracker, _ = _send_app(
        tmp_path,
        analyzer=_analyzer(tmp_path, error=RuntimeError("cloudflare down")),
        memory=memory,
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


# ── 발행 판정 ─────────────────────────────────────────

def _gate_app(tmp_path, *, items, analyzer=None, memory=None, prefilter=None):
    queue = _queue(tmp_path)
    asyncio.run(queue.enqueue(items))
    tracker = _RecordingTracker()
    return _App(
        news_report_queue=queue,
        news_report_analyzer=analyzer or _analyzer(tmp_path, _payload()),
        sent_tracker=tracker,
        news_log=_RecordingLog(),
        news_prefilter=prefilter,
        news_report_memory=memory if memory is not None else _memory(tmp_path),
    ), queue, tracker


def test_a_thin_window_is_held_without_calling_the_llm(tmp_path, monkeypatch):
    """재료가 얇으면 부르지 않는다. 억지로 쓴 한 편이 같은 국면을 반복한다."""
    monkeypatch.setattr(news_report, "NEWS_REPORT_MIN_ARTICLES", 8)
    analyzer = _analyzer(tmp_path, _payload())
    app, queue, tracker = _gate_app(
        tmp_path, items=[_item(index) for index in range(3)], analyzer=analyzer
    )

    asyncio.run(send_news_report(app))

    assert analyzer._backend.calls == []
    assert app.bot.messages == []
    # 보류한 기사는 버려지지 않고 다음 구간의 재료가 된다.
    assert len(asyncio.run(queue.snapshot())[1]) == 3
    assert tracker.confirmed == []


def test_a_thick_window_passes_the_first_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(news_report, "NEWS_REPORT_MIN_ARTICLES", 8)
    analyzer = _analyzer(tmp_path, _payload())
    app, queue, tracker = _gate_app(
        tmp_path, items=[_item(index) for index in range(8)], analyzer=analyzer
    )

    asyncio.run(send_news_report(app))

    assert len(analyzer._backend.calls) == 1
    assert app.bot.messages


def test_the_hold_ceiling_publishes_a_thin_window(tmp_path, monkeypatch):
    """보류는 기다리는 것이지 덮는 것이 아니다. 상한을 넘기면 발행한다.

    사전선별의 라벨 공급원이 이 보고서 하나뿐이라, 끝나지 않는 보류는 학습선을
    조용히 끊는다.
    """
    monkeypatch.setattr(news_report, "NEWS_REPORT_MIN_ARTICLES", 8)
    analyzer = _analyzer(tmp_path, _payload())
    memory = _memory(tmp_path, US=_published_entry(hours_ago=13))
    app, queue, tracker = _gate_app(
        tmp_path, items=[_item(0)], analyzer=analyzer, memory=memory
    )

    asyncio.run(send_news_report(app))

    assert app.bot.messages
    assert analyzer._backend.calls[0]["must_publish"] is True


def test_the_model_can_hold_a_window_that_adds_nothing(tmp_path):
    """같은 국면이 이어지기만 하는 구간은 보내지 않는다. 침묵도 출력이다."""
    payload = {"publish": False, "hold_reason": "직전 판단이 그대로다",
               "analysis": "", "highlights": [], "evaluations": []}
    memory = _memory(tmp_path, US=_published_entry(hours_ago=3))
    app, queue, tracker = _gate_app(
        tmp_path,
        items=[_item(0), _item(1)],
        analyzer=_analyzer(tmp_path, payload),
        memory=memory,
    )

    asyncio.run(send_news_report(app))

    assert app.bot.messages == []
    assert tracker.confirmed == []
    assert len(asyncio.run(queue.snapshot())[1]) == 2
    assert memory.held_windows("US") == 1
    # 보류해도 직전 발행분은 그대로 남아 다음 호출의 비교 대상이 된다.
    assert memory.previous("US")["analysis"] == "직전 보고서 본문이다."


def test_a_held_window_still_feeds_the_prefilter_label(tmp_path):
    """호출은 이미 나갔다. 보류가 길어지는 동안 라벨이 마르면 학습이 멈춘다."""
    payload = {"publish": False, "hold_reason": "새로운 것이 없다", "analysis": "",
               "highlights": [], "evaluations": [{"index": 0, "impact": "high"}]}
    prefilter = _RecordingPrefilter()
    app, _, _ = _gate_app(
        tmp_path,
        items=[{**_item(0), "prefilter_candidate_id": "cand-1"}],
        analyzer=_analyzer(tmp_path, payload),
        memory=_memory(tmp_path, US=_published_entry(hours_ago=3)),
        prefilter=prefilter,
    )

    asyncio.run(send_news_report(app))

    assert prefilter.outcomes == [("cand-1", "high", None)]


def test_one_market_publishes_while_another_holds(tmp_path):
    """시장마다 발행 시점이 다르다. 발행한 시장의 기사만 확정하고 뺀다."""
    calls = []

    class _PerMarketBackend:
        def generate(self, *, system_prompt, user_prompt, max_tokens, temperature,
                     response_format=None):
            request = json.loads(user_prompt)
            calls.append(request)
            if request["market"] == "CN":
                return json.dumps(
                    {"publish": False, "hold_reason": "그대로다", "analysis": "",
                     "highlights": [], "evaluations": []},
                    ensure_ascii=False,
                )
            return json.dumps(_payload(), ensure_ascii=False)

    analyzer = NewsReportAnalyzer(
        backend=_PerMarketBackend(),
        prompt_file=_prompt_file(),
        num_predict=2048,
        max_highlights=8,
    )
    app, queue, tracker = _gate_app(
        tmp_path,
        items=[_item(0, market="US"), _item(1, market="CN")],
        analyzer=analyzer,
    )

    asyncio.run(send_news_report(app))

    assert len(app.bot.messages) == 1
    assert "미국" in app.bot.messages[0]
    assert "중국 본토" not in app.bot.messages[0]
    assert tracker.confirmed == ["gnews_us-0"]
    _, remaining = asyncio.run(queue.snapshot())
    assert [row["article_id"] for row in remaining] == ["gnews_us-1"]


def test_the_previous_report_is_sent_to_the_model(tmp_path):
    """무상태로 부르면 모델은 비교 대상이 없어 같은 국면을 새 얘기처럼 다시 쓴다."""
    analyzer = _analyzer(tmp_path, _payload())
    memory = _memory(tmp_path, US=_published_entry(hours_ago=3, analysis="반도체가 국면이다."))
    app, _, _ = _gate_app(tmp_path, items=[_item(0)], analyzer=analyzer, memory=memory)

    asyncio.run(send_news_report(app))

    assert analyzer._backend.calls[0]["previous"]["analysis"] == "반도체가 국면이다."
    assert analyzer._backend.calls[0]["must_publish"] is False


def test_the_first_report_of_a_market_has_no_previous(tmp_path):
    analyzer = _analyzer(tmp_path, _payload())
    app, _, _ = _gate_app(tmp_path, items=[_item(0)], analyzer=analyzer)

    asyncio.run(send_news_report(app))

    assert analyzer._backend.calls[0]["previous"] is None


def test_publishing_records_the_body_for_the_next_window(tmp_path):
    memory = _memory(tmp_path)
    app, _, _ = _gate_app(
        tmp_path,
        items=[_item(0)],
        analyzer=_analyzer(tmp_path, _payload(analysis="이번 구간의 판단이다.")),
        memory=memory,
    )

    asyncio.run(send_news_report(app))

    assert memory.previous("US")["analysis"] == "이번 구간의 판단이다."
    assert memory.held_windows("US") == 0


def test_a_failed_send_does_not_record_the_report_as_published(tmp_path):
    """보내지 못한 글을 발행으로 기억하면 다음 보고서가 읽히지 않은 글과 견준다."""
    memory = _memory(tmp_path)
    app, queue, tracker = _gate_app(tmp_path, items=[_item(0)], memory=memory)
    app.bot = _RecordingBot(fail=True)

    asyncio.run(send_news_report(app))

    assert memory.previous("US") is None
    assert tracker.confirmed == []
    assert len(asyncio.run(queue.snapshot())[1]) == 1


def test_only_the_first_highlights_are_shown_but_all_are_logged(tmp_path, monkeypatch):
    """본문이 판단이고 목록은 그 각주다. 줄이는 것은 표시 분량이지 라벨이 아니다."""
    monkeypatch.setattr(news_report, "NEWS_REPORT_SHOWN_HIGHLIGHTS", 2)
    items = [_item(index) for index in range(5)]
    news_log = _RecordingLog()
    app, _, _ = _gate_app(
        tmp_path,
        items=items,
        analyzer=_analyzer(tmp_path, _payload(indexes=(0, 1, 2, 3, 4))),
    )
    app.bot_data["news_log"] = news_log

    asyncio.run(send_news_report(app))

    message = app.bot.messages[0]
    assert "한국어 제목 1" in message
    assert "한국어 제목 4" not in message
    assert "기사 3건 더" in message
    # 표시에서 뺀 근거도 라벨은 그대로 간다.
    assert len(news_log.records) == 5


def test_a_market_section_shows_how_long_it_has_been_silent(tmp_path):
    memory = _memory(tmp_path, US=_published_entry(hours_ago=9))
    app, _, _ = _gate_app(tmp_path, items=[_item(0)], memory=memory)

    asyncio.run(send_news_report(app))

    assert "마지막 보고 이후 9시간" in app.bot.messages[0]


def test_publish_defaults_to_true_when_the_model_omits_the_verdict(tmp_path):
    """판정 필드 하나가 빠졌다는 이유로 한 구간을 침묵하지 않는다."""
    payload = _payload()
    del payload["publish"]
    analyzer = _analyzer(tmp_path, payload)

    result = analyzer.analyze("US", "창", [{"index": 0, "title": "t"}])

    assert result["publish"] is True
    assert result["analysis"] == "현재 시장상황 요약이다."


def test_a_hold_verdict_drops_the_body_and_the_highlights(tmp_path):
    """보류분은 사용자가 보지 않는다. 들고 있으면 다음 구간이 읽힌 글로 착각한다."""
    payload = _payload(analysis="쓰다 만 판단")
    payload["publish"] = False
    payload["hold_reason"] = "직전과 같다"
    payload["evaluations"] = [{"index": 0, "impact": "low"}]

    result = _analyzer(tmp_path, payload).analyze(
        "US", "창", [{"index": 0, "title": "t"}]
    )

    assert result["publish"] is False
    assert result["analysis"] == ""
    assert result["highlights"] == []
    assert result["hold_reason"] == "직전과 같다"
    # 학습 평가는 보류해도 남는다.
    assert result["evaluations"] == [{"index": 0, "impact": "low"}]


def test_must_publish_overrides_a_hold_verdict(tmp_path):
    payload = _payload()
    payload["publish"] = False

    result = _analyzer(tmp_path, payload).analyze(
        "US", "창", [{"index": 0, "title": "t"}], None, True
    )

    assert result["publish"] is True
    assert result["analysis"] == "현재 시장상황 요약이다."


def test_memory_survives_a_restart(tmp_path):
    memory = NewsReportMemory(tmp_path / "news_report_memory.json")
    asyncio.run(memory.record_published("US", "00:00~03:00 UTC +9", "판단이다."))
    asyncio.run(memory.record_held("CN", "재료가 얇다"))

    reloaded = NewsReportMemory(tmp_path / "news_report_memory.json")

    assert reloaded.previous("US")["analysis"] == "판단이다."
    assert reloaded.held_windows("CN") == 1
    assert reloaded.previous("CN") is None


def test_a_market_that_never_published_still_reaches_the_ceiling(tmp_path):
    """한 번도 발행하지 못한 시장이 상한에 영영 닿지 않으면 보류가 끝나지 않는다."""
    memory = NewsReportMemory(tmp_path / "news_report_memory.json")
    asyncio.run(memory.record_held("CN", "재료가 얇다"))
    memory._markets["CN"]["held_since"] = (
        (datetime.now(JST) - timedelta(hours=13)).isoformat(timespec="seconds")
    )

    assert memory.held_hours("CN") >= 13


def test_publishing_clears_the_hold_streak(tmp_path):
    memory = NewsReportMemory(tmp_path / "news_report_memory.json")
    asyncio.run(memory.record_held("US", "재료가 얇다"))
    asyncio.run(memory.record_published("US", "창", "판단이다."))

    assert memory.held_windows("US") == 0
    assert memory.held_hours("US") < 1


def test_jobs_collect_hourly_and_report_every_three_hours_utc_plus_9():
    scheduler = _RecordingScheduler()

    news_feature._install_jobs(scheduler, object())

    jobs = {kwargs["id"]: kwargs for _, kwargs in scheduler.jobs}
    assert jobs["news_collection"]["trigger"] == "interval"
    assert jobs["news_collection"]["minutes"] == 60
    assert jobs["market_situation_report"]["trigger"] == "cron"
    assert jobs["market_situation_report"]["hour"] == "*/4"
    assert jobs["market_situation_report"]["minute"] == 0
    assert jobs["market_situation_report"]["timezone"] is JST


def test_initial_collection_runs_after_telegram_startup_delay(monkeypatch):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from services.telegram_bot.core.clock import now

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

    assert "마지막 보고 이후" in prompt
    assert "시장상황" in prompt
    assert "UTC +9" in prompt
    assert "기사를 차례로 번역하거나 나열하지 않는다" in prompt
    assert "야간" not in prompt


def test_report_prompt_asks_for_a_publication_verdict_against_the_previous_report():
    """정해진 시간마다 한 편을 채우는 것이 목적이 아니라는 것이 이 프롬프트의 전제다."""
    prompt = _prompt_file().read_text(encoding="utf-8")

    assert "쓸 말이 있을 때만 쓴다" in prompt
    assert "publish" in prompt
    assert "hold_reason" in prompt
    assert "previous" in prompt
    assert "must_publish" in prompt


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
    from services.telegram_bot.news.utils import display_time

    assert display_time("2026-09-05 14:30 JST") == "14:30 UTC +9"
    assert display_time("2026-09-05 14:30 KST") == "14:30 UTC +9"
    # 형식이 다르면 손대지 않는다.
    assert display_time("알 수 없음") == "알 수 없음"


# ── 사전선별 라벨 공급 ────────────────────────────────

class _RecordingPrefilter:
    """`record_outcome` 호출만 받아 적는 최소 대역."""

    def __init__(self):
        self.outcomes = []

    async def record_outcome(self, *, candidate_id, impact, sentiment, selected=True):
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
    asyncio.run(news_report._record_evaluations("CN", _queued(), result, prefilter))
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
    from services.telegram_bot.news.models import SourceCandidate

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


def test_report_exports_only_published_news_to_public_search(tmp_path):
    app, queue, _, _ = _send_app(tmp_path)
    for item in queue._items:
        item["published_at"] = datetime.now(JST).isoformat()
        item["url"] = "https://example.com/article"
    asyncio.run(send_news_report(app))
    payload = json.loads((tmp_path / "public" / "news.json").read_text(encoding="utf-8"))
    rows = payload["documents"]
    assert any(row["kind"] == "report" for row in rows)
    assert any(row["title"] == "한국어 제목 0" for row in rows)
    assert any(row["url"] == "https://example.com/article" for row in rows)
    assert all("mentioned_stocks" not in row for row in rows)


def test_public_export_failure_does_not_repeat_telegram_delivery(tmp_path, monkeypatch):
    from services.telegram_bot import publish as export

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(export, "publish_news", fail)
    app, queue, tracker, _ = _send_app(tmp_path)
    asyncio.run(send_news_report(app))
    assert len(app.bot.messages) == 1
    assert len(tracker.confirmed) == 2
    assert asyncio.run(queue.snapshot())[1] == []
