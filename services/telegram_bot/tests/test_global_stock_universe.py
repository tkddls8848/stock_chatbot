"""미국·한국 종목 유니버스와 시장 선택 입력의 회귀 테스트."""

import json

import services.telegram_bot.stocks.database as stock_database_module
from services.telegram_bot.stocks import StockDatabase
from services.telegram_bot.stocks.quotes import tencent_symbol, yahoo_symbol
from services.telegram_bot.stocks.universe import parse_nasdaq_directory, stock_key
from services.telegram_bot.watchlist.handlers import normalize_selected_stock_code


def test_parse_nasdaq_directory_keeps_common_stock_and_exchange():
    rows = parse_nasdaq_directory(
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        "NVDA|NVIDIA Corporation Common Stock|Q|N|N|100|N|N\n"
        "QQQ|Invesco QQQ Trust|Q|N|N|100|Y|N\n"
        "File Creation Time: 20260717\n",
        "NASDAQ",
    )

    assert rows == [
        {
            "key": "US:NASDAQ:NVDA",
            "code": "NVDA",
            "display_name": "NVIDIA Corporation Common Stock",
            "market": "US",
            "exchange": "NASDAQ",
            "yahoo_ticker": "NVDA",
        }
    ]


def test_parse_otherlisted_uses_act_symbol_and_canonical_exchange_names():
    rows = parse_nasdaq_directory(
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "IBM|International Business Machines Common Stock|N|IBM|N|100|N|IBM\n"
        "AMEX|American Exchange Example|A|AMEX|N|100|N|AMEX\n"
        "ARCA|Arca Example|P|ARCA|N|100|N|ARCA\n"
        "IEXG|IEX Example|V|IEXG|N|100|N|IEXG\n"
        "CBOE|Cboe Example|Z|CBOE|N|100|N|CBOE\n"
        "SKIP|ETF Example|P|SKIP|Y|100|N|SKIP\n",
        "NYSE",
    )

    assert {row["code"]: row["exchange"] for row in rows} == {
        "IBM": "NYSE",
        "AMEX": "NYSEAMERICAN",
        "ARCA": "NYSEARCA",
        "IEXG": "IEX",
        "CBOE": "CBOE",
    }
    assert {row["code"]: row["key"] for row in rows} == {
        "IBM": "US:NYSE:IBM",
        "AMEX": "US:NYSEAMERICAN:AMEX",
        "ARCA": "US:NYSEARCA:ARCA",
        "IEXG": "US:IEX:IEXG",
        "CBOE": "US:CBOE:CBOE",
    }


def test_stock_key_prevents_same_numeric_code_collisions():
    assert stock_key("CN", "SH", "600519") == "600519"
    assert stock_key("HK", "HKEX", "700") == "00700"
    assert stock_key("KR", "KOSPI", "005930") == "KR:KOSPI:005930"
    assert stock_key("US", "NYSE", "BRK.B") == "US:NYSE:BRK.B"
    assert stock_key("US", "N", "ibm") == "US:NYSE:IBM"


def test_selected_market_normalizes_only_its_expected_input():
    assert normalize_selected_stock_code("CN:SH", "600519") == "600519"
    assert normalize_selected_stock_code("HK:HKEX", "700") == "00700"
    assert normalize_selected_stock_code("KR:KOSPI", "5930") == "KR:KOSPI:005930"
    assert normalize_selected_stock_code("US:NASDAQ", "nvda") == "US:NASDAQ:NVDA"
    assert normalize_selected_stock_code("US:NYSE", "ibm") == "US:NYSE:IBM"
    assert normalize_selected_stock_code("US:NASDAQ", "005930") is None
    assert normalize_selected_stock_code("malformed", "NVDA") is None


def test_selected_codes_resolve_to_db_and_quote_provider_symbols(tmp_path):
    nasdaq_rows = parse_nasdaq_directory(
        "Symbol|Security Name|Test Issue|ETF\n"
        "NVDA|NVIDIA Corporation Common Stock|N|N\n",
        "NASDAQ",
    )
    nyse_rows = parse_nasdaq_directory(
        "ACT Symbol|Security Name|Exchange|ETF|Test Issue\n"
        "IBM|International Business Machines Common Stock|N|N|N\n",
        "NYSE",
    )
    entries: dict[str, dict] = {
        "600519": {"display_name": "Kweichow Moutai", "market": "SH"},
        "00700": {"display_name": "Tencent", "market": "HK"},
        "KR:KOSPI:005930": {
            "display_name": "Samsung Electronics",
            "market": "KR",
        },
    }
    StockDatabase._build_market_universe(
        entries,
        {},
        "US",
        lambda: nasdaq_rows + nyse_rows,
    )
    cache_file = tmp_path / "stock_db.json"
    cache_file.write_text(json.dumps(entries), encoding="utf-8")
    stock_db = StockDatabase(cache_file)
    stock_db.load()

    canonical = {
        selection: normalize_selected_stock_code(selection, raw)
        for selection, raw in (
            ("CN:SH", "600519"),
            ("HK:HKEX", "700"),
            ("KR:KOSPI", "5930"),
            ("US:NASDAQ", "nvda"),
            ("US:NYSE", "ibm"),
        )
    }

    assert canonical == {
        "CN:SH": "600519",
        "HK:HKEX": "00700",
        "KR:KOSPI": "KR:KOSPI:005930",
        "US:NASDAQ": "US:NASDAQ:NVDA",
        "US:NYSE": "US:NYSE:IBM",
    }
    for code in canonical.values():
        assert code is not None
        assert stock_db.resolve_code(code) == code
        assert stock_db.get_display_name(code)

    assert stock_db.resolve_code("CN:SH:600519") == "600519"
    assert stock_db.resolve_code("HK:HKEX:00700") == "00700"
    assert tencent_symbol(canonical["CN:SH"]) == "sh600519"
    assert tencent_symbol(canonical["HK:HKEX"]) == "hk00700"
    assert yahoo_symbol(canonical["KR:KOSPI"]) == "005930.KS"
    assert yahoo_symbol(canonical["US:NASDAQ"]) == "NVDA"
    assert yahoo_symbol(canonical["US:NYSE"]) == "IBM"


def test_resolve_code_preserves_scoped_market_and_unique_bare_aliases(tmp_path):
    cache_file = tmp_path / "stock_db.json"
    entries = {
        "000810": {"display_name": "CN collision", "market": "SZ"},
        "KR:KOSPI:000810": {
            "display_name": "KR collision",
            "market": "KR",
        },
        "US:NASDAQ:NVDA": {"display_name": "NVIDIA", "market": "US"},
        "US:NASDAQ:DUP": {"display_name": "Duplicate A", "market": "US"},
        "US:NYSE:DUP": {"display_name": "Duplicate B", "market": "US"},
    }
    cache_file.write_text(json.dumps(entries), encoding="utf-8")
    stock_db = StockDatabase(cache_file)
    stock_db.load()

    assert stock_db.resolve_code("000810") == "000810"
    assert stock_db.resolve_code("KR:KOSDAQ:000810") == "KR:KOSPI:000810"
    assert stock_db.resolve_code("CN:SH:810") == "000810"
    assert stock_db.resolve_code("HK:HKEX:000810") is None
    assert stock_db.resolve_code("NVDA") == "US:NASDAQ:NVDA"
    assert stock_db.resolve_code("US:NYSE:NVDA") == "US:NASDAQ:NVDA"
    assert stock_db.resolve_code("DUP") is None
    assert stock_db.resolve_code("US:CBOE:DUP") is None


def test_build_preserves_complete_cache_when_all_providers_fail(
    tmp_path,
    monkeypatch,
):
    cache_file = tmp_path / "stock_db.json"
    old_db = {
        "600519": {
            "display_name": "Kweichow Moutai",
            "cn_name": "贵州茅台",
            "market": "SH",
            "ticker": "600519.SS",
            "exchange": "SH",
            "currency": "CNY",
            "industry": "Beverages",
            "market_cap_cny": 1.23,
            "custom_metadata": {"keep": True},
        },
        "00700": {
            "display_name": "Tencent",
            "market": "HK",
            "ticker": "0700.HK",
            "exchange": "HKEX",
            "currency": "HKD",
            "industry": "Internet",
        },
        "KR:KOSPI:005930": {
            "display_name": "Samsung Electronics",
            "market": "KR",
            "ticker": "005930",
            "yahoo_ticker": "005930.KS",
            "exchange": "KOSPI",
            "currency": "KRW",
            "source": "cached-kr-source",
            "schema_version": 3,
        },
        "US:NYSE:IBM": {
            "display_name": "International Business Machines",
            "market": "US",
            "ticker": "IBM",
            "yahoo_ticker": "IBM",
            "exchange": "NYSE",
            "currency": "USD",
            "source": "cached-us-source",
            "schema_version": 3,
        },
    }
    cache_file.write_text(
        json.dumps(old_db, ensure_ascii=False),
        encoding="utf-8",
    )

    def provider_failure(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        stock_database_module,
        "_fetch_a_code_name",
        provider_failure,
    )
    monkeypatch.setattr(stock_database_module, "_fetch_hk_spot", provider_failure)
    monkeypatch.setattr(stock_database_module, "fetch_kr_listed", provider_failure)
    monkeypatch.setattr(stock_database_module, "fetch_us_listed", provider_failure)

    stock_db = StockDatabase(cache_file)
    stock_db.build()

    assert stock_db.get_all() == old_db
    assert json.loads(cache_file.read_text(encoding="utf-8")) == old_db


def test_build_preserves_cache_when_providers_return_empty(tmp_path, monkeypatch):
    cache_file = tmp_path / "stock_db.json"
    old_db = {
        "600519": {
            "display_name": "Moutai",
            "market": "SH",
            "ticker": "600519.SS",
        },
        "00700": {
            "display_name": "Tencent",
            "market": "HK",
            "ticker": "0700.HK",
        },
        "KR:KOSPI:005930": {
            "display_name": "Samsung",
            "market": "KR",
            "yahoo_ticker": "005930.KS",
        },
        "US:NYSE:IBM": {
            "display_name": "IBM",
            "market": "US",
            "yahoo_ticker": "IBM",
        },
    }
    cache_file.write_text(json.dumps(old_db), encoding="utf-8")

    class EmptyFrame:
        empty = True

    monkeypatch.setattr(stock_database_module, "_fetch_a_code_name", EmptyFrame)
    monkeypatch.setattr(stock_database_module, "_fetch_hk_spot", EmptyFrame)
    monkeypatch.setattr(stock_database_module, "fetch_kr_listed", list)
    monkeypatch.setattr(stock_database_module, "fetch_us_listed", list)

    stock_db = StockDatabase(cache_file)
    stock_db.build()

    assert stock_db.get_all() == old_db
