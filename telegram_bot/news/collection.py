"""Fetch and prefilter original articles for the market report."""

from __future__ import annotations

import asyncio
import logging

import requests

from telegram_bot.core.config import NEWS_LIVE_MAX_AGE_HOURS, NEWS_SOURCE_FETCH_TIMEOUT_SECONDS
from telegram_bot.news.models import SourceCandidate
from telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from telegram_bot.news.sources import GlobalArticle
from telegram_bot.news.utils import filter_recent_articles

logger = logging.getLogger(__name__)

async def _fetch_source(func, *args):
    return await asyncio.wait_for(
        asyncio.to_thread(func, *args),
        timeout=NEWS_SOURCE_FETCH_TIMEOUT_SECONDS,
    )

async def collect_source_candidates(
    spec: SourceSpec,
    registry: NewsSourceRegistry,
    watchlist: dict[str, str],
    prefilter=None,
    cycle_id: str = "",
    *,
    excluded_article_ids: set[str] | None = None,
    excluded_event_ids: set[str] | None = None,
) -> list[SourceCandidate]:
    """소스 실패·신선도·기존 예약을 걸러 보고서 후보를 선별한다."""
    try:
        articles: list[GlobalArticle] = await _fetch_source(spec.fetch)
        registry.record_success(spec.key)
    except TimeoutError:
        registry.record_failure(spec.key, "timeout")
        logger.error(
            "[%s] API 호출 시간 초과: %.1f초", spec.key, NEWS_SOURCE_FETCH_TIMEOUT_SECONDS
        )
        return []
    except Exception as e:
        is_rss_blocked = (
            spec.key.startswith("rss:")
            and isinstance(e, requests.HTTPError)
            and e.response is not None
            and e.response.status_code in (403, 429)
        )
        if spec.key.startswith("rss:") and (isinstance(e, requests.Timeout) or is_rss_blocked):
            registry.record_unavailable(spec.key, str(e))
        elif isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 429:
            registry.record_rate_limited(spec.key, str(e))
        else:
            registry.record_failure(spec.key, str(e))
        logger.error("[%s] API 호출 실패: %s", spec.key, e)
        return []

    fetched_count = len(articles)
    articles = filter_recent_articles(articles, NEWS_LIVE_MAX_AGE_HOURS)
    articles = [article for article in articles if article.article_id not in (excluded_article_ids or set())]
    if not articles:
        logger.info(
            "[%s] 신선도·기존 예약 제외 후 기사 0건 (수집 %d건, 최근 %d시간)",
            spec.key,
            fetched_count,
            NEWS_LIVE_MAX_AGE_HOURS,
        )
        return []

    prefilter_contexts = {}
    if prefilter is not None:
        try:
            ranked_candidates = await prefilter.rank_articles(
                source=spec.key,
                market=spec.market,
                articles=articles,
                watchlist=watchlist,
                cycle_id=cycle_id,
                excluded_event_ids=excluded_event_ids,
            )
            articles = [candidate.article for candidate in ranked_candidates]
            prefilter_contexts = {
                candidate.article.article_id: candidate
                for candidate in ranked_candidates
            }
        except Exception as e:
            # 로컬 보조 기능이 뉴스 수집·보고를 막아서는 안 된다. shadow/active와
            # 무관하게 실패한 주기만 기존 최신순으로 처리한다.
            logger.error("[%s] 뉴스 사전선별 실패, 최신순으로 계속: %s", spec.key, e)

    candidates = []
    for article in articles:
        context = prefilter_contexts.get(article.article_id)
        candidates.append(
            SourceCandidate(
                spec=spec,
                article=article,
                prefilter_candidate_id=getattr(context, "candidate_id", ""),
                event_id=getattr(context, "event_id", ""),
                prefilter_exploration=bool(getattr(context, "exploration", False)) and prefilter.mode == "active",
            )
        )
    logger.info(
        "[%s] 후보 준비: 수집 %d / 발행시각 통과 %d",
        spec.key,
        fetched_count,
        len(candidates),
    )
    return candidates





