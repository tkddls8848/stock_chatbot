"""`news/utils.py`의 표시·시각·자르기 계약.

예약 뉴스가 화면에 내보내는 모든 문자열이 이 함수들을 지난다
(`news/report.py`의 3시간 시장상황 보고서). 자르기·이스케이프 순서나 시각
표기가 바뀌면 텔레그램 메시지가 깨지거나 링크가 사라지므로 여기서 고정한다.
"""

from datetime import datetime, timedelta, timezone

from shared.core.config import (
    NEWS_DIGEST_ARTICLE_MAX_CHARS,
    NEWS_DIGEST_TITLE_MAX_CHARS,
)
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
