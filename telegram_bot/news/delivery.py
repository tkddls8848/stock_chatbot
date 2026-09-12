"""Deliver selected digest rows and persist their outcomes."""

from __future__ import annotations

import html
import logging

from telegram import Bot

from shared.core.config import (
    NEWS_DIGEST_MESSAGE_MAX_CHARS,
    NEWS_SOURCE_MARKETS,
)
from telegram_bot.news.models import PreparedGlobalArticle, PreparedSourceSection
from telegram_bot.news.utils import (
    chunk_message_items,
    publication_time_naive,
    signal_codes,
)
from telegram_bot.state import NewsLog, PredictionLog, SentNewsTracker

logger = logging.getLogger(__name__)
_DIGEST_ARTICLE_SEPARATOR = "\n\n"
_DIGEST_SOURCE_SEPARATOR = "\n\n"
_DIGEST_HEADER_RESERVE = 160

async def _confirm_and_log_global_article(
    prepared: PreparedGlobalArticle,
    tracker: SentNewsTracker,
    prediction_log: PredictionLog | None,
    news_log: NewsLog | None,
    prefilter=None,
) -> None:
    """처리를 마친 기사를 확정한 뒤 원문 기반 감성 결과를 로그에 기록한다.

    `prediction_log`(signal_scoring 소유)·`prefilter`(news_prefilter 소유)는
    둘 다 선택 의존이다 — `news`의 `FeatureSpec.requires`에는 없다. 그 기능이
    꺼져 있으면 `None`을 받아 조용히 건너뛴다(순환 의존 회피: signal_scoring이
    이미 news를 requires하므로 반대 방향을 선언할 수 없다).
    """
    spec = prepared.spec
    article = prepared.article
    translated = prepared.translated
    await tracker.confirm(article.article_id)

    codes = signal_codes(translated.mentioned_stocks)
    market = str(
        article.extra.get("market")
        or NEWS_SOURCE_MARKETS.get(spec.key.lower())
        or spec.market
        or "OTHER"
    )
    try:
        if prediction_log is not None and translated.sentiment is not None:
            await prediction_log.record(
                source=spec.key,
                title=translated.title,
                sentiment=translated.sentiment,
                impact=translated.impact,
                codes=codes,
                market=market,
            )
        if news_log is not None:
            await news_log.record(
                source=spec.key,
                title=translated.title,
                sentiment=translated.sentiment,
                impact=translated.impact,
                codes=codes,
                market=market,
                article_id=article.article_id,
                occurred_at=publication_time_naive(
                    article.published_at,
                    article.published_date or None,
                ),
            )
    except Exception as e:
        # 이미 확정한 뒤이므로 예약을 풀어 같은 기사를 다시 처리하지 않는다.
        logger.error("[%s] 확정 후 로그 기록 실패: %s", spec.key, e)
    if prefilter is not None and prepared.prefilter_candidate_id:
        try:
            await prefilter.record_outcome(
                candidate_id=prepared.prefilter_candidate_id,
                impact=translated.impact,
                sentiment=translated.sentiment,
            )
        except Exception as e:
            logger.warning("[%s] 사전선별 라벨 기록 실패: %s", spec.key, e)


async def archive_unsent_articles(
    rows: list[PreparedGlobalArticle],
    tracker: SentNewsTracker,
    prediction_log: PredictionLog | None,
    news_log: NewsLog | None,
    prefilter=None,
) -> None:
    """송출에서 탈락한 기사를 전송 시도 없이 확정하고 로그에만 남긴다.

    release하면 다음 주기에 같은 기사를 다시 집어 다시 번역한다. impact가
    계속 낮으면 영원히 재번역되며 Neurons만 태우므로 확정까지 마친다.
    """
    if not rows:
        return
    for row in rows:
        await _confirm_and_log_global_article(
            row,
            tracker,
            prediction_log,
            news_log,
            prefilter,
        )
    logger.info("[GLOBAL] 송출 제외 %d건은 로그에만 기록", len(rows))


async def send_global_digest(
    bot: Bot,
    chat_id: str,
    prepared_rows: list[PreparedGlobalArticle],
    tracker: SentNewsTracker,
    prediction_log: PredictionLog | None,
    news_log: NewsLog | None,
    prefilter=None,
) -> None:
    """여러 뉴스사의 기사를 안전 크기의 다이제스트 메시지로 묶어 보낸다."""
    if not prepared_rows:
        return

    grouped_rows: dict[str, list[PreparedGlobalArticle]] = {}
    for row in prepared_rows:
        grouped_rows.setdefault(row.spec.key, []).append(row)
    max_body_length = NEWS_DIGEST_MESSAGE_MAX_CHARS - _DIGEST_HEADER_RESERVE
    # chunk_message_items는 아이템 하나가 상한을 넘어도 쪼개지 못한다. 소스 하나의
    # 기사 묶음이 한 메시지에 안 들어가면 텔레그램 4096자 제한에 걸려 전송이
    # 통째로 실패하므로, 섹션을 만들 때 소스 안에서 먼저 나눠 둔다.
    # (기사당 URL 500자 + 본문 상한이 겹치면 소스당 3~4건에서도 넘길 수 있다.)
    sections: list[PreparedSourceSection] = []
    for rows in grouped_rows.values():
        label = f"<b>[{html.escape(rows[0].spec.label)}]</b>\n"
        for row_group in chunk_message_items(
            rows,
            text_getter=lambda row: row.text,
            max_body_length=max(1, max_body_length - len(label)),
            separator=_DIGEST_ARTICLE_SEPARATOR,
        ):
            sections.append(
                PreparedSourceSection(
                    rows=row_group,
                    text=label
                    + _DIGEST_ARTICLE_SEPARATOR.join(row.text for row in row_group),
                )
            )
    chunks = chunk_message_items(
        sections,
        text_getter=lambda section: section.text,
        max_body_length=max_body_length,
        separator=_DIGEST_SOURCE_SEPARATOR,
    )
    # 섹션은 분할될 수 있으므로 소스 수는 그룹 수로 센다.
    source_count = len(grouped_rows)
    article_count = len(prepared_rows)

    for chunk_index, chunk in enumerate(chunks, start=1):
        header = (
            "<b>뉴스 다이제스트</b>\n"
            f"소스 {source_count}곳 · 새 기사 {article_count}건"
            f" · {chunk_index}/{len(chunks)}\n\n"
        )
        text = header + _DIGEST_SOURCE_SEPARATOR.join(
            section.text for section in chunk
        )
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            for section in chunk:
                for row in section.rows:
                    await tracker.release(row.article.article_id)
            logger.error(
                "[GLOBAL] 다이제스트 %d/%d 전송 실패: %s",
                chunk_index,
                len(chunks),
                e,
            )
            continue

        for section in chunk:
            for row in section.rows:
                await _confirm_and_log_global_article(
                    row,
                    tracker,
                    prediction_log,
                    news_log,
                    prefilter,
                )
                logger.info(
                    "[%s] 다이제스트 전송 완료: %s",
                    row.spec.key,
                    row.translated.title[:30],
                )



