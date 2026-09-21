"""전역/개별 뉴스 원천 fetcher와 소스별 정규화 어댑터.

각 어댑터는 원천 DataFrame/RSS를 GlobalArticle 목록(최신순)으로 변환한다.
"""

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import quote_plus, urlparse

import akshare as ak
import curl_cffi
import requests
import requests.exceptions
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from telegram_bot.core.config import NEWS_SOURCE_ARTICLE_LIMIT

def retry_on_network(func):
    # AkShare 1.18+는 curl_cffi로 요청을 보내므로 curl_cffi 예외도 재시도 대상에
    # 포함해야 한다(requests.RequestException만 잡으면 재시도가 동작하지 않는다).
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(
            (
                requests.exceptions.RequestException,
                curl_cffi.CurlError,
            )
        ),
        reraise=True,
    )(func)


@dataclass(frozen=True)
class GlobalArticle:
    """소스와 무관한 전역 속보 1건."""

    article_id: str
    title: str
    content: str
    published_at: str
    published_date: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)


# ── 원천 fetcher ─────────────────────────────────────

@retry_on_network
def fetch_futu_raw():
    return ak.stock_info_global_futu()


@retry_on_network
def fetch_sina_raw():
    return ak.stock_info_global_sina()


@retry_on_network
def fetch_cls_raw():
    # "重点"은 财联社가 A/B 등급을 매긴 전보만 돌려준다. "全部"도 한 호출에 20건뿐이라
    # 등급 없는 잡음이 섞이면 실제로 남는 기사가 몇 건 되지 않는다.
    return ak.stock_info_global_cls(symbol="重点")


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

def _cell(row, key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    text = str(value)
    return "" if text.lower() in ("nan", "nat", "none") else text


def fetch_futu_articles() -> list[GlobalArticle]:
    df = fetch_futu_raw()
    articles = []
    for _, row in df.iterrows():
        title = _cell(row, "标题")
        content = _cell(row, "内容")
        published_at = _cell(row, "发布时间")
        if not (title or content):
            continue
        articles.append(
            GlobalArticle(
                article_id=f"{published_at}{content[:20]}",
                title=title,
                content=content,
                published_at=published_at,
                url=_cell(row, "链接"),
            )
        )
    return articles[:NEWS_SOURCE_ARTICLE_LIMIT]


def fetch_sina_articles() -> list[GlobalArticle]:
    df = fetch_sina_raw()
    articles = []
    for _, row in df.iterrows():
        content = _cell(row, "内容")
        published_at = _cell(row, "时间")
        if not content:
            continue
        articles.append(
            GlobalArticle(
                article_id=f"sina:{published_at}:{content[:20]}",
                title="",
                content=content,
                published_at=published_at,
            )
        )
    return articles[:NEWS_SOURCE_ARTICLE_LIMIT]


def fetch_cls_articles() -> list[GlobalArticle]:
    """财联社 전보(重点)를 최신순으로 정규화한다.

    akshare는 이 표를 발행 시각 **오름차순**으로 돌려주고 날짜와 시각을 두 열로
    쪼개 놓는다. 다른 어댑터와 달리 뒤집어야 최신순이 되고, `published_date`를
    따로 넘겨야 `parse_news_datetime`이 "14:33:21"만으로는 못 읽는 시각을 읽는다.
    """
    df = fetch_cls_raw()
    articles = []
    for _, row in df.iloc[::-1].iterrows():
        title = _cell(row, "标题")
        content = _cell(row, "内容")
        published_date = _cell(row, "发布日期")
        published_at = _cell(row, "发布时间")
        if not (title or content):
            continue
        articles.append(
            GlobalArticle(
                article_id=f"cls:{published_date} {published_at}:{(title or content)[:20]}",
                title=title,
                content=content,
                published_at=published_at,
                published_date=published_date,
            )
        )
    return articles[:NEWS_SOURCE_ARTICLE_LIMIT]


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
}
_DEFAULT_GOOGLE_NEWS_LOCALE = "hl=en-US&gl=US&ceid=US:en"


def _google_news_url(query: str, market: str = "") -> str:
    locale = _GOOGLE_NEWS_LOCALES.get(market.upper(), _DEFAULT_GOOGLE_NEWS_LOCALE)
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{locale}"


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
    "JP": "Japan stock market economy",
    "TW": "Taiwan stock market economy",
}
# 시장 전용 소스의 질의. 리서치 후보 발굴이 개별 종목 언급에 의존하므로
# 지수 시황뿐 아니라 종목·실적 질의를 함께 넣는다.
_MARKET_STOCK_NEWS_QUERIES = {
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
}

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
    return [
        GlobalArticle(
            article_id=f"gnews:{market}:{article.article_id}",
            title=article.title,
            content=article.content,
            published_at=article.published_at,
            published_date=article.published_date,
            url=article.url,
            extra={"market": market, "provider": "google-news"},
        )
        for article in articles
    ]


def fetch_google_news_global_articles() -> list[GlobalArticle]:
    """Public RSS source with market-specific queries."""
    markets = list(_REGIONAL_MARKET_QUERIES)
    with ThreadPoolExecutor(max_workers=len(markets)) as executor:
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
        _google_news_url(query, market),
        f"{prefix}:{query_index}",
        max_articles=limit,
    )
    return [
        GlobalArticle(
            article_id=f"{prefix}:{article.article_id}",
            title=article.title,
            content=article.content,
            published_at=article.published_at,
            published_date=article.published_date,
            url=article.url,
            extra={"market": market, "provider": "google-news"},
        )
        for article in articles
    ]


def fetch_google_news_stock_articles(market: str) -> list[GlobalArticle]:
    """시장 전용 Google News RSS(주기 다이제스트·리서치 공용)."""
    queries = _MARKET_STOCK_NEWS_QUERIES[market.upper()]
    per_query_limit = max(
        1,
        (NEWS_SOURCE_ARTICLE_LIMIT + len(queries) - 1) // len(queries),
    )
    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
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


def fetch_google_news_us_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("US")


def fetch_google_news_kr_stock_articles() -> list[GlobalArticle]:
    return fetch_google_news_stock_articles("KR")
