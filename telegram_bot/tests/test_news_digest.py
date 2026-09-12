"""기사별 번역 다이제스트 경로(`news/pipeline.py`)의 계약.

**이 경로는 스케줄러가 부르지 않는다.** `fetch_all`을 호출하는 코드는 저장소에
없고, 예약 뉴스는 `collect_report_articles` → `run_news_report_job`(3시간
시장상황 보고서)만 돈다. 남아 있는 이유는 수동 실행용이며 CLAUDE.md가 그렇게
적어 두었다.

그래서 여기가 초록이어도 **예약 뉴스가 동작한다는 뜻이 아니다.** 예약 경로는
`test_news_report.py`가 본다. 다만 두 경로는 `collect_source_candidates`를
공유하므로 소스 실패 격리·발행시각 필터·사전선별 순서는 여기서도 같은 코드를
지난다.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from shared.core.config import (
    NEWS_DIGEST_ARTICLE_MAX_CHARS,
    NEWS_DIGEST_MESSAGE_MAX_CHARS,
    NEWS_DIGEST_TITLE_MAX_CHARS,
    TELEGRAM_MESSAGE_LIMIT,
)
from telegram_bot.news.pipeline import (
    PreparedGlobalArticle,
    archive_unsent_articles,
    select_digest_rows,
    send_global_digest,
)
from telegram_bot.state import SentNewsTracker
from telegram_bot.news.sources import GlobalArticle
from telegram_bot.news.utils import (
    chunk_message_items,
    compact_jst_time,
    compact_sentiment_line,
    filter_articles_for_jst_day,
    filter_recent_articles,
    format_china_time_as_jst,
    format_digest_article,
    truncate_at_sentence,
    truncate_text,
)


def test_truncate_text_remains_available_for_titles():
    result = truncate_text("가" * 150, 100)

    assert len(result) == 100
    assert result.endswith("...")


def test_truncate_text_keeps_short_text():
    assert truncate_text("짧은 기사 요약", 100) == "짧은 기사 요약"


def test_chunk_message_items_keeps_articles_whole_and_ordered():
    items = ["가" * 40, "나" * 40, "다" * 40]

    chunks = chunk_message_items(
        items,
        text_getter=lambda item: item,
        max_body_length=81,
        separator="\n",
    )

    assert chunks == [[items[0], items[1]], [items[2]]]
    assert [item for chunk in chunks for item in chunk] == items


class _RecordingBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class _NoopTracker:
    async def confirm(self, article_id):
        return None

    async def release(self, article_id):
        return None


class _RecordingTracker:
    def __init__(self):
        self.confirmed = []
        self.released = []

    async def confirm(self, article_id):
        self.confirmed.append(article_id)

    async def release(self, article_id):
        self.released.append(article_id)


class _RecordingLog:
    def __init__(self):
        self.records = []

    async def record(self, **kwargs):
        self.records.append(kwargs)


def _row(index, *, impact="", sentiment=None, source_key="futu", article_chars=100):
    from telegram_bot.news.registry import SourceSpec
    from shared.llm.translator import TranslationResult

    spec = SourceSpec(key=source_key, label="푸투", fetch=lambda: [])
    return PreparedGlobalArticle(
        spec=spec,
        article=GlobalArticle(
            article_id=f"{source_key}-{index}",
            title="제목",
            content="본문",
            published_at="2026-08-01 10:00:00",
        ),
        text="가" * article_chars,
        translated=TranslationResult("제목", "본문", [], sentiment, impact),
    )


def _prepared_rows(count, *, article_chars, source_key="futu"):
    return [
        _row(index, source_key=source_key, article_chars=article_chars)
        for index in range(count)
    ]


def test_digest_splits_one_oversized_source_across_messages():
    """소스 하나의 기사가 한 메시지에 안 들어가면 나눠 보낸다.

    chunk_message_items는 아이템을 쪼개지 못하므로, 섹션을 미리 나누지 않으면
    텔레그램 4096자 제한에 걸려 그 소스의 다이제스트가 통째로 실패한다.
    """
    bot = _RecordingBot()
    # 한 소스에 큰 기사 6건 → 단일 섹션이면 상한을 크게 넘는다.
    rows = _prepared_rows(6, article_chars=900)

    asyncio.run(
        send_global_digest(bot, "chat", rows, _NoopTracker(), None, None)
    )

    assert len(bot.messages) > 1
    for message in bot.messages:
        assert len(message) <= TELEGRAM_MESSAGE_LIMIT, len(message)
        assert len(message) <= NEWS_DIGEST_MESSAGE_MAX_CHARS
    # 기사는 하나도 잃지 않고 모두 실려야 한다.
    assert sum(message.count("가" * 900) for message in bot.messages) == 6
    # 소스 수는 섹션 분할과 무관하게 1곳으로 센다.
    assert "소스 1곳 · 새 기사 6건" in bot.messages[0]


def test_digest_keeps_small_sources_in_one_message():
    bot = _RecordingBot()
    rows = _prepared_rows(2, article_chars=200, source_key="sina")

    asyncio.run(
        send_global_digest(bot, "chat", rows, _NoopTracker(), None, None)
    )

    assert len(bot.messages) == 1
    assert "소스 1곳 · 새 기사 2건 · 1/1" in bot.messages[0]


@pytest.mark.xfail(
    strict=True,
    reason="successful sends are persisted only after the complete news cycle returns",
)
def test_successful_send_survives_immediate_process_restart(tmp_path):
    """전송 직후 종료돼도 새 tracker가 같은 기사를 다시 예약하면 안 된다."""
    path = tmp_path / "sent.json"
    tracker = SentNewsTracker(path)
    row = _row(0, impact="high", sentiment=0.8)

    asyncio.run(
        send_global_digest(
            _RecordingBot(),
            "chat",
            [row],
            tracker,
            None,
            None,
        )
    )

    restarted = SentNewsTracker(path)
    assert asyncio.run(restarted.reserve(row.article.article_id)) is False


def test_selection_sends_only_the_highest_impact_articles():
    """번역은 6건, 송출은 impact 상위 3건이다."""
    rows = [
        _row(0, impact="low", sentiment=0.9),
        _row(1, impact="high", sentiment=0.1),
        _row(2, impact="medium", sentiment=0.2),
        _row(3, impact="high", sentiment=0.8),
        _row(4, impact="low", sentiment=-0.5),
        _row(5, impact="medium", sentiment=-0.9),
    ]

    selected, dropped = select_digest_rows(rows, 3)

    assert [row.article.article_id for row in selected] == [
        "futu-1",  # high
        "futu-3",  # high
        "futu-5",  # medium 중 감성이 가장 센 건
    ]
    assert [row.article.article_id for row in dropped] == [
        "futu-0",
        "futu-2",
        "futu-4",
    ]


def test_selection_breaks_impact_ties_by_sentiment_strength_then_recency():
    rows = [
        _row(0, impact="high", sentiment=0.2),
        _row(1, impact="high", sentiment=-0.7),  # 세기는 부호와 무관하다
        _row(2, impact="high", sentiment=0.2),  # 같은 세기면 뒤쪽(최신)이 이긴다
    ]

    selected, dropped = select_digest_rows(rows, 2)

    assert [row.article.article_id for row in selected] == ["futu-1", "futu-2"]
    assert [row.article.article_id for row in dropped] == ["futu-0"]


def test_selection_pushes_missing_impact_or_sentiment_to_the_back():
    rows = [
        _row(0, impact="", sentiment=0.9),  # impact 없음 → 제일 뒤
        _row(1, impact="low", sentiment=None),  # sentiment 없음 → low 안에서 뒤
        _row(2, impact="low", sentiment=0.1),
    ]

    selected, dropped = select_digest_rows(rows, 2)

    assert [row.article.article_id for row in selected] == ["futu-1", "futu-2"]
    assert [row.article.article_id for row in dropped] == ["futu-0"]
    # low 안에서는 sentiment가 있는 쪽이 먼저다.
    assert select_digest_rows(rows, 1)[0][0].article.article_id == "futu-2"


def test_selection_keeps_everything_when_under_the_limit():
    rows = [_row(0, impact="low"), _row(1, impact="high")]

    selected, dropped = select_digest_rows(rows, 3)

    assert selected == rows
    assert dropped == []


def test_unsent_articles_are_confirmed_and_logged_without_release():
    """탈락분을 release하면 다음 주기에 다시 번역한다 — 확정하고 로그만 남긴다."""
    tracker = _RecordingTracker()
    news_log, prediction_log = _RecordingLog(), _RecordingLog()
    dropped = [_row(0, impact="low", sentiment=0.3), _row(1, impact="low", sentiment=0.2)]

    asyncio.run(archive_unsent_articles(dropped, tracker, prediction_log, news_log))

    assert tracker.confirmed == ["futu-0", "futu-1"]
    assert tracker.released == []
    assert len(news_log.records) == 2
    assert len(prediction_log.records) == 2


def test_send_failure_releases_only_the_sent_group():
    """다이제스트 전송이 실패해도 탈락분은 영향받지 않는다."""

    class _FailingBot:
        async def send_message(self, chat_id, text, parse_mode=None):
            raise RuntimeError("telegram down")

    tracker = _RecordingTracker()
    news_log, prediction_log = _RecordingLog(), _RecordingLog()
    rows = [
        _row(0, impact="low", sentiment=0.1),
        _row(1, impact="high", sentiment=0.5),
        _row(2, impact="high", sentiment=0.4),
        _row(3, impact="low", sentiment=0.2),
    ]
    selected, dropped = select_digest_rows(rows, 2)

    async def run():
        await send_global_digest(
            _FailingBot(), "chat", selected, tracker, prediction_log, news_log
        )
        await archive_unsent_articles(dropped, tracker, prediction_log, news_log)

    asyncio.run(run())

    # 송출 대상만 예약이 풀려 다음 주기에 다시 시도된다.
    assert sorted(tracker.released) == ["futu-1", "futu-2"]
    # 탈락분은 전송 경로를 타지 않았으므로 그대로 확정·기록된다.
    assert sorted(tracker.confirmed) == ["futu-0", "futu-3"]
    assert len(news_log.records) == 2


def test_long_model_response_is_capped_before_display():
    """모델이 상한보다 길게 답해도 기사 하나의 표시 분량은 확정된다.

    프롬프트는 본문을 200자 내외로 지시하지만 그것은 지시일 뿐이다. 길게
    답하는 주기가 섞여도 20분마다 올라오는 총량이 예측 가능해야 한다.
    """
    text = format_digest_article(
        "제" * 130,
        "본" * 450,
        "09:15:00 JST",
        "- 감성 : 긍정 +0.50 · 영향 높음",
        url="https://example.com/news?" + "a" * 470,
    )

    assert "제" * NEWS_DIGEST_TITLE_MAX_CHARS not in text
    assert "본" * NEWS_DIGEST_ARTICLE_MAX_CHARS not in text
    bot = _RecordingBot()
    rows = [
        PreparedGlobalArticle(
            spec=row.spec, article=row.article, text=text, translated=row.translated
        )
        for row in _prepared_rows(3, article_chars=1)
    ]

    asyncio.run(send_global_digest(bot, "chat", rows, _NoopTracker(), None, None))

    assert sum(message.count(text) for message in bot.messages) == 3
    for message in bot.messages:
        assert len(message) <= NEWS_DIGEST_MESSAGE_MAX_CHARS, len(message)


def test_china_article_timestamp_includes_date_and_time_in_jst():
    assert (
        format_china_time_as_jst("23:30:00", "2026-07-18")
        == "2026-07-19 00:30:00 JST"
    )


def test_rss_article_timestamp_respects_source_timezone():
    assert (
        format_china_time_as_jst("Fri, 17 Jul 2026 14:30:00 GMT")
        == "2026-07-17 23:30:00 JST"
    )


def test_recent_article_filter_drops_stale_unknown_and_future_items():
    now = datetime(2026, 7, 19, 12, tzinfo=timezone(timedelta(hours=9)))
    recent = GlobalArticle("recent", "recent", "", "Sun, 19 Jul 2026 01:00:00 GMT")
    stale = GlobalArticle("stale", "stale", "", "Fri, 19 Jun 2026 01:00:00 GMT")
    unknown = GlobalArticle("unknown", "unknown", "", "")
    future = GlobalArticle("future", "future", "", "Mon, 20 Jul 2026 12:00:00 GMT")

    assert filter_recent_articles(
        [stale, unknown, recent, future],
        max_age_hours=48,
        now=now,
    ) == [recent]


def test_calendar_day_filter_uses_jst_date_and_sorts_newest_first():
    early = GlobalArticle("early", "early", "", "Fri, 03 Jul 2026 15:30:00 GMT")
    late = GlobalArticle("late", "late", "", "Sat, 04 Jul 2026 10:00:00 GMT")
    previous = GlobalArticle("previous", "previous", "", "Fri, 03 Jul 2026 14:59:00 GMT")

    assert filter_articles_for_jst_day(
        [early, previous, late],
        datetime(2026, 7, 4).date(),
    ) == [late, early]


def test_article_display_keeps_only_jst_time():
    assert compact_jst_time("2026-07-19 09:15:00 JST") == "09:15:00 JST"
    assert compact_jst_time("2026-07-19 09:15 JST") == "09:15 JST"


def test_article_display_migrates_legacy_kst_label_without_shifting_time():
    assert compact_jst_time("2026-07-19 09:15:00 KST") == "09:15:00 JST"


def test_digest_article_uses_text_file_layout():
    assert format_digest_article(
        "기사 제목",
        "기사 본문",
        "09:15:00 JST",
        "- 감성 : 긍정 +0.50 · 영향 높음",
    ) == (
        "• 기사 제목 (09:15:00 JST)\n"
        "- 기사 본문\n"
        "- 감성 : 긍정 +0.50 · 영향 높음"
    )


def test_digest_article_caps_body_at_the_display_limit():
    body = format_digest_article("제목", "본" * 400, "09:15:00 JST").split("\n")[1]

    assert len(body) == len("- ") + NEWS_DIGEST_ARTICLE_MAX_CHARS
    assert body.endswith("...")


def test_digest_article_caps_title_at_the_display_limit():
    title_line = format_digest_article("제" * 150, "본문", "09:15:00 JST").split("\n")[0]

    assert title_line == (
        "• " + "제" * (NEWS_DIGEST_TITLE_MAX_CHARS - 3) + "... (09:15:00 JST)"
    )


def test_digest_article_truncates_before_escaping():
    """자른 뒤에 escape한다 — HTML 엔티티가 중간에서 잘리지 않는다.

    순서를 뒤집으면 `&amp;`가 `&am`으로 끊겨 텔레그램이 메시지 전체를
    파싱 오류로 거부한다.
    """
    body = format_digest_article("제목", "&" * 400, "09:15:00 JST").split("\n")[1]

    assert body.count("&amp;") == NEWS_DIGEST_ARTICLE_MAX_CHARS - 3
    assert body.endswith("...")
    assert "&am" not in body.replace("&amp;", "")


def test_digest_article_keeps_original_link_on_title():
    assert format_digest_article(
        "기사 제목",
        "기사 본문",
        "2026-07-19 09:15 JST",
        url="https://example.com/news?a=1&b=2",
    ).startswith(
        '• <a href="https://example.com/news?a=1&amp;b=2">기사 제목</a> '
    )


def test_body_is_cut_at_a_sentence_boundary_not_mid_word():
    """상한에 걸린 본문을 문장 한가운데에서 끊으면 반 문장만 남는다."""
    first = "가" * 140 + "다."
    body = first + " " + "두 번째 문장이 길게 이어진다" * 20

    result = truncate_at_sentence(body, NEWS_DIGEST_ARTICLE_MAX_CHARS)

    assert result == first


def test_body_falls_back_to_character_cut_when_the_sentence_ends_too_early():
    """경계가 너무 앞이면 버리는 내용이 더 많다. 그때는 글자로 자른다."""
    body = "짧다. " + "이어지는 긴 문장이 계속된다" * 30

    result = truncate_at_sentence(body, NEWS_DIGEST_ARTICLE_MAX_CHARS)

    assert result.endswith("...")
    assert len(result) == NEWS_DIGEST_ARTICLE_MAX_CHARS


def test_body_within_the_limit_is_untouched():
    assert truncate_at_sentence("한 문장이다. 두 문장이다.", 100) == "한 문장이다. 두 문장이다."


def test_digest_article_without_a_body_keeps_only_the_title_line():
    """야간 다이제스트는 제목만 옮긴다. 빈 본문 줄을 남기면 '- '만 보인다."""
    text = format_digest_article("제목", "", "09:15 JST", compact_sentiment_line(0.4, "high"))

    assert text.splitlines() == [
        "• 제목 (09:15 JST)",
        "- 감성 : 긍정 +0.40 · 영향 높음",
    ]


def test_quality_rejected_article_is_confirmed_not_released():
    """품질 미달은 다시 불러도 같은 응답이 온다 — release하면 매 주기 태운다."""
    from shared.llm.translator import TranslationError, TranslationQualityError
    from telegram_bot.news.pipeline import prepare_global_source
    from telegram_bot.news.registry import SourceSpec

    articles = [
        GlobalArticle(
            article_id=f"futu-{index}",
            title="标题",
            content="本文",
            published_at=datetime.now(timezone(timedelta(hours=9))).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        for index in range(2)
    ]
    spec = SourceSpec(key="futu", label="푸투", fetch=lambda: articles)

    class _Registry:
        def record_success(self, key):
            return None

    class _Translator:
        def translate_article(self, title, content):
            raise TranslationQualityError("not Korean")

    class _Tracker(_RecordingTracker):
        async def reserve(self, article_id):
            return True

    tracker = _Tracker()
    prepared = asyncio.run(
        prepare_global_source(
            spec,
            _Registry(),
            tracker,
            _Translator(),
            asyncio.Semaphore(1),
            {},
        )
    )

    assert prepared == []
    assert tracker.released == []
    assert sorted(tracker.confirmed) == ["futu-0", "futu-1"]
    # 형식 오류와 달라야 이 분기가 의미를 가진다.
    assert issubclass(TranslationQualityError, TranslationError)


def test_quality_rejects_stop_the_cycle_before_burning_the_scan_window():
    """소스가 통째로 나쁜 날 scan_limit까지 태우면 그 주기의 Neurons가 다 나간다."""
    from shared.core.config import (
        NEWS_GLOBAL_LIMIT,
        NEWS_TRANSLATION_QUALITY_REJECT_LIMIT,
    )
    from shared.llm.translator import TranslationQualityError
    from telegram_bot.news.pipeline import prepare_global_source
    from telegram_bot.news.registry import SourceSpec

    articles = [
        GlobalArticle(
            article_id=f"futu-{index}",
            title="标题",
            content="本文",
            published_at=datetime.now(timezone(timedelta(hours=9))).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        for index in range(NEWS_GLOBAL_LIMIT * 20)
    ]
    spec = SourceSpec(key="futu", label="푸투", fetch=lambda: articles)

    class _Registry:
        def record_success(self, key):
            return None

    class _Translator:
        def __init__(self):
            self.calls = 0

        def translate_article(self, title, content):
            self.calls += 1
            raise TranslationQualityError("not Korean")

    class _Tracker(_RecordingTracker):
        async def reserve(self, article_id):
            return True

    translator = _Translator()
    asyncio.run(
        prepare_global_source(
            spec,
            _Registry(),
            _Tracker(),
            translator,
            asyncio.Semaphore(1),
            {},
        )
    )

    assert translator.calls == NEWS_TRANSLATION_QUALITY_REJECT_LIMIT
