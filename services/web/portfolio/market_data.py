"""조언에 쓰는 외부 시장 자료. 조언을 요청할 때만(`POST /api/portfolio/advice`) 부른다.

| 자료 | 출처 | 쓰임 |
|---|---|---|
| 예적금 금리 | 금융감독원 금융상품통합비교공시(`FSS_API_KEY`) | 보유 예적금 금리와 시중 최고 금리의 차이 |
| 시장 금리 | 한국은행 ECOS 100대 통계지표(`ECOS_API_KEY`) | 기준금리·국고채·회사채로 채권·예금의 금리 국면 |
| 아파트 실거래가 | 국토교통부 실거래가(`MOLIT_API_KEY`) | 보유 부동산 지역의 ㎡당 중앙값으로 평가액 추정 |

**하나가 실패해도 나머지로 진행한다.** 각 결과는 `{"status": "ok"|"missing_key"|"error", ...}`
이고, 실패한 항목은 진단·화면에 "자료 없음"으로 드러난다. 추측으로 메우지 않는다.
응답 원문·키를 로그에 남기지 않는다.
"""

from __future__ import annotations

import logging
import statistics
import xml.etree.ElementTree as ElementTree
from datetime import date
from typing import Any

import requests

from services.web.core.config import (
    ECOS_API_KEY,
    ECOS_BASE_URL,
    FSS_API_KEY,
    FSS_BASE_URL,
    MARKET_DATA_TIMEOUT,
    MOLIT_API_KEY,
    MOLIT_APT_TRADE_URL,
    MOLIT_LOOKBACK_MONTHS,
)

logger = logging.getLogger(__name__)

# ECOS 100대 지표 중 쓰는 것. 이름이 조금씩 바뀌어 온 이력이 있어 앞부분으로 찾는다.
_ECOS_KEYS = {
    "base_rate": "한국은행 기준금리",
    "call_rate": "콜금리",
    "cd_91d": "CD수익률",
    "treasury_3y": "국고채수익률(3년)",
    "treasury_5y": "국고채수익률(5년)",
    "corporate_3y": "회사채수익률",
}


def _error(source: str, error: Exception | str) -> dict[str, Any]:
    reason = type(error).__name__ if isinstance(error, Exception) else str(error)
    logger.warning("[PORTFOLIO] %s 자료 실패: %s", source, reason)
    return {"status": "error", "reason": reason}


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


# ── 금감원 예적금 ───────────────────────────────────────────────────────────

def fetch_deposit_rates(session: requests.Session | None = None, *, key: str = FSS_API_KEY) -> dict[str, Any]:
    """은행권(020000) 12개월 예금·적금의 기본금리 최고값과 상위 상품."""
    if not key:
        return {"status": "missing_key"}
    session = session or requests.Session()
    result: dict[str, Any] = {"status": "ok"}
    try:
        for product, endpoint in (("deposit", "depositProductsSearch"), ("saving", "savingProductsSearch")):
            response = session.get(
                f"{FSS_BASE_URL}/{endpoint}.json",
                params={"auth": key, "topFinGrpNo": "020000", "pageNo": 1},
                timeout=MARKET_DATA_TIMEOUT,
            )
            response.raise_for_status()
            body = response.json().get("result") or {}
            if str(body.get("err_cd", "000")) != "000":
                return _error("FSS", f"err_cd={body.get('err_cd')}")
            names = {
                (row.get("fin_co_no"), row.get("fin_prdt_cd")): (row.get("kor_co_nm"), row.get("fin_prdt_nm"))
                for row in body.get("baseList") or []
            }
            offers = []
            for option in body.get("optionList") or []:
                if str(option.get("save_trm")) != "12":
                    continue
                rate = _number(option.get("intr_rate"))
                if rate is None:
                    continue
                bank, name = names.get((option.get("fin_co_no"), option.get("fin_prdt_cd")), ("", ""))
                offers.append({"bank": bank, "product": name, "rate_pct": rate,
                               "max_rate_pct": _number(option.get("intr_rate2"))})
            offers.sort(key=lambda row: row["rate_pct"], reverse=True)
            result[product] = {
                "term_months": 12,
                "best_rate_pct": offers[0]["rate_pct"] if offers else None,
                "top": offers[:3],
                "count": len(offers),
            }
    except (requests.RequestException, ValueError) as error:
        return _error("FSS", error)
    return result


# ── 한국은행 ECOS ───────────────────────────────────────────────────────────

def fetch_market_rates(session: requests.Session | None = None, *, key: str = ECOS_API_KEY) -> dict[str, Any]:
    if not key:
        return {"status": "missing_key"}
    session = session or requests.Session()
    try:
        response = session.get(f"{ECOS_BASE_URL}/KeyStatisticList/{key}/json/kr/1/100",
                               timeout=MARKET_DATA_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        return _error("ECOS", error)
    rows = (payload.get("KeyStatisticList") or {}).get("row")
    if not isinstance(rows, list):
        code = (payload.get("RESULT") or {}).get("CODE", "no rows")
        return _error("ECOS", f"result={code}")
    rates: dict[str, Any] = {}
    for field, label in _ECOS_KEYS.items():
        for row in rows:
            if str(row.get("KEYSTAT_NAME", "")).startswith(label):
                value = _number(row.get("DATA_VALUE"))
                if value is not None:
                    rates[field] = {"value_pct": value, "as_of": str(row.get("CYCLE", ""))}
                break
    if not rates:
        return _error("ECOS", "no matching indicators")
    return {"status": "ok", **rates}


# ── 국토부 아파트 실거래가 ──────────────────────────────────────────────────

def _recent_months(today: date, count: int) -> list[str]:
    months = []
    year, month = today.year, today.month
    for _ in range(count):
        months.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def _trades(xml_text: str) -> list[dict[str, Any]]:
    """응답 XML의 거래 목록. 새 API(영문 태그)와 옛 API(한글 태그)를 모두 읽는다."""
    root = ElementTree.fromstring(xml_text)
    code = (root.findtext(".//resultCode") or "").strip()
    if code not in ("", "00", "000"):
        raise ValueError(f"resultCode={code}")
    trades = []
    for item in root.iter("item"):
        amount = _number(item.findtext("dealAmount") or item.findtext("거래금액"))
        area = _number(item.findtext("excluUseAr") or item.findtext("전용면적"))
        name = (item.findtext("aptNm") or item.findtext("아파트") or "").strip()
        if amount and area:
            # 거래금액 단위는 만원이다.
            trades.append({"price_krw": amount * 10_000, "area_m2": area, "complex": name})
    return trades


def estimate_property(
    region_code: str,
    area_m2: float,
    *,
    complex_name: str = "",
    today: date,
    session: requests.Session | None = None,
    key: str = MOLIT_API_KEY,
) -> dict[str, Any]:
    """시군구(법정동 앞 5자리) 최근 몇 달 아파트 거래의 ㎡당 중앙값 × 전용면적.

    단지명을 주고 그 단지 거래가 3건 이상이면 단지 기준, 아니면 지역 기준이다.
    어느 기준을 썼는지(`basis`)와 거래 수를 같이 돌려준다 — 추정치다.
    """
    if not key:
        return {"status": "missing_key"}
    session = session or requests.Session()
    trades: list[dict[str, Any]] = []
    try:
        for month in _recent_months(today, MOLIT_LOOKBACK_MONTHS):
            response = session.get(
                MOLIT_APT_TRADE_URL,
                params={"serviceKey": key, "LAWD_CD": region_code, "DEAL_YMD": month,
                        "numOfRows": 1000, "pageNo": 1},
                timeout=MARKET_DATA_TIMEOUT,
            )
            response.raise_for_status()
            trades.extend(_trades(response.text))
    except (requests.RequestException, ValueError, ElementTree.ParseError) as error:
        return _error("MOLIT", error)
    basis = "region"
    pool = trades
    if complex_name:
        matched = [t for t in trades if complex_name.replace(" ", "") in t["complex"].replace(" ", "")]
        if len(matched) >= 3:
            basis, pool = "complex", matched
    if not pool:
        return {"status": "ok", "trade_count": 0, "estimate_krw": None, "basis": basis}
    per_m2 = statistics.median(t["price_krw"] / t["area_m2"] for t in pool)
    return {
        "status": "ok",
        "basis": basis,
        "trade_count": len(pool),
        "months": MOLIT_LOOKBACK_MONTHS,
        "median_per_m2_krw": round(per_m2),
        "estimate_krw": round(per_m2 * area_m2),
    }
