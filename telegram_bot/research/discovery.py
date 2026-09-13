"""리서치 후보군 확장: 시장별 발굴 소스.

- 중화권: 강세 섹터 구성종목(시나 업종 보드) + 问财 자연어 스크리닝(옵션)
- 미국:   Yahoo Finance 프리셋 스크리너(yfinance.screen)
- 한국:   FinanceDataReader KRX 시세 목록의 등락률 상위

모든 함수는 블로킹이므로 핸들러에서 asyncio.to_thread(run_non_urgent)로 호출한다.
결과는 candidate_universe 항목과 같은 형태의 dict 목록이며, 실패는
빈 목록으로 조용히 처리한다(보조 소스).
"""

import logging
from typing import Any

from telegram_bot.core.config import (
    RESEARCH_KR_CANDIDATE_LIMIT,
    RESEARCH_KR_CANDIDATES_ENABLED,
    RESEARCH_KR_MIN_TRADING_VALUE,
    RESEARCH_SECTOR_CANDIDATE_LIMIT,
    RESEARCH_SECTOR_CANDIDATES_ENABLED,
    RESEARCH_US_CANDIDATE_LIMIT,
    RESEARCH_US_CANDIDATES_ENABLED,
    RESEARCH_US_SCREENERS,
)
from telegram_bot.stocks import StockDatabase
from telegram_bot.stocks.universe import stock_key

logger = logging.getLogger(__name__)

_SECTOR_BOARDS_TO_SCAN = 3
_US_SCREENER_LABELS = {
    "day_gainers": "상승률 상위",
    "day_losers": "하락률 상위",
    "most_actives": "거래량 상위",
    "small_cap_gainers": "중소형 상승",
    "undervalued_growth_stocks": "저평가 성장주",
    "growth_technology_stocks": "성장 기술주",
    "aggressive_small_caps": "공격적 중소형",
    "most_shorted_stocks": "공매도 상위",
}
_KR_EXCHANGES = ("KOSPI", "KOSDAQ", "KONEX")
# FinanceDataReader의 등락률 컬럼명은 버전에 따라 오타(ChagesRatio)가 섞여 있다.
_KR_PCT_COLUMNS = ("ChagesRatio", "ChangesRatio", "ChangeRatio")


def _candidate_entry(
    code: str,
    name: str,
    market: str,
    watchlist: dict[str, str],
    relation_keyword: str,
    relation_reason: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "market": market,
        "in_watchlist": code in watchlist,
        "matched_news": [],
        "relation_keyword": relation_keyword,
        "relation_reason": relation_reason,
    }


def _pct_part(value: Any) -> str:
    return f" ({value:+.2f}%)" if isinstance(value, (int, float)) else ""


def build_sector_candidates(
    quote_service,
    stock_db: StockDatabase,
    watchlist: dict[str, str],
    limit: int = RESEARCH_SECTOR_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """오늘 강세 업종 보드의 구성종목 중 종목 DB(북향 가능) 적격 종목."""
    if not RESEARCH_SECTOR_CANDIDATES_ENABLED or quote_service is None:
        return []
    try:
        top_boards = quote_service.get_sector_rankings().get("top", [])
    except Exception as e:
        logger.warning("[DISCOVERY] 섹터 랭킹 조회 실패: %s", e)
        return []

    candidates: list[dict[str, Any]] = []
    for board in top_boards[:_SECTOR_BOARDS_TO_SCAN]:
        board_name = str(board.get("name") or "")
        if not board_name:
            continue
        pct_part = _pct_part(board.get("pct_change"))
        for constituent in quote_service.get_sector_constituents(board_name):
            code = constituent["code"]
            if code in watchlist:
                continue
            display_name = stock_db.get_display_name(code)
            if not display_name:
                continue  # 북향 가능 목록 밖 → 제외
            candidates.append(
                _candidate_entry(
                    code,
                    display_name,
                    stock_db.get_market(code) or "CN",
                    watchlist,
                    relation_keyword=board_name,
                    relation_reason=f"오늘 강세 업종 {board_name}{pct_part} 구성종목",
                )
            )
            if len(candidates) >= limit:
                return candidates
    return candidates


def build_us_candidates(
    stock_db: StockDatabase,
    watchlist: dict[str, str],
    limit: int = RESEARCH_US_CANDIDATE_LIMIT,
    screeners: tuple[str, ...] = RESEARCH_US_SCREENERS,
) -> list[dict[str, Any]]:
    """Yahoo Finance 프리셋 스크리너 상위 종목 중 종목 DB에 있는 미국 주식.

    ETF·펀드 등 비주식(quoteType != EQUITY)과 DB에 없는 심볼은 제외한다.
    """
    if not RESEARCH_US_CANDIDATES_ENABLED or limit <= 0 or not screeners:
        return []
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[DISCOVERY] yfinance가 없어 미국 후보 발굴을 건너뜁니다.")
        return []

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for screener in screeners:
        if len(candidates) >= limit:
            break
        label = _US_SCREENER_LABELS.get(screener, screener)
        try:
            response = yf.screen(screener, count=max(10, limit * 2))
        except Exception as e:
            logger.warning("[DISCOVERY] 미국 스크리너 %s 조회 실패: %s", screener, e)
            continue
        quotes = (response or {}).get("quotes") or []
        for quote in quotes:
            if str(quote.get("quoteType") or "").upper() != "EQUITY":
                continue
            symbol = str(quote.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            # 종목 DB의 정식 키(US:NASDAQ:AAPL)로 승격한다. 한국·A주 6자리 코드와
            # 겹치지 않도록 US: 접두사가 붙은 결과만 채택한다.
            code = stock_db.resolve_code(symbol) or ""
            if not code.startswith("US:") or code in watchlist or code in seen:
                continue
            name = stock_db.get_display_name(code) or str(quote.get("shortName") or symbol)
            candidates.append(
                _candidate_entry(
                    code,
                    name,
                    "US",
                    watchlist,
                    relation_keyword=label,
                    relation_reason=(
                        f"미국 {label} 스크리너"
                        f"{_pct_part(quote.get('regularMarketChangePercent'))}"
                    ),
                )
            )
            seen.add(code)
            if len(candidates) >= limit:
                break
    return candidates


def build_kr_candidates(
    stock_db: StockDatabase,
    watchlist: dict[str, str],
    limit: int = RESEARCH_KR_CANDIDATE_LIMIT,
    min_trading_value: float = RESEARCH_KR_MIN_TRADING_VALUE,
) -> list[dict[str, Any]]:
    """KRX 시세 목록에서 거래대금 하한을 넘는 등락률 상위 종목.

    한국 6자리 코드는 A주 코드 형식과 겹치므로 별칭 조회 대신
    KR:거래소:코드 키를 직접 만들어 종목 DB와 대조한다.
    """
    if not RESEARCH_KR_CANDIDATES_ENABLED or limit <= 0:
        return []
    try:
        import FinanceDataReader as fdr
        import pandas as pd
    except ImportError:
        logger.warning("[DISCOVERY] FinanceDataReader가 없어 한국 후보 발굴을 건너뜁니다.")
        return []

    try:
        frame = fdr.StockListing("KRX")
    except Exception as e:
        logger.warning("[DISCOVERY] KRX 시세 목록 조회 실패: %s", e)
        return []
    if frame is None or getattr(frame, "empty", True):
        return []

    pct_column = next((c for c in _KR_PCT_COLUMNS if c in frame.columns), None)
    if pct_column is None or "Code" not in frame.columns:
        logger.warning("[DISCOVERY] KRX 목록에 등락률 컬럼이 없어 한국 후보를 건너뜁니다.")
        return []

    frame = frame.copy()
    frame["_pct"] = pd.to_numeric(frame[pct_column], errors="coerce")
    frame["_amount"] = (
        pd.to_numeric(frame["Amount"], errors="coerce")
        if "Amount" in frame.columns
        else 0.0
    )
    frame = frame.dropna(subset=["_pct"])
    if min_trading_value > 0 and "Amount" in frame.columns:
        frame = frame[frame["_amount"].fillna(0) >= min_trading_value]
    frame = frame.sort_values("_pct", ascending=False)

    candidates: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        exchange = str(row.get("Market") or "").upper()
        if exchange not in _KR_EXCHANGES:
            continue
        raw_code = str(row.get("Code") or "").strip().zfill(6)
        if not raw_code.isdigit():
            continue
        code = stock_key("KR", exchange, raw_code)
        display_name = stock_db.get_display_name(code)
        if not display_name or code in watchlist:
            continue  # 종목 DB 밖(상장폐지·미갱신) → 제외
        pct = row.get("_pct")
        candidates.append(
            _candidate_entry(
                code,
                display_name,
                "KR",
                watchlist,
                relation_keyword=f"{exchange} 상승률 상위",
                relation_reason=f"오늘 {exchange} 등락률 상위{_pct_part(pct)}",
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def _interleave_by_market(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """시장별 후보 묶음을 번갈아 배치하고 코드 중복을 제거한다.

    호출부가 남은 슬롯만큼 앞에서 잘라 쓰므로, 한 시장이 앞을 독식하면
    나머지 시장 후보가 통째로 잘려 나간다. 순서 자체가 균형 장치다.
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index >= len(group):
                continue
            candidate = group[index]
            code = candidate["code"]
            if code in seen:
                continue
            merged.append(candidate)
            seen.add(code)
    return merged


def collect_extra_candidates(
    quote_service,
    stock_db: StockDatabase,
    watchlist: dict[str, str],
) -> list[dict[str, Any]]:
    """중화권·미국·한국 발굴 후보를 시장 균형을 맞춰 반환한다(블로킹)."""
    cn_candidates = build_sector_candidates(quote_service, stock_db, watchlist)
    groups = [
        cn_candidates,
        build_us_candidates(stock_db, watchlist),
        build_kr_candidates(stock_db, watchlist),
    ]
    extras = _interleave_by_market(groups)
    if extras:
        logger.info(
            "[DISCOVERY] 추가 후보 %d개 (중화권 %d · 미국 %d · 한국 %d)",
            len(extras),
            len(groups[0]),
            len(groups[1]),
            len(groups[2]),
        )
    return extras
