"""매시간 원문 수집과 시장상황 보고서 생성.

기사별 번역 대신 원문 제목을 큐에 모으고, UTC +9 기준 3시간마다 시장별로
공통 테마·상충 신호·다음 관찰 포인트를 추론한다.

**3시간은 검토 주기이고 발행 주기가 아니다.** 시장마다 두 단계로 발행을
판정한다. ① 마지막 발행 뒤 모은 기사가 `NEWS_REPORT_MIN_ARTICLES`에 못 미치면
LLM을 부르지 않고 보류한다. ② 모델이 직전 발행분 대비 새로 확인된 사실도
방향 전환도 없다고 판정하면 보류한다. 보류한 시장의 기사는 큐에 남아 다음
구간에 더 두꺼운 재료로 다시 평가되고, `NEWS_REPORT_MAX_HELD_HOURS`를 넘기면
판정과 무관하게 발행한다.

재료가 얇은 구간에 한 편을 억지로 쓰게 하면 같은 국면을 다른 문장으로
반복하게 되고, 그 반복이 보고서를 기계적으로 만든다. 침묵도 출력이다.

LLM 호출 수는 기사 수가 아니라 ①을 통과한 시장 수에 비례한다.
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta

from telegram import Bot
from telegram.ext import Application

from services.telegram_bot.core.clock import JST, ensure_jst, now
from services.telegram_bot.core.config import (
    NEWS_DIGEST_MESSAGE_MAX_CHARS,
    NEWS_REPORT_INTERVAL_HOURS,
    NEWS_REPORT_MAX_HEADLINES,
    NEWS_REPORT_MAX_HELD_HOURS,
    NEWS_REPORT_MIN_ARTICLES,
    NEWS_REPORT_QUEUE_PER_SOURCE_LIMIT,
    NEWS_REPORT_SHOWN_HIGHLIGHTS,
    NEWS_SOURCE_MARKETS,
    TELEGRAM_CHAT_ID,
)
from services.telegram_bot.core.workers import burst_job, run_non_urgent
from services.telegram_bot.llm.news_report import NewsReportAnalyzer, NewsReportError
from services.telegram_bot.news.collection import collect_source_candidates
from services.telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from services.telegram_bot.news.utils import (
    chunk_message_items,
    display_time,
    compact_sentiment_line,
    format_china_time_as_jst,
    format_digest_article,
    parse_news_datetime,
    publication_time_naive,
    signal_codes,
)
from services.telegram_bot.state import (
    NewsLog,
    NewsReportMemory,
    NewsReportQueue,
    SentNewsTracker,
)
from services.telegram_bot.watchlist import WatchlistManager

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
# 전용 소스가 있는 시장을 의도한 순서로 둔다. 여기 없는 시장(gnews 의 EU·RU·TW)은
# 아래에서 정렬돼 뒤에 붙는다 — 순서가 우연이 되지 않게 JP 를 명시한다.
_MARKET_ORDER = ("CN", "HK", "US", "KR", "JP")
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
        "prefilter_exploration": candidate.prefilter_exploration,
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
    _, queued = await queue.snapshot()
    candidates = await collect_source_candidates(
        spec,
        registry,
        watchlist,
        prefilter,
        cycle_id,
        excluded_article_ids=await tracker.unavailable_ids(),
        excluded_event_ids={item["event_id"] for item in queued if item.get("event_id")},
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


def _market_window(
    memory: NewsReportMemory | None,
    market: str,
    opened_at: str,
    closed_at: datetime,
) -> str:
    """이 시장이 **마지막으로 발행된 뒤** 쌓인 구간.

    시장마다 발행 시점이 다르므로 구간도 시장마다 다르다. 발행 이력이 없으면
    큐가 열린 시각을, 그것도 없으면 직전 검토 시각을 시작으로 본다.
    """
    opened = memory.last_published_at(market) if memory is not None else None
    if opened is None:
        try:
            opened = ensure_jst(datetime.fromisoformat(opened_at))
        except (TypeError, ValueError):
            opened = closed_at - timedelta(hours=NEWS_REPORT_INTERVAL_HOURS)
    span = closed_at - opened
    if span >= timedelta(hours=24):
        return f"{opened.strftime('%m-%d %H:%M')}~{closed_at.strftime('%m-%d %H:%M')} UTC +9"
    return f"{opened.strftime('%H:%M')}~{closed_at.strftime('%H:%M')} UTC +9"


def _elapsed_label(
    memory: NewsReportMemory | None,
    market: str,
    closed_at: datetime,
) -> str:
    """섹션 머리에 붙일 「마지막 보고 이후 N시간」. 첫 보고면 빈 문자열이다."""
    opened = memory.last_published_at(market) if memory is not None else None
    if opened is None:
        return ""
    hours = int((closed_at - opened).total_seconds() // 3600)
    if hours >= 24:
        return f"마지막 보고 이후 {hours // 24}일 {hours % 24}시간"
    return f"마지막 보고 이후 {max(hours, 1)}시간"


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
                "exploration": bool(item.get("prefilter_exploration")),
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
    elapsed: str = "",
) -> str:
    """시장 하나의 상황 보고서 섹션. result가 없으면 제목만 나열한다.

    근거 기사는 앞 `NEWS_REPORT_SHOWN_HIGHLIGHTS`건만 붙인다. 본문이 판단이고
    목록은 그 각주라, 목록이 길어질수록 글이 아니라 뉴스 나열로 읽힌다.
    나머지 근거도 `_log_highlights`가 NewsLog와 사전선별 라벨에 그대로 넣는다 —
    줄이는 것은 표시 분량이지 라벨 공급량이 아니다.
    """
    label = _MARKET_LABELS.get(market, market)
    head = f"<b>[{html.escape(label)}]</b> 수집 {len(items)}건"
    if elapsed:
        head = f"{head} · {html.escape(elapsed)}"
    lines = [head]
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
    shown = result["highlights"][:NEWS_REPORT_SHOWN_HIGHLIGHTS]
    for highlight in shown:
        lines.append(_highlight_text(items[highlight["index"]], highlight))
    hidden = len(result["highlights"]) - len(shown)
    if hidden > 0:
        lines.append(f"<i>이 판단이 읽은 기사 {hidden}건 더</i>")
    return "\n\n".join(lines)


async def _analyze_market(
    analyzer: NewsReportAnalyzer,
    market: str,
    window: str,
    items: list[dict],
    previous: dict | None = None,
    must_publish: bool = False,
) -> dict | None:
    headlines = _headline_payload(items[:NEWS_REPORT_MAX_HEADLINES])
    try:
        return await run_non_urgent(
            analyzer.analyze, market, window, headlines, previous, must_publish
        )
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



async def _record_evaluations(
    market: str,
    items: list[dict],
    result: dict,
    prefilter=None,
) -> None:
    """미선정 표본의 중요도 평가를 사전선별 학습에만 넣는다.

    사용자 뉴스 로그나 사건 재탕 차단에는 넣지 않는다 — 사용자가 본 적 없는
    기사다. **보류한 구간에도 이 평가는 기록한다.** 호출은 이미 나갔고, 제목의
    중요도 판정은 발행 여부와 무관한 사실이다. 보류가 길어지는 동안 라벨이
    0건으로 마르면 사전선별 학습이 조용히 멈춘다.
    """
    if prefilter is None:
        return
    for evaluation in result.get("evaluations", []):
        item = items[evaluation["index"]]
        candidate_id = str(item.get("prefilter_candidate_id") or "")
        if not candidate_id:
            continue
        try:
            await prefilter.record_outcome(
                candidate_id=candidate_id, impact=evaluation["impact"],
                sentiment=None, selected=False,
            )
        except Exception as exc:
            logger.error("[NEWS REPORT] %s 학습 평가 저장 실패: %s", market, exc)


async def _hold_market(
    memory: NewsReportMemory | None,
    market: str,
    reason: str,
) -> None:
    """이 구간에 이 시장을 발행하지 않는다. 기사는 큐에 그대로 남는다."""
    logger.info("[NEWS REPORT] %s 보류: %s", market, reason)
    if memory is None:
        return
    try:
        await memory.record_held(market, reason)
    except Exception as exc:
        logger.error("[NEWS REPORT] %s 보류 기록 실패: %s", market, exc)


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


def _public_news(published: list, closed_at: datetime) -> list[dict]:
    """공개용 필드만 내보낸다. URL은 수집 원본에서 가져오며 모델에 맡기지 않는다."""
    documents = []
    for market, items, result, window in published:
        stamp = closed_at.isoformat(timespec="seconds")
        label = _MARKET_LABELS.get(market, market)
        if result and result.get("analysis"):
            documents.append({
                "id": f"report:{market}:{stamp}", "kind": "report", "market": market,
                "title": f"{label} 시장상황 보고서 · {window}",
                "text": result["analysis"], "date": closed_at.date().isoformat(),
                "published_at": stamp, "source": "눈치 시장상황 보고서", "url": "",
            })
        highlights = result["highlights"] if result else [
            {"index": index, "title": item["title"]}
            for index, item in enumerate(items[:_FALLBACK_HEADLINE_LIMIT])
        ]
        for highlight in highlights:
            item = items[highlight["index"]]
            occurred = parse_news_datetime(item.get("published_at"), item.get("published_date"))
            # 원문 발행 시각이 없으면 보고서 발행일을 쓰고 시각은 미상으로 표시한다.
            day = ensure_jst(occurred).date() if occurred else closed_at.date()
            documents.append({
                "id": f"news:{market}:{item['article_id']}", "kind": "news", "market": market,
                "title": highlight["title"], "text": str(item.get("title") or ""),
                "date": day.isoformat(),
                "published_at": ensure_jst(occurred).isoformat() if occurred else "",
                "source": str(item.get("label") or item.get("source") or ""),
                "url": str(item.get("url") or ""), "sentiment": highlight.get("sentiment"),
            })
    return documents


async def send_news_report(app: Application) -> None:
    """큐의 기사를 시장별로 분석해 3시간 보고서를 보내고 큐를 비운다."""
    async with _REPORT_LOCK:
        await _send_news_report(app)


@burst_job("시장상황 보고서")
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
    memory: NewsReportMemory | None = app.bot_data.get("news_report_memory")
    closed_at = now()

    sections: list[str] = []
    published: list[tuple[str, list[dict], dict | None, str]] = []
    for market, market_items in group_by_market(items):
        window = _market_window(memory, market, opened_at, closed_at)
        # 상한을 넘겼으면 판정과 무관하게 발행한다. 보류는 재료가 쌓일 때까지
        # 기다리는 것이지 그 시장을 영영 덮는 것이 아니고, 사전선별의 라벨
        # 공급원도 이 보고서 하나뿐이다.
        held_hours = memory.held_hours(market) if memory is not None else 0.0
        must_publish = held_hours >= NEWS_REPORT_MAX_HELD_HOURS

        if len(market_items) < NEWS_REPORT_MIN_ARTICLES and not must_publish:
            # 1차 게이트. 여기서 걸린 시장은 LLM을 부르지 않는다.
            await _hold_market(
                memory, market, f"재료가 얇다 — 기사 {len(market_items)}건"
            )
            continue

        result = await _analyze_market(
            analyzer,
            market,
            window,
            market_items,
            memory.previous(market) if memory is not None else None,
            must_publish,
        )
        if result is None:
            # 분석이 실패했다. 상한 전이면 다음 구간이 같은 기사로 다시 본다 —
            # 원문 제목 나열은 상한에 닿았을 때의 마지막 수단이다.
            if not must_publish:
                await _hold_market(memory, market, "분석 실패, 다음 구간에 다시 본다")
                continue
        elif not result["publish"]:
            await _record_evaluations(market, market_items, result, prefilter)
            await _hold_market(
                memory, market, result["hold_reason"] or "직전 보고서 대비 새로운 것이 없다"
            )
            continue

        sections.append(
            format_market_section(
                market,
                market_items,
                result,
                _elapsed_label(memory, market, closed_at),
            )
        )
        published.append((market, market_items, result, window))
        if result is not None:
            await _log_highlights(market, market_items, result, news_log, prefilter)
            await _record_evaluations(market, market_items, result, prefilter)

    if not sections:
        # 할 말이 있는 시장이 없다. 기사는 큐에 남아 다음 구간이 더 두꺼운
        # 재료로 다시 본다 — 빈 보고서를 보내는 것보다 낫다.
        logger.info(
            "[NEWS REPORT] 발행할 시장이 없어 보내지 않는다 (큐 %d건 유지)", len(items)
        )
        return

    header = (
        f"🧭 <b>시장상황 보고서</b>\n"
        f"{closed_at.strftime('%m-%d %H:%M')} UTC +9 · 시장 {len(sections)}곳"
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

    try:
        from services.web.export import publish_news

        await asyncio.to_thread(publish_news, _public_news(published, closed_at))
    except Exception:
        logger.exception("[NEWS REPORT] 공개 검색 자료 저장 실패")

    # **발행한 시장의 기사만** 확정하고 큐에서 뺀다. 보류한 시장의 기사는
    # 큐에 남아 다음 구간의 재료가 된다.
    published_ids = {
        str(item.get("article_id") or "")
        for _, market_items, _, _ in published
        for item in market_items
    }
    for article_id in published_ids:
        await tracker.confirm(article_id)
    await tracker.persist()
    await queue.drop(published_ids)
    if memory is not None:
        for market, _, result, window in published:
            try:
                await memory.record_published(
                    market, window, (result or {}).get("analysis", "")
                )
            except Exception as exc:
                logger.error("[NEWS REPORT] %s 발행 기록 실패: %s", market, exc)
    logger.info(
        "[NEWS REPORT] 보고서 전송 완료 · 시장 %d곳 · 기사 %d건 확정 (큐 %d건 보류)",
        len(published),
        len(published_ids),
        len(items) - len(published_ids),
    )


async def run_news_report_job(app: Application) -> None:
    """예약 실행 경계. 받을 사람이 없으므로 실패를 여기서 삼킨다."""
    try:
        await send_news_report(app)
    except Exception:
        logger.error("[NEWS REPORT] 예약 실행 실패", exc_info=True)
