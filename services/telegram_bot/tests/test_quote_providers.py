import pandas as pd
import pytest

from services.telegram_bot.stocks.quotes import (
    QuoteService,
    parse_sina_limit_up,
    parse_sina_moneyflow,
    parse_tencent_quotes,
    parse_yahoo_closes,
    tencent_symbol,
    yahoo_symbol,
)


def test_tencent_symbol_maps_markets():
    assert tencent_symbol("600519") == "sh600519"
    assert tencent_symbol("000001") == "sz000001"
    assert tencent_symbol("300888") == "sz300888"
    assert tencent_symbol("430047") == "bj430047"
    assert tencent_symbol("00700") == "hk00700"
    assert tencent_symbol("AAPL") is None


def test_yahoo_symbol_maps_us_and_kr_universe_keys():
    assert yahoo_symbol("US:NASDAQ:AAPL") == "AAPL"
    assert yahoo_symbol("US:NYSE:BRK.B") == "BRK-B"
    assert yahoo_symbol("KR:KOSPI:005930") == "005930.KS"
    assert yahoo_symbol("KR:KOSDAQ:247540") == "247540.KQ"
    # 거래소를 모르는 코드는 A주와 형식이 겹쳐 시장을 단정할 수 없다.
    assert yahoo_symbol("005930") is None
    assert yahoo_symbol("KR:KONEX:123456") is None


def test_parse_yahoo_closes_computes_change_from_previous_close():
    frame = pd.DataFrame(
        {"AAPL": [100.0, 110.0], "005930.KS": [None, 70000.0]},
        index=pd.to_datetime(["2026-07-21", "2026-07-22"]),
    )

    quotes = parse_yahoo_closes(frame, ["AAPL", "005930.KS", "MISSING"])

    assert quotes["AAPL"]["price"] == 110.0
    assert quotes["AAPL"]["pct_change"] == pytest.approx(10.0)
    assert quotes["AAPL"]["amount"] is None
    # 전일 종가가 없으면 가격만 채우고 등락률은 비운다.
    assert quotes["005930.KS"]["price"] == 70000.0
    assert quotes["005930.KS"]["pct_change"] is None
    assert "MISSING" not in quotes


def test_watchlist_quotes_split_providers_by_market(monkeypatch):
    service = QuoteService()
    monkeypatch.setattr(
        service,
        "_fetch_tencent_quotes",
        lambda symbols: {
            "600519": {"price": 1253.0, "pct_change": -0.48, "amount": 1.0}
        },
    )
    monkeypatch.setattr(
        service,
        "_fetch_yahoo_quotes",
        lambda symbols: {"AAPL": {"price": 200.0, "pct_change": 1.0, "amount": None}},
    )

    quotes = service.get_watchlist_quotes(
        ["600519", "US:NASDAQ:AAPL", "KR:KOSPI:005930"]
    )

    assert set(quotes) == {"600519", "US:NASDAQ:AAPL"}
    assert quotes["US:NASDAQ:AAPL"]["price"] == 200.0


def test_watchlist_quotes_keep_working_when_one_provider_fails(monkeypatch):
    def boom(symbols):
        raise RuntimeError("provider down")

    service = QuoteService()
    monkeypatch.setattr(service, "_fetch_tencent_quotes", boom)
    monkeypatch.setattr(
        service,
        "_fetch_yahoo_quotes",
        lambda symbols: {"AAPL": {"price": 200.0, "pct_change": 1.0, "amount": None}},
    )

    quotes = service.get_watchlist_quotes(["600519", "US:NASDAQ:AAPL"])

    assert list(quotes) == ["US:NASDAQ:AAPL"]


def _fields(price: str, pct: str, amount: str) -> str:
    fields = ["0"] * 40
    fields[1] = "이름"
    fields[3] = price
    fields[32] = pct
    fields[37] = amount
    return "~".join(fields)


def test_parse_tencent_quotes_a_share_and_hk():
    text = (
        f'v_sh600519="{_fields("1253.00", "-0.48", "732273")}";\n'
        f'v_hk00700="{_fields("461.600", "-4.63", "16928705332.9")}";\n'
        'v_pv_none_match="1";'
    )
    quotes = parse_tencent_quotes(text)
    assert set(quotes) == {"600519", "00700"}
    assert quotes["600519"]["price"] == 1253.00
    assert quotes["600519"]["pct_change"] == -0.48
    assert quotes["600519"]["amount"] == 732273 * 1e4  # 만위안 → 위안
    assert quotes["00700"]["amount"] == 16928705332.9  # HK는 원 단위 그대로


def test_parse_tencent_quotes_treats_zero_price_as_missing():
    text = f'v_sz000001="{_fields("0.00", "0.00", "0")}";'
    assert parse_tencent_quotes(text)["000001"]["price"] is None


def test_parse_sina_moneyflow_computes_main_net_inflow():
    payload = {
        "r0_in": "300",
        "r0_out": "100",
        "r1_in": "200",
        "r1_out": "150",
        "r2_in": "50",
        "r2_out": "50",
        "r3_in": "25",
        "r3_out": "25",
    }
    flow = parse_sina_moneyflow(payload)
    assert flow["main_net_inflow"] == (300 + 200) - (100 + 150)
    assert flow["main_net_inflow_pct"] == 250 / 900 * 100


def test_parse_sina_moneyflow_rejects_incomplete_payload():
    assert parse_sina_moneyflow({"r0_in": "1"}) is None


def _ranked(code, name, trade, settlement, amount=1.0, changepercent=10.0):
    return {"code": code, "name": name, "trade": str(trade), "settlement": str(settlement),
            "amount": amount, "changepercent": changepercent}


def test_parse_sina_limit_up_uses_each_board_price_limit():
    rows = [
        _ranked("600001", "主板涨停", 11.0, 10.0),        # 10% → 11.00
        _ranked("000002", "主板未封", 10.99, 10.0),       # 10.99 < 11.00
        _ranked("300003", "创业板", 12.0, 10.0),          # 20%
        _ranked("688004", "科创板", 11.0, 10.0),          # 20% → 11.00은 아직 아니다
        _ranked("600005", "*ST某某", 5.25, 5.0),          # ST 5% → 5.25
        _ranked("300006", "ST创业", 6.0, 5.0),            # 창업판 ST도 20%
        _ranked("920007", "北交所", 13.0, 10.0),          # 30%
        _ranked("600008", "N新股", 20.0, 10.0),           # 상장 초기 제한 없음
        _ranked("600009", "停牌", 0, 10.0),               # 거래 없음
    ]

    names = [hit["name"] for hit in parse_sina_limit_up(rows)]

    assert names == ["主板涨停", "创业板", "*ST某某", "ST创业", "北交所"]


def test_parse_sina_limit_up_rounds_the_limit_price_half_up():
    # 7.35 × 1.1 = 8.085 → 거래소 규칙대로 반올림해 8.09다. 8.08은 涨停이 아니다.
    assert parse_sina_limit_up([_ranked("600010", "반올림", 8.08, 7.35)]) == []
    assert len(parse_sina_limit_up([_ranked("600010", "반올림", 8.09, 7.35)])) == 1


def test_zt_pool_summary_stops_below_the_narrowest_limit(monkeypatch):
    """push2ex는 해외 IP를 막는다(2026-10). 시나 상승률 순위를 5% 밑까지 읽고 멈춘다."""
    pages = {
        1: [_ranked("600001", "작은", 11.0, 10.0, amount=1.0), _ranked("600002", "큰", 22.0, 20.0, amount=9.0)],
        2: [_ranked("600003", "끝", 4.0, 4.0, changepercent=0.0)],
    }
    calls = []

    class _Response:
        def __init__(self, rows):
            self._rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return self._rows

    def fake_get(url, params, headers, timeout):
        calls.append(params["page"])
        return _Response(pages[params["page"]])

    monkeypatch.setattr("services.telegram_bot.stocks.quotes.requests.get", fake_get)

    summary = QuoteService().get_zt_pool_summary()

    assert summary == {"count": 2, "names": ["큰", "작은"]}
    assert calls == [1, 2]
