"""Country-specific listed-stock universe collectors."""

from __future__ import annotations

import csv
import io

import requests

NASDAQ_DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

_NUMERIC_CODE_WIDTHS = {"CN": 6, "HK": 5, "KR": 6}
_US_EXCHANGE_NAMES = {
    "A": "NYSEAMERICAN",
    "N": "NYSE",
    "P": "NYSEARCA",
    "V": "IEX",
    "Z": "CBOE",
}


def _canonical_exchange(market: str, exchange: str) -> str:
    normalized = str(exchange or "").strip().upper()
    if market == "US":
        return _US_EXCHANGE_NAMES.get(normalized, normalized)
    return normalized


def stock_key(market: str, exchange: str, code: str) -> str:
    """Return the canonical identifier consumed by the DB and quote providers.

    A-share and Hong Kong providers use bare numeric codes.  Korean and US
    symbols need a market/exchange prefix to avoid collisions.
    """
    normalized_market = str(market or "").strip().upper()
    normalized_code = str(code or "").strip().upper()
    width = _NUMERIC_CODE_WIDTHS.get(normalized_market)
    if width is not None and normalized_code.isdigit():
        normalized_code = normalized_code.zfill(width)
    if normalized_market in {"CN", "HK"}:
        return normalized_code
    normalized_exchange = _canonical_exchange(normalized_market, exchange)
    return f"{normalized_market}:{normalized_exchange}:{normalized_code}"


def parse_nasdaq_directory(text: str, exchange: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in csv.DictReader(io.StringIO(text), delimiter="|"):
        symbol = str(row.get("Symbol") or row.get("ACT Symbol") or "").strip().upper()
        if not symbol or symbol.startswith("File Creation Time"):
            continue
        if (
            str(row.get("Test Issue") or "N").upper() == "Y"
            or str(row.get("ETF") or "N").upper() == "Y"
        ):
            continue
        name = str(row.get("Security Name") or "").strip()
        if not name:
            continue
        raw_exchange = str(row.get("Exchange") or "").strip() or exchange
        resolved_exchange = _canonical_exchange("US", raw_exchange)
        rows.append(
            {
                "key": stock_key("US", resolved_exchange, symbol),
                "code": symbol,
                "display_name": name,
                "market": "US",
                "exchange": resolved_exchange,
                "yahoo_ticker": symbol,
            }
        )
    return rows


def fetch_us_listed() -> list[dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for url, exchange in ((NASDAQ_DIRECTORY_URL, "NASDAQ"), (OTHER_DIRECTORY_URL, "NYSE")):
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        for row in parse_nasdaq_directory(response.text, exchange):
            result[row["key"]] = row
    return list(result.values())


def fetch_kr_listed() -> list[dict[str, str]]:
    import FinanceDataReader as fdr

    frame = fdr.StockListing("KRX")
    rows: list[dict[str, str]] = []
    for _, item in frame.iterrows():
        raw_exchange = str(item.get("Market") or "").upper()
        if raw_exchange not in {"KOSPI", "KOSDAQ", "KONEX"}:
            continue
        code = str(item.get("Code") or "").zfill(6)
        name = str(item.get("Name") or "").strip()
        if not code.isdigit() or not name:
            continue
        rows.append(
            {
                "key": stock_key("KR", raw_exchange, code),
                "code": code,
                "display_name": name,
                "market": "KR",
                "exchange": raw_exchange,
                "yahoo_ticker": (
                    f"{code}.KS" if raw_exchange == "KOSPI" else f"{code}.KQ"
                ),
            }
        )
    return rows
