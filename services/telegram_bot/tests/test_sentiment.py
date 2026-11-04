

from services.telegram_bot.news.utils import normalize_stock_code


def test_normalize_stock_code():
    assert normalize_stock_code("700") == "00700"
    assert normalize_stock_code("600519") == "600519"
    assert normalize_stock_code("SZ000665") == "000665"
    assert normalize_stock_code("") == ""
