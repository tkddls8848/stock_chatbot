import html

from services.telegram_bot.core.telegram_html import truncate_html


def test_truncate_html_keeps_short_markup():
    text = "<b>요약</b>\n정상 메시지"

    assert truncate_html(text, 100) == text


def test_truncate_html_returns_valid_escaped_text_when_over_limit():
    result = truncate_html(
        "<b>요약 &amp; 분석</b>\n" + "&lt;위험&gt;" * 20,
        40,
    )

    assert len(html.unescape(result)) == 40
    assert result.endswith("...")
    assert "<b>" not in result
    assert "<위험>" not in result
    assert "&lt;" in result


def test_truncate_html_counts_visible_text_after_entity_parsing():
    text = "<b>" + "&amp;" * 1000 + "</b>"

    assert truncate_html(text, 1000) == text
