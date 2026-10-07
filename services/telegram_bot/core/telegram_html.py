"""Telegram HTML 메시지를 안전한 길이로 제한한다."""

import html
import re

_TAG_RE = re.compile(r"<[^>]*>")
_ELLIPSIS = "..."


def truncate_html(text: str, limit: int) -> str:
    """길이를 넘으면 서식을 제거하고 유효한 HTML 텍스트로 축약한다."""
    plain = html.unescape(_TAG_RE.sub("", text))
    if len(plain) <= limit:
        return text
    if limit <= len(_ELLIPSIS):
        return _ELLIPSIS[: max(0, limit)]

    visible = plain[: limit - len(_ELLIPSIS)]
    return html.escape(visible) + _ELLIPSIS


# 상태 표의 기호. ⚪는 VS16이 없으면 글자 꼴로 좁게 그리는 기기가 있어 붙여 둔다.
MARK_OK, MARK_WARN, MARK_FAIL, MARK_BUSY, MARK_NONE = "🟢", "🟡", "🔴", "🔵", "⚪️"


def status_table(rows: list[tuple[str, str, str]]) -> str:
    """(기호, 값, 항목) 줄을 고정폭 표(`<pre>`)로 만든다. 텔레그램 HTML에는 표가 없다.

    줄을 맞추는 칸(기호·값)에는 ASCII만 둔다. 고정폭 글꼴에서도 한글 한 자의 폭은 기기마다
    달라(대략 1.5~2칸) 한글이 든 칸 뒤로는 줄이 어긋난다. 그래서 한글 항목은 맨 끝 칸이다
    (운영자 요청 2026-10-08).
    """
    width = max((len(value) for _, value, _ in rows), default=0)
    body = "\n".join(f"{mark} {value.ljust(width)}  {label}".rstrip() for mark, value, label in rows)
    return f"<pre>{html.escape(body)}</pre>"
