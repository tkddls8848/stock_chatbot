import html

from services.telegram_bot.core.telegram_html import MARK_FAIL, MARK_OK, status_table, truncate_html


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


def test_status_table_aligns_only_ascii_columns_and_puts_korean_last():
    """텔레그램 HTML에는 표가 없어 고정폭 `<pre>`로 그린다. 한글 폭은 기기마다 달라 한글은 맨 끝 칸이다(2026-10-08)."""
    table = status_table([(MARK_OK, "10/08 07:40", "시장 감성"), (MARK_FAIL, "HTTP 403", "국토부 · <사유>")])

    assert table == ("<pre>🟢 10/08 07:40  시장 감성\n"
                     "🔴 HTTP 403     국토부 · &lt;사유&gt;</pre>")
