"""매시간 원문 수집과 3시간 시장상황 보고서 생성.

기사별 번역 대신 원문 제목을 큐에 모으고, UTC +9 기준 3시간마다 시장별 공통
테마·상충 신호·다음 관찰 포인트를 추론한다. LLM 호출 수는 기사 수가 아니라
보고서에 포함된 시장 수에 비례한다.
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta

from telegram import Bot
from telegram.ext import Application

from telegram_bot.core.clock import JST, now
from telegram_bot.core.config import (
    NEWS_DIGEST_MESSAGE_MAX_CHARS,
    NEWS_REPORT_INTERVAL_HOURS,
    NEWS_REPORT_MAX_HEADLINES,
    NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
    NEWS_SOURCE_MARKETS,
    TELEGRAM_CHAT_ID,
)
from telegram_bot.core.workers import burst_job, run_non_urgent
from telegram_bot.llm.news_report import NewsReportAnalyzer, NewsReportError
from telegram_bot.news.collection import collect_source_candidates
from telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from telegram_bot.news.utils import (
    chunk_message_items,
    display_time,
    compact_sentiment_line,
    format_china_time_as_jst,
    format_digest_article,
    parse_news_datetime,
    publication_time_naive,
    signal_codes,
)
from telegram_bot.state import NewsLog, NewsReportQueue, SentNewsTracker
from telegram_bot.watchlist import WatchlistManager

logger = logging.getLogger(__name__)

_MARKET_LABELS = {
    "CN": "중국 본토",
    "HK": "홍콩",
    "US": "미국",
    "KR": "한국",
    "JP": "일본",
    "EU": "유럽",
    "OTHER": "기타",
}
# 시장 표시 순서. 목록 밖 시장은 뒤에 붙인다.
_MARKET_ORDER = ("CN", "HK", "US", "KR")
_DIGEST_HEADER_RESERVE = 200
# 요약이 실패한 시장에 원문 제목만 남길 때의 건수.
_FALLBACK_HEADLINE_LIMIT = 10
_EPOCH = datetime(1970, 1, 1, tzinfo=JST)
# 수동 실행과 예약 실행이 겹쳐 같은 보고서가 두 번 전송되는 것을 막는다.
_REPORT_LOCK = asyncio.Lock()


def _market_of(spec: SourceSpec, article) -> str:
    return str(
        article.extra.get("market")
        or NEWS_SOURCE_MARKETS.get(spec.key.lower())
        or spec.market
        or "OTHER"
    )


def _queue_item(candidate) -> dict:
    article = candidate.article
    return {
        "article_id": article.article_id,
        "event_id": candidate.event_id,
        "source": candidate.spec.key,
        "label": candidate.spec.label,
        "market": _market_of(candidate.spec, article),
        "title": article.title[:240],
        "url": article.url if len(article.url) <= 500 else "",
        "published_at": article.published_at,
        "published_date": article.published_date or "",
        "prefilter_candidate_id": candidate.prefilter_candidate_id,
    }


async def collect_report_source(
    spec: SourceSpec,
    registry: NewsSourceRegistry,
    tracker: SentNewsTracker,
    queue: NewsReportQueue,
    watchlist: dict[str, str],
    prefilter=None,
    cycle_id: str = "",
) -> int:
    """소스 하나의 원문 기사를 예약하고 보고서 큐에 담는다."""
    candidates = await collect_source_candidates(
        spec,
        registry,
        watchlist,
        prefilter,
        cycle_id,
    )
    reserved = []
    for candidate in candidates[:NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT]:
        if await tracker.reserve(candidate.article.article_id):
            reserved.append(candidate)
    if not reserved:
        return 0
    try:
        accepted = await queue.enqueue([_queue_item(row) for row in reserved])
    except Exception as e:
        for row in reserved:
            await tracker.release(row.article.article_id)
        logger.error("[%s] 보고서 큐 저장 실패, 예약을 해제합니다: %s", spec.key, e)
        return 0

    accepted_ids = {item["article_id"] for item in accepted}
    for row in reserved:
        # 큐가 받지 않은 것(사건 중복 등)은 다음 주기에 다시 볼 수 있게 둔다.
        if row.article.article_id not in accepted_ids:
            await tracker.release(row.article.article_id)
    return len(accepted)


async def collect_report_articles(app: Application) -> None:
    """매시간 소스를 읽어 큐에만 담고 LLM을 부르지 않는다."""
    tracker: SentNewsTracker = app.bot_data["sent_tracker"]
    queue: NewsReportQueue = app.bot_data["news_report_queue"]
    registry: NewsSourceRegistry = app.bot_data["news_registry"]
    wm: WatchlistManager = app.bot_data["watchlist_manager"]
    prefilter = app.bot_data.get("news_prefilter")
    watchlist = await wm.get_all()
    cycle_id = now().isoformat(timespec="seconds")

    specs = registry.active_specs()
    if not specs:
        logger.warning("[NEWS REPORT] 사용 가능한 전역 뉴스 소스가 없습니다(전부 쿨다운).")
        return
    counts = await asyncio.gather(
        *(
            collect_report_source(
                spec,
                registry,
                tracker,
                queue,
                watchlist,
                prefilter,
                cycle_id,
            )
            for spec in specs
        )
    )
    await tracker.persist()
    logger.info(
        "[NEWS REPORT] 원문 수집 %d건 (소스 %d곳, 기사별 번역 없음)",
        sum(counts),
        len(specs),
    )


def _sorted_by_recency(items: list[dict]) -> list[dict]:
    """발행시각 최신순. 시각을 못 읽은 기사는 뒤로 민다."""
    def key(item: dict):
        parsed = parse_news_datetime(item.get("published_at"), item.get("published_date"))
        return (parsed is not None, parsed or _EPOCH)

    return sorted(items, key=key, reverse=True)


def group_by_market(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """시장별로 묶고 표시 순서대로 돌려준다. 시장 안은 최신순이다."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(str(item.get("market") or "OTHER"), []).append(item)
    ordered_keys = [key for key in _MARKET_ORDER if key in grouped]
    ordered_keys += sorted(key for key in grouped if key not in _MARKET_ORDER)
    return [(key, _sorted_by_recency(grouped[key])) for key in ordered_keys]


def _window_label(opened_at: str, closed_at: datetime) -> str:
    try:
        opened = datetime.fromisoformat(opened_at)
    except (TypeError, ValueError):
        opened = None
    opened = opened or closed_at - timedelta(hours=NEWS_REPORT_INTERVAL_HOURS)
    return f"{opened.strftime('%H:%M')}~{closed_at.strftime('%H:%M')} UTC +9"


def _report_time_label(value: str) -> str:
    """기존 UTC +9 변환기를 사용하되 사용자 표기에서 지역 약어를 제거한다."""
    return display_time(value)


def _headline_payload(items: list[dict]) -> list[dict]:
    payload = []
    for index, item in enumerate(items):
        formatted = format_china_time_as_jst(
            item.get("published_at"),
            item.get("published_date") or None,
        )
        payload.append(
            {
                "index": index,
                "title": str(item.get("title") or ""),
                "source": str(item.get("label") or item.get("source") or ""),
                "published_at": _report_time_label(formatted),
            }
        )
    return payload


def _highlight_text(item: dict, highlight: dict) -> str:
    formatted = format_china_time_as_jst(
        item.get("published_at"),
        item.get("published_date") or None,
    )
    return format_digest_article(
        highlight["title"],
        "",
        _report_time_label(formatted),
        compact_sentiment_line(highlight["sentiment"], highlight["impact"]),
        "",
        str(item.get("url") or ""),
    )


def format_market_section(
    market: str,
    items: list[dict],
    result: dict | None,
) -> str:
    """시장 하나의 상황 보고서 섹션. result가 없으면 제목만 나열한다."""
    label = _MARKET_LABELS.get(market, market)
    lines = [f"<b>[{html.escape(label)}]</b> 수집 {len(items)}건"]
    if result is None:
        # LLM이 실패한 시장이다. 그 시간의 뉴스를 통째로 잃지 않도록 원문
        # 제목만이라도 남긴다.
        lines.append("<i>요약 생성 실패 — 원문 제목만 표시합니다.</i>")
        for item in items[:_FALLBACK_HEADLINE_LIMIT]:
            formatted = format_china_time_as_jst(
                item.get("published_at"),
                item.get("published_date") or None,
            )
            lines.append(
                format_digest_article(
                    str(item.get("title") or ""),
                    "",
                    _report_time_label(formatted),
                    "",
                    "",
                    str(item.get("url") or ""),
                )
            )
        return "\n\n".join(lines)

    if result["analysis"]:
        lines.append(html.escape(result["analysis"]))
    for highlight in result["highlights"]:
        lines.append(_highlight_text(items[highlight["index"]], highlight))
    return "\n\n".join(lines)


async def _analyze_market(
    analyzer: NewsReportAnalyzer,
    market: str,
    window: str,
    items: list[dict],
) -> dict | None:
    headlines = _headline_payload(items[:NEWS_REPORT_MAX_HEADLINES])
    try:
        return await run_non_urgent(analyzer.analyze, market, window, headlines)
    except NewsReportError as e:
        logger.error("[NEWS REPORT] %s 시장상황 분석 실패: %s", market, e)
        return None


async def _log_highlights(
    market: str,
    items: list[dict],
    result: dict,
    news_log: NewsLog | None,
    prefilter=None,
) -> None:
    """보고서 주요 기사를 로그에 남기고 사전선별에 라벨을 되먹인다.

    남기지 않으면 브리핑·/market이 보고서 근거를 보지 못한다.

    **사전선별은 이 호출이 유일한 라벨 공급원이다.** 사전선별은 제목만 보고
    추측하므로 자기가 맞았는지 스스로 알 수 없고, 기사를 실제로 읽고 중요도를
    판정하는 것은 이 보고서 LLM뿐이다. 보고서가 이미 만들어 둔 `impact`를
    넘기는 것이라 추가 호출이 없다.

    끊긴 적이 있다. 예전 공급원은 기사별 번역 경로였는데 3시간 보고서로
    바꾸면서 그 경로가 죽었고, 오류가 나지 않아 13일 동안 라벨 0건인 채로
    돌았다(2026-08-30 ~ 09-12). 이 자리를 옮기거나 지울 때 사전선별의 학습이
    함께 멈춘다는 것을 기억한다.
    """
    for highlight in result["highlights"]:
        item = items[highlight["index"]]
        codes = signal_codes(highlight["mentioned_stocks"])
        try:
            if news_log is not None:
                await news_log.record(
                    source=str(item.get("source") or ""),
                    title=highlight["title"],
                    sentiment=highlight["sentiment"],
                    impact=highlight["impact"],
                    codes=codes,
                    market=market,
                    article_id=str(item.get("article_id") or ""),
                    occurred_at=publication_time_naive(
                        item.get("published_at"),
                        item.get("published_date") or None,
                    ),
                )
            candidate_id = str(item.get("prefilter_candidate_id") or "")
            if prefilter is not None and candidate_id:
                await prefilter.record_outcome(
                    candidate_id=candidate_id,
                    impact=highlight["impact"],
                    sentiment=highlight["sentiment"],
                )
        except Exception as e:
            logger.error("[NEWS REPORT] %s 근거 로그 기록 실패: %s", market, e)

    # 미선정 표본은 학습에만 쓴다. 사용자 뉴스·예측 로그나 사건 재탕 차단에 넣지 않는다.
    if prefilter is not None:
        for evaluation in result.get("evaluations", []):
            item = items[evaluation["index"]]
            candidate_id = str(item.get("prefilter_candidate_id") or "")
            if candidate_id:
                try:
                    await prefilter.record_outcome(
                        candidate_id=candidate_id, impact=evaluation["impact"],
                        sentiment=None, selected=False,
                    )
                except Exception as exc:
                    logger.error("[NEWS REPORT] %s 학습 평가 저장 실패: %s", market, exc)


async def _send_sections(
    bot: Bot,
    chat_id: str,
    header: str,
    sections: list[str],
) -> tuple[int, int]:
    max_body_length = NEWS_DIGEST_MESSAGE_MAX_CHARS - _DIGEST_HEADER_RESERVE
    chunks = chunk_message_items(
        sections,
        text_getter=lambda section: section,
        max_body_length=max_body_length,
        separator="\n\n",
    )
    sent = 0
    failed = 0
    for index, chunk in enumerate(chunks, start=1):
        text = f"{header} · {index}/{len(chunks)}\n\n" + "\n\n".join(chunk)
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            logger.error("[NEWS REPORT] 보고서 %d/%d 전송 실패: %s", index, len(chunks), e)
    return sent, failed


async def send_news_report(app: Application) -> None:
    """큐의 기사를 시장별로 분석해 3시간 보고서를 보내고 큐를 비운다."""
    async with _REPORT_LOCK:
        await _send_news_report(app)


@burst_job("3시간 시장상황 보고서")
async def _send_news_report(app: Application) -> None:
    queue: NewsReportQueue | None = app.bot_data.get("news_report_queue")
    analyzer: NewsReportAnalyzer | None = app.bot_data.get("news_report_analyzer")
    if queue is None or analyzer is None:
        return
    opened_at, items = await queue.snapshot()
    if not items:
        return

    tracker: SentNewsTracker = app.bot_data["sent_tracker"]
    news_log: NewsLog | None = app.bot_data.get("news_log")
    prefilter = app.bot_data.get("news_prefilter")
    window = _window_label(opened_at, now())

    sections: list[str] = []
    for market, market_items in group_by_market(items):
        result = await _analyze_market(analyzer, market, window, market_items)
        sections.append(format_market_section(market, market_items, result))
        if result is not None:
            await _log_highlights(
                market, market_items, result, news_log, prefilter
            )

    header = (
        f"🧭 <b>3시간 시장상황 보고서</b>\n{html.escape(window)} · 수집 {len(items)}건"
    )
    sent, failed = await _send_sections(app.bot, TELEGRAM_CHAT_ID, header, sections)
    if not sent:
        # 한 조각도 못 보냈다. 큐와 예약을 그대로 두고 다음 주기가 다시 시도한다.
        logger.error("[NEWS REPORT] 보고서를 보내지 못해 큐를 유지합니다(%d건).", len(items))
        return
    if failed:
        # 일부만 나갔다. 큐를 남기면 성공한 조각을 다음 실행에 다시 보내게 되므로
        # 비우고, 빠진 조각은 로그로만 남긴다.
        logger.error("[NEWS REPORT] 보고서 %d조각이 빠진 채 확정합니다.", failed)

    for item in items:
        await tracker.confirm(str(item.get("article_id") or ""))
    await tracker.persist()
    await queue.clear()
    logger.info("[NEWS REPORT] 보고서 전송 완료 · 기사 %d건 확정", len(items))


async def run_news_report_job(app: Application) -> None:
    """예약 실행 경계. 받을 사람이 없으므로 실패를 여기서 삼킨다."""
    try:
        await send_news_report(app)
    except Exception:
        logger.error("[NEWS REPORT] 예약 실행 실패", exc_info=True)
