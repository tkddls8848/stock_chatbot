"""Translate source candidates into digest-ready articles."""

from __future__ import annotations

import asyncio
import logging

from shared.core.config import (
    NEWS_GLOBAL_LIMIT,
    NEWS_NEGATIVE_ALERT_THRESHOLD,
    NEWS_TRANSLATION_QUALITY_REJECT_LIMIT,
)
from shared.llm.translator import TranslationQualityError, TranslationService
from telegram_bot.news.collection import collect_source_candidates
from telegram_bot.news.models import PreparedGlobalArticle, SourceCandidate
from telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from telegram_bot.news.utils import (
    display_time,
    compact_sentiment_line,
    format_china_time_as_jst,
    format_digest_article,
    is_timeout_error,
    normalize_stock_code,
    translate_article,
)
from telegram_bot.state import SentNewsTracker

logger = logging.getLogger(__name__)

def _watchlist_hits(codes: list[str], watchlist: dict[str, str]) -> list[str]:
    return [
        normalized
        for code in codes
        if (normalized := normalize_stock_code(code)) in watchlist
    ]


def _negative_alert_prefix(sentiment: float | None, related_to_watchlist: bool) -> str:
    if (
        related_to_watchlist
        and sentiment is not None
        and sentiment <= NEWS_NEGATIVE_ALERT_THRESHOLD
    ):
        return "⚠️ <b>관심종목 부정 뉴스</b>\n"
    return ""


async def prepare_global_source(
    spec: SourceSpec,
    registry: NewsSourceRegistry,
    tracker: SentNewsTracker,
    translator: TranslationService,
    translate_semaphore: asyncio.Semaphore,
    watchlist: dict[str, str],
    prefilter=None,
    cycle_id: str = "",
) -> list[PreparedGlobalArticle]:
    candidates = await collect_source_candidates(
        spec,
        registry,
        watchlist,
        prefilter,
        cycle_id,
    )
    metrics = {
        "duplicate": 0,
        "translate_failed": 0,
        "quality_rejected": 0,
        "prepared": 0,
    }

    async def prepare_article(candidate: SourceCandidate):
        """예약이 끝난 후보 하나를 번역해 표시 문자열까지 만든다."""
        article = candidate.article
        article_id = article.article_id
        try:
            translated = await translate_article(
                translator,
                translate_semaphore,
                article.title,
                article.content,
            )
            safe_url = article.url if len(article.url) <= 500 else ""
            hits = _watchlist_hits(translated.mentioned_stocks, watchlist)
            sentiment_line = compact_sentiment_line(
                translated.sentiment,
                translated.impact,
            )
            alert = (
                "⚠️ "
                if _negative_alert_prefix(translated.sentiment, bool(hits))
                else ""
            )
            formatted_time = format_china_time_as_jst(
                article.published_at,
                article.published_date or None,
            )
            text = format_digest_article(
                translated.title,
                translated.content,
                display_time(formatted_time),
                sentiment_line,
                alert,
                safe_url,
            )
            metrics["prepared"] += 1
            return PreparedGlobalArticle(
                spec,
                article,
                text,
                translated,
                candidate.prefilter_candidate_id,
            )
        except TranslationQualityError as e:
            # 형식 오류와 달리 같은 원문에는 대체로 같은 응답이 다시 온다.
            # release하면 매 주기 같은 기사에 Neurons를 태우므로 확정해 둔다.
            await tracker.confirm(article_id)
            metrics["quality_rejected"] += 1
            logger.warning("[%s] 번역 품질 미달로 제외: %s", spec.key, e)
            return None
        except Exception as e:
            await tracker.release(article_id)
            metrics["translate_failed"] += 1
            logger.error("[%s] 번역 실패: %s", spec.key, e)
            if is_timeout_error(e):
                raise
            return None

    # 앞부분의 중복·번역 실패 때문에 소스별 목표 건수를 놓치지 않도록 충분한
    # 범위를 훑는다. 최신 기사부터 준비하고 출력 직전에 과거→최신으로 뒤집는다.
    scan_limit = max(NEWS_GLOBAL_LIMIT * 20, NEWS_GLOBAL_LIMIT)
    scanned = 0
    prepared_rows: list[PreparedGlobalArticle] = []
    for candidate in candidates[:scan_limit]:
        scanned += 1
        if not await tracker.reserve(candidate.article.article_id):
            metrics["duplicate"] += 1
            continue
        try:
            prepared = await prepare_article(candidate)
        except Exception:
            logger.error("[%s] 타임아웃으로 이번 주기 남은 번역을 중단합니다.", spec.key)
            break
        if prepared is not None:
            prepared_rows.append(prepared)
            if len(prepared_rows) >= NEWS_GLOBAL_LIMIT:
                break
        elif metrics["quality_rejected"] >= NEWS_TRANSLATION_QUALITY_REJECT_LIMIT:
            logger.warning(
                "[%s] 품질 미달 %d건으로 이번 주기 남은 번역을 중단합니다.",
                spec.key,
                metrics["quality_rejected"],
            )
            break

    logger.info(
        "[%s] 기사 준비: 후보 %d / 확인 %d / 중복 %d / 번역 준비 %d /"
        " 번역 실패 %d / 품질 미달 %d",
        spec.key,
        len(candidates),
        scanned,
        metrics["duplicate"],
        metrics["prepared"],
        metrics["translate_failed"],
        metrics["quality_rejected"],
    )
    return prepared_rows[::-1]

