"""뉴스 수집과 3시간 시장상황 보고서 기능 선언."""

from shared.core.clock import JST, now

from shared.core.config import (
    NEWS_COLLECTION_INTERVAL_MINUTES,
    NEWS_GLOBAL_SOURCE_KEYS,
    NEWS_LOG_FILE,
    NEWS_LOG_RETENTION_DAYS,
    NEWS_REPORT_INTERVAL_HOURS,
    NEWS_REPORT_QUEUE_FILE,
    NEWS_REPORT_QUEUE_MAX_ITEMS,
    NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
    NEWS_RSS_FEEDS,
    NEWS_SOURCE_COOLDOWN_MINUTES,
    NEWS_SOURCE_FAILURE_THRESHOLD,
    NEWS_SOURCE_MARKETS,
    SENT_IDS_FILE,
    SENT_NEWS_RETENTION_DAYS,
)
from telegram_bot.features.base import FeatureSpec
from shared.llm import build_news_report_analyzer
from telegram_bot.news import NewsSourceRegistry, build_source_specs
from telegram_bot.news.report import (
    collect_report_articles,
    run_news_report_job,
)
from telegram_bot.state import NewsLog, NewsReportQueue, SentNewsTracker


def _install_services(app) -> None:
    app.bot_data["sent_tracker"] = SentNewsTracker(
        SENT_IDS_FILE,
        SENT_NEWS_RETENTION_DAYS,
    )
    app.bot_data["news_registry"] = NewsSourceRegistry(
        build_source_specs(
            NEWS_GLOBAL_SOURCE_KEYS,
            NEWS_RSS_FEEDS,
            NEWS_SOURCE_MARKETS,
        ),
        failure_threshold=NEWS_SOURCE_FAILURE_THRESHOLD,
        cooldown_minutes=NEWS_SOURCE_COOLDOWN_MINUTES,
    )
    app.bot_data["news_log"] = NewsLog(
        NEWS_LOG_FILE,
        NEWS_LOG_RETENTION_DAYS,
    )
    app.bot_data["news_report_queue"] = NewsReportQueue(
        NEWS_REPORT_QUEUE_FILE,
        per_source_limit=NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
        max_items=NEWS_REPORT_QUEUE_MAX_ITEMS,
    )
    app.bot_data["news_report_analyzer"] = build_news_report_analyzer()


async def run_news_collection(app) -> None:
    """원문 기사만 수집해 다음 3시간 보고서 큐에 담는다."""
    await collect_report_articles(app)


def _install_jobs(scheduler, app) -> None:
    scheduler.add_job(
        run_news_collection,
        trigger="interval",
        minutes=NEWS_COLLECTION_INTERVAL_MINUTES,
        args=[app],
        next_run_time=now(),
        id="news_collection",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_news_report_job,
        trigger="cron",
        hour=f"*/{NEWS_REPORT_INTERVAL_HOURS}",
        minute=0,
        timezone=JST,
        args=[app],
        id="market_situation_report",
        max_instances=1,
        coalesce=True,
    )


FEATURE = FeatureSpec(
    key="news",
    label="뉴스 수집·시장상황 보고서",
    requires=frozenset({"watchlist"}),
    install_services=_install_services,
    install_jobs=_install_jobs,
    data_files=(
        "data/news/sent_ids.json",
        "data/news/news_log.json",
        "data/news/news_report_queue.json",
    ),
)
