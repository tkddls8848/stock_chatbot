"""뉴스 감성 로그에 저장할 종목 코드 정규화 테스트."""

from telegram_bot.news.utils import signal_codes


def test_signal_codes_keeps_explicit_global_tickers():
    raw = ["JNJ.N", "NVDA", "NVDA.O", "GOOGL", "600519", "09988"]
    assert signal_codes(raw) == ["JNJ.N", "NVDA", "NVDA.O", "GOOGL", "600519", "09988"]


def test_signal_codes_normalizes_and_dedupes():
    assert signal_codes(["9988", "09988", "600519"]) == ["09988", "600519"]
    assert signal_codes([]) == []
    assert signal_codes(None) == []
