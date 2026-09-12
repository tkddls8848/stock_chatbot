"""스케줄러가 주기적으로 호출하는 뉴스 수집·번역·묶음 전송 파이프라인.

전역 속보는 NewsSourceRegistry의 활성 소스를 함께 조회하고 소스별 기사를
번역한 뒤, 텔레그램 메시지 크기에 맞춘 다이제스트로 묶어 전송한다.

번역 건수(NEWS_GLOBAL_LIMIT)와 송출 건수(NEWS_DIGEST_SEND_LIMIT)는 다르다.
번역한 기사 중 impact가 높은 순으로 골라 보내고, 탈락한 기사도 로그에는
그대로 남긴다(`archive_unsent_articles`).
"""

import asyncio
import logging

from telegram.ext import Application

from shared.core.clock import now
from shared.core.config import NEWS_DIGEST_SEND_LIMIT, TELEGRAM_CHAT_ID
from shared.core.workers import urgent_phase
from shared.llm.translator import TranslationService
from telegram_bot.news.delivery import archive_unsent_articles, send_global_digest
from telegram_bot.news.models import PreparedGlobalArticle
from telegram_bot.news.preparation import prepare_global_source
from telegram_bot.news.registry import NewsSourceRegistry
from telegram_bot.news.selection import select_digest_rows
from telegram_bot.state import NewsLog, PredictionLog, SentNewsTracker
from telegram_bot.watchlist import WatchlistManager

logger = logging.getLogger(__name__)

async def fetch_all(app: Application) -> None:
    """뉴스 수집·번역 주기(긴급 경로).

    이 구간이 도는 동안 리서치·브리핑 등 비긴급 LLM 작업은 시작을 보류해,
    번역이 그 뒤에서 대기하지 않도록 한다.
    """
    async with urgent_phase():
        await _fetch_all(app)


async def _fetch_all(app: Application) -> None:
    tracker: SentNewsTracker = app.bot_data["sent_tracker"]
    wm: WatchlistManager     = app.bot_data["watchlist_manager"]
    translator: TranslationService = app.bot_data["translator"]
    translate_semaphore: asyncio.Semaphore = app.bot_data["translate_semaphore"]
    registry: NewsSourceRegistry = app.bot_data["news_registry"]
    prediction_log: PredictionLog | None = app.bot_data.get("prediction_log")
    news_log: NewsLog | None = app.bot_data.get("news_log")
    # news_prefilter 소유. `news`의 requires에 없는 선택 의존이다 — 꺼져 있으면
    # None이고, 아래 호출부는 최신순 정렬로 그대로 fail-soft 동작한다.
    prefilter = app.bot_data.get("news_prefilter")
    watchlist = await wm.get_all()
    cycle_id = now().isoformat(timespec="seconds")

    selected_specs = registry.active_specs()
    if not selected_specs:
        logger.warning("[GLOBAL] 사용 가능한 전역 뉴스 소스가 없습니다(전부 쿨다운).")
    logger.info(
        "[GLOBAL] 이번 주기 처리 소스 %d개: %s",
        len(selected_specs),
        ", ".join(spec.key for spec in selected_specs),
    )
    prepared_groups = await asyncio.gather(
        *(
            prepare_global_source(
                spec,
                registry,
                tracker,
                translator,
                translate_semaphore,
                watchlist,
                prefilter,
                cycle_id,
            )
            for spec in selected_specs
        )
    )
    # 번역은 소스당 NEWS_GLOBAL_LIMIT건까지 하되, 실제로 보내는 건 impact
    # 상위 NEWS_DIGEST_SEND_LIMIT건이다. 선별은 소스 안에서만 겨룬다.
    prepared_rows: list[PreparedGlobalArticle] = []
    unsent_rows: list[PreparedGlobalArticle] = []
    for group in prepared_groups:
        selected, dropped = select_digest_rows(group, NEWS_DIGEST_SEND_LIMIT)
        prepared_rows.extend(selected)
        unsent_rows.extend(dropped)

    # 전송 실패 시 release되는 건 송출 대상뿐이다. 탈락분은 전송 경로를 아예
    # 타지 않으므로 다이제스트가 실패해도 영향받지 않는다.
    await send_global_digest(
        app.bot,
        TELEGRAM_CHAT_ID,
        prepared_rows,
        tracker,
        prediction_log,
        news_log,
        prefilter,
    )
    await archive_unsent_articles(
        unsent_rows,
        tracker,
        prediction_log,
        news_log,
        prefilter,
    )

    await tracker.persist()
