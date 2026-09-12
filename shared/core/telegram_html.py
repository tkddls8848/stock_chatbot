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
