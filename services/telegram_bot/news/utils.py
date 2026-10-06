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

from services.telegram_bot.core.clock import JST as _JST
from services.telegram_bot.core.config import (
    NEWS_DIGEST_ARTICLE_MAX_CHARS,
    NEWS_DIGEST_TITLE_MAX_CHARS,
    NEWS_SENTIMENT_ENABLED,
)
from services.telegram_bot.llm.translator import TranslationResult, TranslationService

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


# 속보 표시로 **시작하는** 제목. 운영자 결정(2026-10-02): 목적은 빠른 취득이 아니라
# 사건과 그 함의의 분석이라 속보는 쓰지 않는다. 제목 중간의 낱말("속보 이후 반등")은
# 거르지 않는다 — 앞머리 표지만 본다. 전각 괄호·공백·대소문자를 함께 받는다.
_FLASH_PREFIX = re.compile(
    r"^\s*(?:[\[【〔(（<＜]\s*(?:속보|긴급|速報|速报|快讯|快訊|突发|突發|breaking(?:\s+news)?)\s*[\]】〕)）>＞]"
    r"|(?:속보|速報|速报|快讯|快訊|breaking(?:\s+news)?)\s*[:：|｜])",
    re.IGNORECASE,
)
# Google News 제목 끝의 " - 매체명"을 매체명을 모른 채 떼는 휴리스틱. **사전선별 벡터에만** 쓴다
# (`features/news_prefilter/optimizer.py`) — 화면·보고서 제목은 RSS `<source>`와 정확히 같을 때만
# 뗀다(`news/sources.py`). 2026-10-02 서버 관측 11,755건 중 6,327건이 이 꼬리를 달았고, 한 번만 나온
# 꼬리 40개 표본은 전부 매체명이었다(news1.kr, 세계일보, BusinessLine …). 표본 무오탐이 전체 무오탐은
# 아니다: 사전선별은 새 제목에도 이 규칙을 쓰므로 제목 자체의 마지막 " - 구절"(60자 이하)도
# 벡터에서는 빠질 수 있다. 영향은 사전선별 점수의 문자 n-gram 일부에 그친다.
_PUBLISHER_TAIL = re.compile(r"\s+[-–—]\s+[^-–—]{1,60}$")


# 기사가 아니라 종목 시세 화면인 제목. Google News 질의("株価 終値" 등)가 Yahoo 시세 페이지를
# 기사처럼 돌려준다 — 2026-10-02 큐에서 일본 130건 중 32건, 미국 110건 중 11건이었다.
# 분석할 사건이 없다. 실제로 본 두 형식만 정확히 잡는다(일반 "주가" 낱말은 거르지 않는다).
_QUOTE_PAGE = re.compile(r"[：:]\s*株価・株式情報|\bStock Price, News, Quote & History\b"
                         # 같은 부류의 한국어 자동 생성 글(TradingKey). 등락률 하나로 찍어 낸 제목이다
                         # — "브로드컴 (AVGO) 주식 움직였습니다 상승 3.26%에 10월2일: 변동 원인",
                         # "Bitcoin(BTCUSD) 종목이 10월4일에 갑자기 1.01% 상승한 …".
                         r"|주식 (?:움직였|시작했|마감했)습니다 (?:상승|하락)|\) 종목이 \d{1,2}월\s?\d{1,2}일에")
# 도박 광고 상표가 기사 제목 **앞에** 붙어 오는 경우(2026-10-02 중국 표본: "必威买球泽连斯基…",
# "ManBetx手机版武契奇…", "yabovip188登录武契奇…"). 앞머리만 본다 — 제목 중간의 상표명은 그 회사의
# 기사일 수 있고, "博彩"는 마카오 카지노주 기사에 정상적으로 쓰여 넣지 않는다.
_SPAM = re.compile(r"^\s*(?:必威|买球|manbetx(?:手机|app|官网|登录)|yabo(?:vip|\d)|亚博体育|开云体育|乐鱼体育|华体会)", re.IGNORECASE)
# 한국어 도박 SEO 글. 2026-10-02부터 `gnews_kr` 질의에 쏟아졌다 — 10-06까지 KR 큐에 담긴 1,615건 중
# 196건이었고 15건은 보고서 근거로 공개됐다("규칙 문구의 이상과 초과, 로얄 슬롯 조건 해석").
# 기사 꼴을 흉내 낸 "게임 안내문"이라 도박 낱말과 안내문 낱말이 함께 나온다. 도박 쪽만 보면 정상
# 기사를 버린다: 카지노주(강원랜드·파라다이스)·"외국인 1.3조 베팅"·포커스·게임주가 실제로 같이 들어온다.
# 그래서 이 시장 기사에 안 쓰는 낱말(_GAMBLING)은 하나로, 정상 기사에도 쓰는 낱말(_GAMBLING_TERM)은
# 안내문 낱말(_GAME_GUIDE)과 같이 나올 때만 거른다. 끝의 묶음은 같은 매체가 찍어 내는 상투 문구다.
_GAMBLING = re.compile(
    r"바카라|먹튀|토토|가입\s?코드|꽁\s?머니|프리\s?스핀|홀덤|슬롯\s?머신|블랙\s?잭|고스톱|무료\s?(?:슬롯|룰렛)"
    r"|배팅\s?업체|온라인\s?카지노|카지노\s?(?:사이트|추천)"
    r"|학습 전략과 자원|위험 피하기와 최적화|입문부터 마스터까지|브랜드 구축: 기초|의 모든 측면: 장점"
    r"|기술과 비밀 풀기|의 비밀을 풀다: 전문가|궁금증을 해소하는 완벽한 가이드|위한 필수 정보 및 팁")
_GAMBLING_TERM = re.compile(r"카지노(?!주)|슬롯|룰렛|도박|경마|포커(?!스)|마작")
_GAME_GUIDE = re.compile(
    r"심벌|배당표|버튼|안내|해설|규칙|기능|보너스|디시|용어|기초|입문|초보|플레이|문답|가이드|공략|연출"
    r"|라운드|화면|설정|아이콘|팝업|알림창|로딩|도움말|접속|도메인|후기|표시|설명|메뉴|현금화|연습|분류"
    r"|요소|최신판|대회 참가|게임(?![주사업株])")
# 한국어 홍보 글. 셋 다 사건이 아니라 상품을 판다(2026-10-02~06 KR 큐 31건).
# ① 종목 추천 서비스 광고 — 매경 `[MK시그널]`("매도신호 포착, 수익률 78.5% 달성"), "【AI골든봇】 추천종목
#    공개", Investing.com의 "AI가 고른 이 기술주들". ② 연합뉴스 `[게시판]` — 기업이 보낸 보도자료를 모아
#    싣는 난이다. ③ 금융사·유통사의 고객 이벤트("ISA 이벤트 진행", "현금 경품"). "대형 이벤트 앞두고"처럼
#    시장 일정을 이벤트라 부르는 기사는 남는다 — 판촉 낱말이 앞뒤에 붙을 때만 거른다.
_PROMOTION = re.compile(
    r"\[MK시그널\]|(?:매수|매도)\s?신호 포착|추천\s?종목 공개|▶▶|AI가 고른|AI 종목 선정 전략|AI 추천주"
    r"|^\s*\[게시판\]"
    r"|(?:가입|개설|거래|고객|기념|환영|사은|증정|축하|감사)\s?이벤트|이벤트\s?(?:진행|실시|시작|전개)|경품|사은품")
# 한국어 제목의 그림 문자("[과매도 우량주 리포트] 🚨RSI …"). 국내 매체 기사 제목은 그림 문자를 쓰지 않는다.
# 다른 언어 제목에는 적용하지 않는다 — 그쪽 관측은 없다.
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_HANGUL = re.compile(r"[가-힣]")
# 기사를 퍼 와 도박 광고를 끼워 넣는 매체. 같은 날 표본의 광고 제목이 전부 이 매체였다.
# 뒤의 둘은 한국어 도박 SEO 글만 내는 해외 사이트다(제목 규칙을 빠져나가는 "카지노 여자배우" 같은 글도 낸다).
_SPAM_PUBLISHERS = frozenset({"Pchome电脑之家", "Calgary Roughnecks", "Histoire pour tous"})


def is_flash_title(title: str) -> bool:
    """속보 표시로 시작하는 제목인가."""
    return bool(_FLASH_PREFIX.match(str(title or "")))


def is_advertisement(title: str, publisher: str = "") -> bool:
    """광고·홍보 글인가: 도박 광고(중국어 상표·한국어 SEO 글), 종목 추천 광고, 보도자료 난, 판촉 이벤트."""
    text = str(title or "")
    if _SPAM.search(text) or str(publisher or "") in _SPAM_PUBLISHERS:
        return True
    if not _HANGUL.search(text):
        return False
    return bool(_GAMBLING.search(text) or (_GAMBLING_TERM.search(text) and _GAME_GUIDE.search(text))
                or _PROMOTION.search(text) or _EMOJI.search(text))


def analyzable_articles(articles: list) -> list:
    """분석 재료가 되는 기사만 남긴다: 제목이 있고, 속보 표시로 시작하지 않고, 시세 화면·광고가 아닌 기사.

    보고서 수집(사전선별 전)과 리서치 수집이 같은 규칙을 쓴다. 제목 없는 항목은
    보고서가 근거로 고를 수도, 원문과 대조할 수도 없다.
    """
    return [article for article in articles
            if str(article.title or "").strip() and not is_flash_title(article.title)
            and not _QUOTE_PAGE.search(article.title)
            and not is_advertisement(article.title, (getattr(article, "extra", None) or {}).get("publisher"))]


def strip_publisher_tail(title: str) -> str:
    """끝의 " - 매체명"을 뗀 제목. 같은 꼬리가 거듭되면 모두 뗀다("… - 머니투데이 - 머니투데이").
    남는 제목이 너무 짧으면 그대로 둔다."""
    text = str(title or "")
    match = _PUBLISHER_TAIL.search(text)
    if match is None:
        return text
    tail, stripped = match.group(0), text[: match.start()]
    while stripped.endswith(tail):
        stripped = stripped[: -len(tail)]
    return stripped if len(stripped.strip()) >= 8 else text


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
    text = f"• {alert}{safe_title}"
    if published_time:
        text += f" ({html.escape(published_time)})"
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
