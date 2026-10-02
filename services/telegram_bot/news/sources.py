"""뉴스 원천 fetcher와 소스별 정규화 어댑터.

각 어댑터는 원천 RSS를 GlobalArticle 목록(최신순)으로 변환한다. 속보(7×24 快讯)
어댑터 넷(futu·sina·cls·em_global)은 2026-10-02에 퇴역했다 — 이 시스템의 목적은 빠른
취득이 아니라 사건과 그 함의의 분석이다(운영자 결정). 되살릴 일은 git에서 꺼내는 별도 변경이다.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote_plus, urlparse

import requests

from services.telegram_bot.core.config import NEWS_SOURCE_ARTICLE_LIMIT
from services.telegram_bot.core.workers import ShutdownThreadPool


@dataclass(frozen=True)
class GlobalArticle:
    """소스와 무관한 기사 1건."""

    article_id: str
    title: str
    content: str
    published_at: str
    published_date: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)


# ── 원천 fetcher ─────────────────────────────────────

def fetch_rss_raw(url: str) -> bytes:
    host = urlparse(url).netloc.lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; StockChatbotRSS/1.0)",
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }
    if host.endswith("mk.co.kr"):
        headers["Referer"] = "https://www.mk.co.kr/rss/"
    response = requests.get(url, headers=headers, timeout=(3, 5))
    response.raise_for_status()
    return response.content


# ── 정규화 어댑터(최신순 반환) ───────────────────────

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub(" ", text or "")).strip()


def fetch_rss_articles(
    url: str,
    label: str,
    max_articles: int | None = NEWS_SOURCE_ARTICLE_LIMIT,
) -> list[GlobalArticle]:
    import feedparser

    feed = feedparser.parse(fetch_rss_raw(url))
    articles = []
    for entry in feed.entries[:max_articles]:
        title = _strip_html(str(entry.get("title") or ""))
        content = _strip_html(str(entry.get("summary") or entry.get("description") or ""))
        published_at = str(entry.get("published") or entry.get("updated") or "")
        link = str(entry.get("link") or "")
        raw_source = entry.get("source") or {}
        source_name = (
            str(raw_source.get("title") or "")
            if isinstance(raw_source, dict)
            else str(getattr(raw_source, "title", "") or "")
        )
        if not (title or content):
            continue
        unique = str(entry.get("id") or link or f"{published_at}:{title[:20]}")
        articles.append(
            GlobalArticle(
                article_id=f"rss:{label}:{unique}",
                title=title,
                content=content[:1500],
                published_at=published_at,
                url=link,
                extra={"source": source_name},
            )
        )
    return articles


# 시장별 Google News 로케일. 현지 언어로 질의해야 그 시장 종목이 실제로
# 기사 본문에 등장한다(한국 기사를 영어로 질의하면 종목명이 사라진다).
_GOOGLE_NEWS_LOCALES = {
    "KR": "hl=ko&gl=KR&ceid=KR:ko",
    "HK": "hl=zh-HK&gl=HK&ceid=HK:zh-Hant",
    # 일본어 로케일로 받아야 종목명이 현지 표기(트요타가 아니라 トヨタ)로 남아
    # 사전선별의 종목 매칭과 리서치 후보 발굴이 본문에서 이름을 찾을 수 있다.
    "JP": "hl=ja&gl=JP&ceid=JP:ja",
    # 유럽은 공용 언어가 없어 영어로 질의하되 영국판 로케일로 받는다. 미국판은
    # 같은 질의에도 월가 기사가 앞을 채운다.
    "EU": "hl=en-GB&gl=GB&ceid=GB:en",
}
_DEFAULT_GOOGLE_NEWS_LOCALE = "hl=en-US&gl=US&ceid=US:en"
# 시장 전용 질의(`_MARKET_STOCK_NEWS_QUERIES`)에만 쓰는 로케일. 위 표에 CN을 넣으면
# `gnews`의 CN 영어 질의까지 간체 로케일로 바뀐다 — 그 질의는 Bloomberg·SCMP 같은
# 영어 분석 기사를 받는 자리라 그대로 둔다.
_STOCK_QUERY_LOCALES = {"CN": "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"}


def _google_news_url(query: str, market: str = "", locale: str = "") -> str:
    locale = locale or _GOOGLE_NEWS_LOCALES.get(market.upper(), _DEFAULT_GOOGLE_NEWS_LOCALE)
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale}"


def _without_publisher(article: GlobalArticle) -> tuple[str, str]:
    """(매체명을 뗀 제목, 매체명). Google News 제목은 끝에 " - 매체명"을 단다.

    RSS `<source>`가 준 매체명과 **정확히 같은** 꼬리만 뗀다. 다르면 제목을 건드리지
    않는다 — "정책 - 시행 전"처럼 제목 자체의 하이픈을 자르지 않기 위해서다.
    매체명은 화면의 출처로 남기고, 분석 모델에는 보내지 않는다(code_guide).
    """
    publisher = str(article.extra.get("source") or "").strip()
    suffix = f" - {publisher}"
    title = article.title
    # 매체가 자기 이름을 제목에 넣고 Google이 한 번 더 붙이는 경우가 있다
    # (2026-10-02 실측: "… - 머니투데이 - 머니투데이"). 같은 꼬리는 되풀이해 뗀다.
    while publisher and title.endswith(suffix) and len(title) > len(suffix):
        title = title[: -len(suffix)].rstrip()
    return title, publisher


def fetch_google_news_history(query: str, day: date, market: str) -> list[GlobalArticle]:
    """Fetch articles for one closed calendar day from Google News RSS.

    This is used only to complete a missing chart history.  The query is
    date-bounded so it cannot silently substitute today's headlines for a
    historical point.
    """
    previous_day = date.fromordinal(day.toordinal() - 1)
    next_day = date.fromordinal(day.toordinal() + 1)
    bounded_query = (
        f"{query} after:{previous_day.isoformat()} before:{next_day.isoformat()}"
    )
    return fetch_rss_articles(
        _google_news_url(bounded_query),
        f"history:{market}:{day.isoformat()}",
        max_articles=None,
    )


_REGIONAL_MARKET_QUERIES = {
    "CN": "China stock market economy",
    # 보고서와 감성 차트는 HK를 시장으로 갖고 있는데 HK 기사를 내는 소스가 없어
    # 그 칸이 늘 비어 있었다. 번체 로케일로 질의해야 현지 종목명이 본문에 남는다.
    "HK": "香港股市 恒生指數 經濟",
    "EU": "European stock market economy",
    "RU": "Russia stock market economy",
    "KR": "Korea stock market economy",
    "JP": "日本株 日経平均 経済",
    "TW": "Taiwan stock market economy",
}
# 시장 전용 소스의 질의. 리서치 후보 발굴이 개별 종목 언급에 의존하므로
# 지수 시황뿐 아니라 종목·실적 질의를 함께 넣는다.
_MARKET_STOCK_NEWS_QUERIES = {
    # 중국은 속보(7×24 快讯)를 빼고 사건의 함의를 다루는 기사로 채운다(운영자 결정
    # 2026-10-02 — 목적은 빠른 취득이 아니라 함의의 분석이다). 간체 로케일로 받는다.
    # 2026-10-02 실측(국경절 휴장 중)으로 골랐다: "A股 政策" 32건(정책·업종 분석 위주),
    # "人民银行 货币政策" 8건(재련사 조간 정리·주택대출 이자 지원), "中国经济 政策 影响" 34건(분석 섞임).
    # 뺀 질의: "A股 市场 分析 解读"는 TradingKey의 미국 개별 종목 기사("AMD股票…原因全解读")가 섞이고,
    # 证监会·人民币汇率·宏观数据는 홍콩·말레이시아·ECB·아이폰 기사가 절반이었다. 영어
    # "Chinese stocks outlook"은 나이키 등 무관 기사가 대부분이었다.
    "CN": (
        "A股 政策 when:1d",
        "人民银行 货币政策 when:1d",
        "中国经济 政策 影响 when:1d",
    ),
    "US": (
        "US stock market Wall Street when:1d",
        "S&P 500 Nasdaq stocks earnings when:1d",
        "US companies stock market news when:1d",
    ),
    "KR": (
        "코스피 증시 마감 when:1d",
        "코스닥 종목 실적 when:1d",
        "한국 증시 상승 종목 when:1d",
    ),
    "JP": (
        "日経平均 株価 終値 when:1d",
        "東証 プライム 決算 銘柄 when:1d",
        "日本株 上昇 銘柄 when:1d",
    ),
    "EU": (
        "European stocks STOXX 600 DAX CAC when:1d",
        "ECB interest rates eurozone economy when:1d",
        "European companies earnings shares when:1d",
    ),
}

def _google_article(article: GlobalArticle, article_id: str, market: str) -> GlobalArticle:
    """Google News 항목을 시장 기사로 바꾼다. 매체명은 제목이 아니라 extra에 둔다."""
    title, publisher = _without_publisher(article)
    # Google News의 RSS 요약은 본문이 아니라 "제목  매체명"이다. 리서치가 이 요약을 모델에
    # 넣으므로 끝의 매체명도 뗀다(매체명은 분석에 보내지 않는다).
    content = article.content
    if publisher and content.endswith(publisher):
        content = content[: -len(publisher)].rstrip(" \xa0")
    return GlobalArticle(
        article_id=article_id,
        title=title,
        content=content,
        published_at=article.published_at,
        published_date=article.published_date,
        url=article.url,
        # 사전선별의 사건 군집은 매체명을 떼기 전 원문으로 계속 비교한다. 사건 메모리가 그
        # 원문으로 쌓여 있어, 뗀 제목으로 비교하면 같은 사건을 새 사건으로 본다(2026-10-02 실측:
        # 서버 사건 메모리 5,050건에 대고 370건 중 179건). 화면·모델에는 쓰지 않는다.
        extra={"market": market, "provider": "google-news", "publisher": publisher,
               "event_title": article.title, "event_content": article.content},
    )


def _interleave(groups: list[list[GlobalArticle]]) -> list[GlobalArticle]:
    result: list[GlobalArticle] = []
    highest = max((len(group) for group in groups), default=0)
    for index in range(highest):
        for group in groups:
            if index < len(group):
                result.append(group[index])
    return result


def _deduplicate_articles(articles: list[GlobalArticle]) -> list[GlobalArticle]:
    unique = []
    seen = set()
    for article in articles:
        identity = article.url or re.sub(r"\s+", " ", article.title).strip().lower()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        unique.append(article)
    return unique


def _fetch_google_news_market(market: str) -> list[GlobalArticle]:
    query = _REGIONAL_MARKET_QUERIES[market]
    market_count = len(_REGIONAL_MARKET_QUERIES)
    per_market_limit = max(1, (NEWS_SOURCE_ARTICLE_LIMIT + market_count - 1) // market_count)
    articles = fetch_rss_articles(
        _google_news_url(query, market),
        f"gnews:{market}",
        max_articles=per_market_limit,
    )
    return [_google_article(article, f"gnews:{market}:{article.article_id}", market) for article in articles]


def fetch_google_news_global_articles() -> list[GlobalArticle]:
    """Public RSS source with market-specific queries."""
    markets = list(_REGIONAL_MARKET_QUERIES)
    with ShutdownThreadPool(max_workers=len(markets)) as executor:
        futures = {market: executor.submit(_fetch_google_news_market, market) for market in markets}
        groups = []
        for market in markets:
            try:
                groups.append(futures[market].result())
            except Exception:
                groups.append([])
    return _deduplicate_articles(_interleave(groups))[:NEWS_SOURCE_ARTICLE_LIMIT]


def _fetch_google_news_stock_query(
    market: str,
    query: str,
    query_index: int,
    limit: int,
) -> list[GlobalArticle]:
    # article_id 접두사(gnews-us 등)는 sent_ids 호환을 위해 유지한다.
    prefix = f"gnews-{market.lower()}"
    articles = fetch_rss_articles(
        _google_news_url(query, market, _STOCK_QUERY_LOCALES.get(market, "")),
        f"{prefix}:{query_index}",
        max_articles=limit,
    )
    return [_google_article(article, f"{prefix}:{article.article_id}", market) for article in articles]


def fetch_google_news_stock_articles(market: str) -> list[GlobalArticle]:
    """시장 전용 Google News RSS(주기 다이제스트·리서치 공용)."""
    queries = _MARKET_STOCK_NEWS_QUERIES[market.upper()]
    per_query_limit = max(
        1,
        (NEWS_SOURCE_ARTICLE_LIMIT + len(queries) - 1) // len(queries),
    )
    with ShutdownThreadPool(max_workers=len(queries)) as executor:
        futures = [
            executor.submit(
                _fetch_google_news_stock_query,
                market.upper(),
                query,
                index,
                per_query_limit,
            )
            for index, query in enumerate(queries)
        ]
        groups = []
        for future in futures:
            try:
                groups.append(future.result())
            except Exception:
                groups.append([])
    return _deduplicate_articles(_interleave(groups))[:NEWS_SOURCE_ARTICLE_LIMIT]


def fetch_google_news_cn_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("CN")


def fetch_google_news_us_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("US")


def fetch_google_news_kr_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("KR")


def fetch_google_news_jp_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("JP")


def fetch_google_news_eu_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("EU")
