"""종목 DB 구축에 필요한 원본 목록과 이름 정규화 도구."""

import json
import logging
import re
from http.client import RemoteDisconnected
from typing import Any, Callable

import akshare as ak
import pandas as pd
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_SINA_HQ_NODE_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
}

try:
    import hanja
except ImportError:
    hanja = None

try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None

_OPENCC_S2T = None
NETWORK_ERRORS = (
    requests.exceptions.RequestException,
    ConnectionError,
    TimeoutError,
    RemoteDisconnected,
)


def _retry_on_network(func: Callable):
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(NETWORK_ERRORS),
        reraise=True,
    )(func)


def _fetch_a_code_name_sina() -> pd.DataFrame:
    """시나 전체 A주 목록(페이지당 100개 고정).

    거래소(SSE/SZSE) 공식 목록이 해외 IP를 차단할 때의 대체 소스.
    """
    rows: list[dict[str, str]] = []
    for page in range(1, 121):
        response = requests.get(
            _SINA_HQ_NODE_URL,
            params={
                "page": page,
                "num": 100,
                "sort": "symbol",
                "asc": 1,
                "node": "hs_a",
                "symbol": "",
                "_s_r_a": "init",
            },
            headers=_SINA_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        data = json.loads(response.text)
        if not data:
            break
        for item in data:
            code = str(item.get("code") or "").strip()
            name = _clean_stock_name(item.get("name"))
            if len(code) == 6 and code.isdigit() and name:
                rows.append({"code": code, "name": name})
    if not rows:
        raise RuntimeError("시나 A주 목록이 비어 있습니다")
    return pd.DataFrame(rows)


def _fetch_a_code_name():
    try:
        return _retry_on_network(ak.stock_info_a_code_name)()
    except Exception as exc:
        logger.warning("[StockDB] 거래소 A주 목록 조회 실패, 시나 목록으로 대체: %s", exc)
        return _retry_on_network(_fetch_a_code_name_sina)()


@_retry_on_network
def _fetch_hk_spot():
    return ak.stock_hk_spot()


def _classify_market(code: str) -> str:
    if len(code) <= 5:
        return "HK"
    if code.startswith(("688", "689")):
        return "STAR"
    if code.startswith(("300", "301")):
        return "CHI"
    return "SH" if code.startswith("6") else "SZ"


def _clean_stock_name(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except Exception:
        pass
    return re.sub(r"\s+", " ", str(value)).strip()


def _to_traditional_chinese(text: str) -> str:
    global _OPENCC_S2T
    if OpenCC is None:
        raise RuntimeError("opencc-python-reimplemented is required to build ko_name")
    try:
        if _OPENCC_S2T is None:
            _OPENCC_S2T = OpenCC("s2t")
        return _OPENCC_S2T.convert(text)
    except Exception as exc:
        logger.debug("[StockDB] OpenCC conversion failed: %s", exc)
        return text


def _to_korean_hanja_reading(cn_name: str) -> str:
    cn_name = _clean_stock_name(cn_name)
    if not cn_name:
        return ""
    if hanja is None:
        raise RuntimeError("hanja is required to build ko_name")
    try:
        converted = hanja.translate(_to_traditional_chinese(cn_name), "substitution")
    except Exception as exc:
        logger.debug("[StockDB] Hanja conversion failed: %s", exc)
        return cn_name
    return _clean_stock_name(converted) or cn_name


def _stock_entry(
    cn_name: str,
    market: str,
    *,
    ticker: str = "",
    exchange: str = "",
    source: str = "akshare",
) -> dict[str, str]:
    cn_name = _clean_stock_name(cn_name)
    ko_name = _to_korean_hanja_reading(cn_name)
    return {
        "display_name": ko_name or cn_name,
        "cn_name": cn_name,
        "ko_name": ko_name,
        "market": market,
        "ticker": ticker or cn_name,
        "yahoo_ticker": ticker or cn_name,
        "exchange": exchange or market,
        "asset_type": "EQUITY",
        "status": "ACTIVE",
        "source": source,
        "schema_version": 2,
        "figi": "",
        "compositeFIGI": "",
        "shareClassFIGI": "",
        "isin": "",
    }
