"""세션 창 뉴스와 전일 지수 등락을 수집해 아노말리 원자료를 저장한다."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import urlsplit, urlunsplit

from shared.core.clock import now
from shared.core.config import (
    MARKET_ANOMALY_INDEX_TICKERS,
    MARKET_ANOMALY_MAX_HEADLINES,
    MARKET_ANOMALY_MIN_ARTICLES,
    MARKET_ANOMALY_MIN_SOURCES,
    MARKET_CHART_MARKETS,
    NEWS_MARKET_BACKFILL_QUERIES,
)
from shared.core.workers import burst_job
from telegram_bot.features.market_sentiment.window import MarketSessionWindow, completed_windows
from shared.llm.overnight_tone import OvernightToneError
from telegram_bot.news.sources import fetch_google_news_history
from telegram_bot.news.utils import filter_articles_in_window

logger = logging.getLogger(__name__)
QUERY_VERSION = "google-rss-session-v1"
_TITLE_SPACE_RE = re.compile(r"\s+")


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return str(url or "")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def _normalized_title(title: str) -> str:
    return _TITLE_SPACE_RE.sub(" ", str(title or "").strip().casefold())


def _publisher(article) -> str:
    source = str((getattr(article, "extra", {}) or {}).get("source") or "").strip()
    if source:
        return source
    title = str(getattr(article, "title", "") or "")
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    host = urlsplit(str(getattr(article, "url", "") or "")).netloc.lower()
    return host or "unknown"


def select_headlines(articles: list, limit: int = MARKET_ANOMALY_MAX_HEADLINES) -> list:
    """Deduplicate and interleave publishers so one outlet cannot dominate."""
    seen_urls = set()
    seen_titles = set()
    groups: dict[str, list] = defaultdict(list)
    for article in articles:
        title = _normalized_title(getattr(article, "title", ""))
        if not title:
            continue
        canonical_url = _canonical_url(getattr(article, "url", ""))
        if title in seen_titles or (canonical_url and canonical_url in seen_urls):
            continue
        seen_titles.add(title)
        if canonical_url:
            seen_urls.add(canonical_url)
        groups[_publisher(article)].append(article)
    publisher_cap = max(1, math.ceil(limit * 0.25))
    groups = {
        publisher: group[:publisher_cap]
        for publisher, group in groups.items()
    }
    selected = []
    publishers = sorted(groups)
    index = 0
    # round-robin makes the selected prefix balanced even when the raw feed is not.
    while publishers and len(selected) < limit:
        remaining = []
        for publisher in publishers:
            group = groups[publisher]
            if index < len(group):
                selected.append(group[index])
                if len(selected) >= limit:
                    break
            if index + 1 < len(group):
                remaining.append(publisher)
        publishers = remaining
        index += 1
    return selected


def index_return(ticker: str, price_session: date) -> float:
    """Return close-to-close percent change ending at ``price_session``."""
    import yfinance as yf

    frame = yf.Ticker(ticker).history(
        start=price_session - timedelta(days=10),
        end=price_session + timedelta(days=2),
        auto_adjust=False,
        actions=False,
    )
    if frame.empty or "Close" not in frame:
        raise RuntimeError(f"no index history for {ticker} {price_session}")
    closes = frame["Close"].dropna()
    eligible = closes[closes.index.date <= price_session]
    if len(eligible) < 2:
        raise RuntimeError(f"not enough closes for {ticker} {price_session}")
    previous, current = float(eligible.iloc[-2]), float(eligible.iloc[-1])
    if not math.isfinite(previous) or not math.isfinite(current) or previous == 0:
        raise RuntimeError(f"invalid closes for {ticker} {price_session}")
    return (current / previous - 1.0) * 100.0


async def _fetch_window_articles(window: MarketSessionWindow, query: str) -> list:
    days = sorted({window.start.date(), window.end.date()})
    batches = await asyncio.gather(
        *(
            asyncio.to_thread(
                fetch_google_news_history,
                query,
                day,
                window.market,
            )
            for day in days
        )
    )
    return filter_articles_in_window(
        [article for batch in batches for article in batch],
        window.start,
        window.end,
    )


async def capture_window(
    app,
    window: MarketSessionWindow,
    *,
    record_insufficient: bool = False,
) -> bool:
    store = app.bot_data.get("overnight_tone_store")
    analyzer = app.bot_data.get("overnight_tone_analyzer")
    # 이름은 "market_digest"지만 다이제스트(/market)와 같은 Cloudflare 무료
    # 할당량을 공유하는 락이라 여기서도 빌려 쓴다(feature.py 설치부 참고).
    semaphore = app.bot_data.get("market_digest_semaphore")
    if store is None or analyzer is None or semaphore is None:
        return False
    if await store.contains(window.market, window.price_session):
        return False
    query = NEWS_MARKET_BACKFILL_QUERIES.get(window.market)
    ticker = MARKET_ANOMALY_INDEX_TICKERS.get(window.market)
    if not query or not ticker:
        return False
    articles = select_headlines(await _fetch_window_articles(window, query))
    publishers = {_publisher(article) for article in articles}
    if len(articles) < MARKET_ANOMALY_MIN_ARTICLES or len(publishers) < MARKET_ANOMALY_MIN_SOURCES:
        logger.info(
            "[ANOMALY] %s %s 표본 부족: 기사=%d 출처=%d",
            window.market,
            window.price_session,
            len(articles),
            len(publishers),
        )
        if record_insufficient:
            await store.put(
                {
                    "market": window.market,
                    "price_session": window.price_session.isoformat(),
                    "sentiment_for_session": window.sentiment_for_session.isoformat(),
                    "window_start": window.start.isoformat(),
                    "window_end": window.end.isoformat(),
                    "window_hours": window.window_hours,
                    "article_count": len(articles),
                    "source_count": len(publishers),
                    "query_version": QUERY_VERSION,
                    "status": "insufficient",
                    "computed_at": now().isoformat(timespec="seconds"),
                }
            )
        return False
    payload = [
        {
            "title": str(article.title),
            "source": _publisher(article),
            "published_at": str(article.published_at),
        }
        for article in articles
    ]
    try:
        price_return = await asyncio.to_thread(index_return, ticker, window.price_session)
        async with semaphore:
            result = await asyncio.to_thread(
                analyzer.analyze,
                window.market,
                window.price_session.isoformat(),
                window.sentiment_for_session.isoformat(),
                window.start.isoformat(),
                window.end.isoformat(),
                payload,
            )
    except OvernightToneError:
        raise
    url_digest = hashlib.sha256(
        "\n".join(sorted(_canonical_url(article.url) for article in articles)).encode("utf-8")
    ).hexdigest()
    await store.put(
        {
            "market": window.market,
            "price_session": window.price_session.isoformat(),
            "sentiment_for_session": window.sentiment_for_session.isoformat(),
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "window_hours": window.window_hours,
            "price_return": price_return,
            "tone": result["tone"],
            "forward": result["forward"],
            "summary": result["summary"],
            "article_count": len(articles),
            "source_count": len(publishers),
            "model_id": result["model_id"],
            "prompt_sha256": result["prompt_sha256"],
            "query_version": QUERY_VERSION,
            "headline_urls_sha256": url_digest,
            # G6 재채점은 같은 입력을 다시 써야 한다. 제목·출처·시각만 남기고
            # 본문과 URL 원문은 저장하지 않는다.
            "headlines": payload,
            "computed_at": now().isoformat(timespec="seconds"),
        }
    )
    return True


@burst_job("시장 컨센서스 원자료 분석")
async def capture_completed_overnight_windows(app) -> None:
    """Scheduled catch-up: at most one newest missing window per market."""
    for market in sorted(MARKET_CHART_MARKETS):
        try:
            windows = completed_windows(market, now(), lookback_sessions=3)
            store = app.bot_data.get("overnight_tone_store")
            if store is None:
                return
            for window in reversed(windows):
                if not await store.contains(market, window.price_session):
                    await capture_window(app, window)
                    break
        except Exception:
            logger.exception("[ANOMALY] %s 세션 창 수집 실패", market)
