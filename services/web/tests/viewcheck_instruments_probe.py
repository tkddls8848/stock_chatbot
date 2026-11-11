"""뷰 검증의 ETF·ETN 표(`viewcheck/instruments.py`)를 공개 상장 목록과 대조하는 독립 프로브.

ETN은 만기가 있고 ETF도 이름이 바뀌거나 상장폐지된다. 표가 낡으면 없는 상품을 포지션으로 낸다.
테스트 러너와 무관하게 손으로 돌린다(외부 호출이라 pytest에 넣지 않는다).

    python -m services.web.tests.viewcheck_instruments_probe

한국 상품은 네이버 금융의 ETF·ETN 목록에서 코드와 이름을, 미국 상품은 나스닥 심볼 디렉터리에서 티커·
ETF 표시·상장 거래소를 본다. 어긋난 줄을 적고, 하나라도 있으면 종료 코드 1이다.
"""

from __future__ import annotations

import csv
import io
import json
import sys

import requests

from services.web.viewcheck.instruments import BY_CODE

KR_LISTS = {
    "ETF": ("https://finance.naver.com/api/sise/etfItemList.nhn", "etfItemList"),
    "ETN": ("https://finance.naver.com/api/sise/etnItemList.nhn", "etnItemList"),
}
US_LISTS = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol", None),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol", "Exchange"),
)
# 나스닥 디렉터리의 거래소 글자 → 정규 코드의 거래소(봇 `stocks/universe.py`의 표기와 같다).
US_EXCHANGES = {"N": "NYSE", "P": "NYSEARCA", "Z": "CBOE", "A": "NYSEAMERICAN"}
TIMEOUT_SECONDS = 30


def _kr_listing() -> dict[str, tuple[str, str]]:
    """코드 → (종류, 이름)."""
    listing = {}
    for kind, (url, key) in KR_LISTS.items():
        response = requests.get(url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        for row in json.loads(response.content.decode("euc-kr"))["result"][key]:
            listing[str(row["itemcode"])] = (kind, " ".join(str(row["itemname"]).split()))
    return listing


def _us_listing() -> dict[str, tuple[str, bool]]:
    """티커 → (거래소, ETF 여부)."""
    listing = {}
    for url, symbol, exchange in US_LISTS:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        for row in csv.DictReader(io.StringIO(response.text), delimiter="|"):
            ticker = str(row.get(symbol) or "").strip()
            raw = str(row.get(exchange) or "").strip() if exchange else "NASDAQ"
            listing[ticker] = (US_EXCHANGES.get(raw, raw), str(row.get("ETF") or "").upper() == "Y")
    return listing


def main() -> int:
    kr, us = _kr_listing(), _us_listing()
    problems = []
    for code, item in BY_CODE.items():
        _, exchange, symbol = code.split(":")
        if item.market == "KR":
            listed = kr.get(symbol)
            if listed is None:
                problems.append(f"{code} {item.name}: 상장 목록에 없음")
            elif listed != (item.kind, item.name):
                problems.append(f"{code} {item.name}: 목록에는 {listed[0]} '{listed[1]}'")
        else:
            listed = us.get(symbol)
            if listed is None:
                problems.append(f"{code} {item.name}: 상장 목록에 없음")
            elif listed != (exchange, True):
                problems.append(f"{code} {item.name}: 목록에는 거래소 {listed[0]}, ETF={listed[1]}")
    for line in problems:
        print(line)
    print(f"대조 {len(BY_CODE)}개, 어긋남 {len(problems)}개")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
