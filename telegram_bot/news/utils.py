"""뉴스 처리 공용 헬퍼(상태 없음).

시간 포맷, 텔레그램 메시지 빌드, 비동기 번역 래퍼, 타임아웃 판별,
회전 배치 선택 등 뉴스 파이프라인과 리서치 수집이 공유하는 순수 함수.
"""

import asyncio
import html
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar

import pandas as pd

from shared.core.clock import JST as _JST
from shared.core.config import (
    NEWS_DIGEST_ARTICLE_MAX_CHARS,
    NEWS_DIGEST_TITLE_MAX_CHARS,
    NEWS_SENTIMENT_ENABLED,
)
from shared.llm.translator import TranslationResult, TranslationService

T = TypeVar("T")
# 기사 시각의 소스 타임존. 중국 뉴스·시세 제공처는 현지 시각(CST)을 준다.
# 여기서 JST로 변환하며, 앱의 "지금"은 core/clock.py가 따로 담당한다.
_CHINA_TZ = timezone(timedelta(hours=8))


def parse_news_datetime(
    published_at: Any,
    published_date: Any | None = None,
) -> datetime | None:
    """Parse a source publication timestamp and normalize it to JST.

    RFC/RSS timestamps keep their declared timezone. Timestamps without a
    timezone are treated as China Standard Time because the stock and Chinese
    news providers return local China time.
    """
    raw_time = str(published_at or "").strip()
    raw_date = str(published_date or "").strip()
    raw = f"{raw_date} {raw_time}".strip() if raw_date else raw_time
    if not raw or (not raw_date and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw)):
        return None

    if isinstance(published_at, datetime) and not raw_date:
        parsed_datetime = published_at
    else:
        try:
            parsed_datetime = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = pd.to_datetime(raw, errors="coerce")
            if pd.isna(parsed):
                return None
            parsed_datetime = parsed.to_pydatetime()

    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=_CHINA_TZ)
    return parsed_datetime.astimezone(_JST)


def publication_time_naive(
    published_at: Any,
    published_date: Any | None = None,
) -> datetime | None:
    """Return a JST publication timestamp in the log's naive ISO format."""
    parsed = parse_news_datetime(published_at, published_date)
    return parsed.replace(tzinfo=None) if parsed is not None else None


def recent_publication_time(
    published_at: Any,
    published_date: Any | None = None,
    max_age_hours: int = 48,
    now: datetime | None = None,
) -> datetime | None:
    """Return the JST publication time only when it is inside the live window."""
    published = parse_news_datetime(published_at, published_date)
    if published is None:
        return None
    current = now or datetime.now(_JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_JST)
    else:
        current = current.astimezone(_JST)
    cutoff = current - timedelta(hours=max(1, max_age_hours))
    if cutoff <= published <= current + timedelta(hours=1):
        return published
    return None


def filter_recent_articles(
    articles: list[T],
    max_age_hours: int,
    now: datetime | None = None,
) -> list[T]:
    """Keep articles inside one shared live-news window, newest first."""
    dated: list[tuple[datetime, T]] = []
    for article in articles:
        published = recent_publication_time(
            getattr(article, "published_at", None),
            getattr(article, "published_date", None),
            max_age_hours,
            now,
        )
        if published is not None:
            dated.append((published, article))
    dated.sort(key=lambda item: item[0], reverse=True)
    return [article for _, article in dated]


def filter_articles_for_jst_day(
    articles: list[T],
    target_day: date,
) -> list[T]:
    """Keep only articles whose actual publication date is the requested JST day."""
    dated: list[tuple[datetime, T]] = []
    for article in articles:
        published = parse_news_datetime(
            getattr(article, "published_at", None),
            getattr(article, "published_date", None),
        )
        if published is not None and published.date() == target_day:
            dated.append((published, article))
    dated.sort(key=lambda item: item[0], reverse=True)
    return [article for _, article in dated]


def filter_articles_in_window(
    articles: list[T],
    start: datetime,
    end: datetime,
) -> list[T]:
    """Keep articles in the half-open JST interval ``[start, end)``."""
    window_start = start if start.tzinfo is not None else start.replace(tzinfo=_JST)
    window_end = end if end.tzinfo is not None else end.replace(tzinfo=_JST)
    window_start = window_start.astimezone(_JST)
    window_end = window_end.astimezone(_JST)
    dated: list[tuple[datetime, T]] = []
    for article in articles:
        published = parse_news_datetime(
            getattr(article, "published_at", None),
            getattr(article, "published_date", None),
        )
        if published is not None and window_start <= published < window_end:
            dated.append((published, article))
    dated.sort(key=lambda item: item[0])
    return [article for _, article in dated]


def format_china_time_as_jst(
    published_at: Any,
    published_date: Any | None = None,
) -> str:
    raw_time = str(published_at or "").strip()
    raw_date = str(published_date or "").strip()
    raw = f"{raw_date} {raw_time}".strip() if raw_date else raw_time
    if not raw:
        return "JST"

    try:
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", raw):
            # 날짜 없이 시각만 온 경우다. 붙일 날짜가 없으니 tzinfo를 달아도
            # 의미가 없고, CST→JST는 고정 +1시간(양쪽 다 서머타임이 없다)이라
            # 시간 산술이 정확하다. 그래서 naive strptime을 의도적으로 쓴다.
            fmt = "%H:%M:%S" if raw.count(":") == 2 else "%H:%M"
            converted = datetime.strptime(raw, fmt) + timedelta(hours=1)  # noqa: DTZ007
            return f"{converted.strftime(fmt)} JST"

        converted = parse_news_datetime(published_at, published_date)
        if converted is None:
            return f"{raw} JST"
        fmt = (
            "%Y-%m-%d %H:%M:%S" if re.search(r":\d{2}:\d{2}", raw) else "%Y-%m-%d %H:%M"
        )
        return f"{converted.strftime(fmt)} JST"
    except Exception:
        return f"{raw} JST"


OFFSET_LABEL = "UTC +9"


def compact_jst_time(formatted_time: str) -> str:
    """날짜가 포함된 JST 문자열에서 기사 표시용 시간만 남긴다."""
    # 전환 전에 큐·로그에 저장된 KST 표기도 UTC +9라 값 변환 없이 JST로
    # 다시 표기할 수 있다. 기존 야간 큐를 비우지 않고 배포할 수 있게 받는다.
    match = re.search(r"(\d{1,2}:\d{2}(?::\d{2})?\s+(?:JST|KST))$", formatted_time)
    return match.group(1).replace("KST", "JST") if match else formatted_time


def display_time(formatted_time: str) -> str:
    """화면에 내보낼 시각 라벨. 저장 문자열의 `JST`를 표기용으로 바꾼다.

    값은 그대로다 — `JST`도 `KST`도 UTC +9라 변환이 아니라 표기만 바꾼다.
    저장 쪽(`compact_jst_time`)은 건드리지 않는다: 큐·로그에 이미 `JST`·`KST`로
    적힌 문자열을 계속 파싱해야 한다. 읽는 사람에게 일본 표준시로 보이지 않게
    하는 것이 목적이므로 표시 직전에만 바꾼다.
    """
    return compact_jst_time(formatted_time).replace("JST", OFFSET_LABEL)


def compact_sentiment_line(sentiment: float | None, impact: str = "") -> str:
    """기사 아래에 붙는 감성·영향 한 줄. 주간 번역과 야간 요약이 함께 쓴다."""
    if not NEWS_SENTIMENT_ENABLED:
        return ""
    if sentiment is None:
        return "- 감성 : 분석 불가"
    if sentiment >= 0.15:
        marker = "긍정"
    elif sentiment <= -0.15:
        marker = "부정"
    else:
        marker = "중립"
    impact_labels = {"high": "높음", "medium": "중간", "low": "낮음"}
    impact_part = f" · 영향 {impact_labels[impact]}" if impact in impact_labels else ""
    return f"- 감성 : {marker} {sentiment:+.2f}{impact_part}"


def format_digest_article(
    title: str,
    content: str,
    published_time: str,
    sentiment_line: str = "",
    alert: str = "",
    url: str = "",
) -> str:
    """요청한 기사 3줄 형식으로 텔레그램 HTML을 만든다.

    제목과 본문을 표시 상한에서 자른다. 프롬프트가 본문을 200자 내외로
    지시하지만 모델이 길게 답하는 주기가 섞이면 한 번에 올라오는 총량이
    다시 부풀기 때문에, 상한은 여기서 확정한다.

    본문은 문장 경계에서 자른다. 제목은 그대로 글자 수로 자른다 — 제목에는
    끊을 문장 경계가 없다. 본문이 비면 본문 줄 자체를 빼고 제목 줄만 만든다.
    """
    safe_title = html.escape(truncate_text(title, NEWS_DIGEST_TITLE_MAX_CHARS))
    content = truncate_at_sentence(content, NEWS_DIGEST_ARTICLE_MAX_CHARS)
    if url:
        safe_title = f'<a href="{html.escape(url)}">{safe_title}</a>'

    # 야간 다이제스트는 제목만 옮기고 본문을 만들지 않는다. 빈 본문 줄을
    # 남기면 "- "만 있는 줄이 그대로 보인다.
    text = f"• {alert}{safe_title} ({html.escape(published_time)})"
    if content:
        text += f"\n- {html.escape(content)}"
    if sentiment_line:
        text += f"\n{sentiment_line}"
    return text


_SENTENCE_END_RE = re.compile(r"[.!?。！？](?=\s|$)|다\.")


def truncate_at_sentence(text: str, max_chars: int) -> str:
    """상한 안의 마지막 문장 끝에서 자른다. 경계가 너무 앞이면 글자로 자른다.

    프롬프트가 "문장을 중간에 끊지 않는다"를 지시해도 모델이 길게 답한 주기는
    표시 상한에 걸려 결국 문장 한가운데에서 끊긴다. 그러면 읽는 쪽에는 무슨
    말인지 알 수 없는 반 문장이 남으므로, 상한 안의 마지막 마침표까지만 쓴다.
    경계가 상한의 절반도 되지 않으면 버리는 내용이 더 많아지므로 그때는 기존
    글자 절단으로 돌아간다(말줄임표가 잘렸음을 알린다).
    """
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    window = normalized[:max_chars]
    cut = 0
    for match in _SENTENCE_END_RE.finditer(window):
        cut = match.end()
    if cut >= max_chars // 2:
        return window[:cut].rstrip()
    return truncate_text(normalized, max_chars)


def truncate_text(text: str, max_chars: int) -> str:
    """텍스트를 문자 수 상한 안에 두고, 잘린 경우 말줄임표를 붙인다."""
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 3:
        return normalized[:max_chars]
    return normalized[: max_chars - 3].rstrip() + "..."


def chunk_message_items(
    items: list[T],
    text_getter: Callable[[T], str],
    max_body_length: int,
    separator: str,
) -> list[list[T]]:
    """기사 내부를 자르지 않고 지정된 본문 길이 이하의 묶음으로 나눈다."""
    chunks: list[list[T]] = []
    current: list[T] = []
    current_length = 0

    for item in items:
        item_length = len(text_getter(item))
        added_length = item_length + (len(separator) if current else 0)
        if current and current_length + added_length > max_body_length:
            chunks.append(current)
            current = [item]
            current_length = item_length
        else:
            current.append(item)
            current_length += added_length

    if current:
        chunks.append(current)
    return chunks


async def translate_article(
    translator: TranslationService,
    semaphore: asyncio.Semaphore,
    title: str,
    content: str,
) -> TranslationResult:
    async with semaphore:
        return await asyncio.to_thread(
            translator.translate_article,
            title,
            content,
        )


def is_timeout_error(error: Exception) -> bool:
    return "timed out" in str(error).lower() or "timeout" in str(error).lower()


def normalize_stock_code(raw: Any) -> str:
    """숫자 코드를 시장 규칙에 맞게 0패딩한다(HK 5자리, A주 6자리)."""
    code = str(raw or "").strip()
    digits = "".join(ch for ch in code if ch.isdigit())
    if not digits:
        return code
    return digits.zfill(5) if len(digits) <= 5 else digits.zfill(6)


_SIGNAL_CODE_RE = re.compile(r"\d{5,6}")
_GLOBAL_TICKER_RE = re.compile(r"[A-Za-z][A-Za-z0-9.-]{0,14}")


def signal_codes(raw_codes: list | None) -> list[str]:
    """신호 로그에 기록할 종목코드만 남긴다(정규화 후 5~6자리 숫자, 중복 제거).

    LLM이 추출한 mentioned_stocks에는 미국 티커(JNJ.N, NVDA 등)처럼
    A주·홍콩 코드 형식이 아닌 값이 섞일 수 있어 기록 전에 걸러낸다.
    """
    codes: list[str] = []
    for raw in raw_codes or []:
        code = normalize_stock_code(raw)
        if _SIGNAL_CODE_RE.fullmatch(code):
            candidate = code
        else:
            raw_text = str(raw or "").strip().upper()
            candidate = raw_text if _GLOBAL_TICKER_RE.fullmatch(raw_text) else ""
        if candidate and candidate not in codes:
            codes.append(candidate)
    return codes
