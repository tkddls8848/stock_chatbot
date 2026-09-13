"""국가별 뉴스 감성(`/market`) 명령 구현.

`/market`은 기사별 감성을 매번 평균하지 않고 `MarketDigestStore`의 일별
확정값을 읽는다. 확정된 날(`final=True`)은 다시 계산하지 않으므로 같은
기간을 다시 조회해도 값이 변하지 않는다.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.core.config import (
    MARKET_CHART_BACKFILL_DAYS_PER_REQUEST,
    MARKET_CHART_MARKETS,
    MARKET_CHART_LOOKBACK_DAYS,
    MARKET_CHART_MIN_ARTICLES,
    MARKET_CHART_MIN_DAYS,
    MARKET_DIGEST_ARTICLES_PER_DAY,
    MARKET_DIGEST_MAX_CALLS_PER_REQUEST,
    MARKET_DIGEST_MIN_ARTICLES,
    NEWS_MARKET_BACKFILL_QUERIES,
)
from telegram_bot.core.menu_status import set_menu_button_text
from telegram_bot.core.workers import burst_job, run_non_urgent
from telegram_bot.features.market_sentiment.chart import (
    market_label,
    render_market_chart,
)
from telegram_bot.news import backfill_market_digests
from telegram_bot.state import (
    MarketDigestStore,
    market_history_gaps,
)

logger = logging.getLogger(__name__)

# 세 화면(감성·이상·폴리마켓) 모두 업로드 타임아웃을 공유한다.
# python-telegram-bot 기본값은 5초인데, Lightsail에서 PNG를 올리고 텔레그램이
# 처리한 뒤 응답 헤더를 돌려줄 때까지 그 안에 끝나지 않아 ReadTimeout으로
# 끊긴다. 차트는 이미 만들어진 뒤라 여기서 죽으면 백필까지 통째로 버린다.
_CHART_UPLOAD_TIMEOUT_SECONDS = 30


# ══════════════════════════════════════════════════════════════════
# /market — 국가별 뉴스 감성
# ══════════════════════════════════════════════════════════════════

def _spread_backfill_days(days: list, limit: int) -> list:
    """기간의 앞·중간·끝을 보존하면서 한 요청의 보충 연산량을 제한한다."""
    if len(days) <= limit:
        return days
    if limit <= 1:
        return [days[-1]]
    indexes = {
        round(index * (len(days) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [days[index] for index in sorted(indexes)]


async def _set_market_status(message, callback_data: str, status, text: str) -> None:
    if callback_data:
        await set_menu_button_text(message, callback_data, text)
    elif status is not None:
        await status.edit_text(text)


async def _report_market_failure(
    message, callback_data: str, status, exc: Exception, what_failed: str
) -> None:
    """실패를 알린다. 어디서 끊겼는지를 문구에 남긴다.

    버튼 경로는 라벨 길이가 제한돼 단계 구분을 넣지 못하므로, 자세한 구분은
    로그와 명령 경로의 회신 문구가 맡는다.
    """
    if callback_data:
        await set_menu_button_text(message, callback_data, "❌ 생성 실패")
    elif status is not None:
        await status.edit_text(f"시장 감성 차트를 {what_failed}: {exc}")


@burst_job("시장 컨센서스 분석")
async def cmd_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send country/market news sentiment ranking and trend chart."""
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    days = MARKET_CHART_LOOKBACK_DAYS
    if args:
        try:
            days = int(args[0])
        except ValueError:
            await message.reply_text("사용법: /market [1-30일]")
            return
    if not 1 <= days <= 30:
        await message.reply_text("조회 기간은 1~30일로 지정해 주세요.")
        return

    store: MarketDigestStore | None = context.bot_data.get("market_digest_store")
    if store is None:
        await message.reply_text("시장 감성 캐시를 아직 준비하지 못했습니다.")
        return
    required_markets = set(MARKET_CHART_MARKETS)
    markets = await store.series(required_markets, days)
    gaps = market_history_gaps(
        markets,
        required_markets,
        MARKET_CHART_MIN_ARTICLES,
        min(days, MARKET_CHART_MIN_DAYS),
        MARKET_DIGEST_MIN_ARTICLES,
    )
    missing_days = await store.missing_digest_days(required_markets, days)
    backfill_days = {
        market: _spread_backfill_days(
            missing,
            MARKET_CHART_BACKFILL_DAYS_PER_REQUEST,
        )
        for market, missing in missing_days.items()
    }
    backfill_markets = {
        market for market, market_days in backfill_days.items() if market_days
    }
    needs_backfill = bool(backfill_markets)
    query = getattr(update, "callback_query", None)
    callback_data = str(getattr(query, "data", ""))
    if not callback_data.startswith("nav:market:"):
        callback_data = ""
    status = None
    if callback_data:
        await _set_market_status(message, callback_data, None, "◐ 데이터 점검 중")
    else:
        status = await message.reply_text(
            "차트용 일별 시장 감성을 점검하는 중입니다..."
        )
    try:
        if needs_backfill:
            analyzer = context.bot_data.get("market_digest_analyzer")
            # 번역과 같은 Cloudflare 무료 할당량을 쓰므로 세마포어로 묶는다.
            semaphore = context.bot_data.get("market_digest_semaphore")
            if analyzer is None or semaphore is None:
                await _set_market_status(
                    message,
                    callback_data,
                    status,
                    "⚠️ 분석기 준비 안 됨",
                )
                return
            await _set_market_status(
                message,
                callback_data,
                status,
                "◓ 과거 뉴스 분석 중",
            )
            await backfill_market_digests(
                store,
                analyzer,
                semaphore,
                backfill_markets,
                NEWS_MARKET_BACKFILL_QUERIES,
                backfill_days,
                articles_per_day=MARKET_DIGEST_ARTICLES_PER_DAY,
                min_articles=MARKET_DIGEST_MIN_ARTICLES,
                max_calls=MARKET_DIGEST_MAX_CALLS_PER_REQUEST,
            )
            markets = await store.series(required_markets, days)
            gaps = market_history_gaps(
                markets,
                required_markets,
                MARKET_CHART_MIN_ARTICLES,
                min(days, MARKET_CHART_MIN_DAYS),
                MARKET_DIGEST_MIN_ARTICLES,
            )
        ready_markets = {market: stats for market, stats in markets.items() if market not in gaps}
        if len(ready_markets) < 2:
            detail = ", ".join(f"{market}: {reason}" for market, reason in sorted(gaps.items()))
            if callback_data:
                await _set_market_status(
                    message,
                    callback_data,
                    status,
                    "⚠️ 데이터 부족",
                )
            else:
                await status.edit_text(
                    "차트를 그릴 만큼 신뢰할 수 있는 국가별 시계열을 확보하지 못했습니다.\n"
                    f"부족 항목: {detail}\n"
                    "불완전한 선이나 단일 점 차트는 만들지 않았습니다. 잠시 뒤 다시 시도해 주세요."
                )
            return
        await _set_market_status(
            message,
            callback_data,
            status,
            "◑ 차트 생성 중",
        )
        image = await run_non_urgent(render_market_chart, ready_markets, days)
        try:
            from web.export import publish_market

            await run_non_urgent(
                publish_market, image.getvalue(), ready_markets, days
            )
        except Exception:
            # 공개용 사본 실패가 텔레그램의 본래 차트 전송을 막으면 안 된다.
            logger.warning("[WEBPUB] 시장 산출물 저장 실패", exc_info=True)
        ranking = " | ".join(
            f"{market_label(market)} {stats['avg_sentiment']:+.2f} ({stats['count']})"
            for market, stats in sorted(ready_markets.items(), key=lambda item: item[1]["avg_sentiment"], reverse=True)
        )
        # 하루 평균 표본 수를 함께 노출한다. 표본이 얕으면 선이 출렁이므로
        # 값만 보여 주면 신뢰도를 오해하기 쉽다.
        sample_note = ""
        day_counts = [
            point["count"]
            for stats in ready_markets.values()
            for point in stats["daily"]
        ]
        if day_counts:
            sample_note = f"\n하루 평균 표본 {sum(day_counts) / len(day_counts):.0f}건"
        try:
            await message.reply_photo(
                photo=image,
                caption=(
                    f"국가·증시별 뉴스 감성 — 최근 {days}일\n{ranking}{sample_note}\n\n"
                    "점수는 하루치 헤드라인을 종합한 분위기 지표(-1~+1)이며 투자 조언이 아닙니다."
                ),
                read_timeout=_CHART_UPLOAD_TIMEOUT_SECONDS,
                write_timeout=_CHART_UPLOAD_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            # 전송 실패를 렌더링 실패와 같은 줄로 찍으면 matplotlib을 들여다보게
            # 된다. 여기까지 왔다면 차트는 이미 만들어졌고 끊긴 곳은 네트워크다.
            logger.exception("[MARKET] chart upload failed")
            await _report_market_failure(
                message, callback_data, status, exc, "전송하지 못했습니다"
            )
            return
        if callback_data:
            await set_menu_button_text(message, callback_data, f"{days}일")
        else:
            await status.delete()
    except Exception as exc:
        logger.exception("[MARKET] chart rendering failed")
        await _report_market_failure(
            message, callback_data, status, exc, "만들지 못했습니다"
        )
