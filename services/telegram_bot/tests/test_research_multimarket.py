"""리서치 후보 발굴이 미국·한국 universe 종목을 다루는지 검증."""
import json

from services.telegram_bot.research.candidates import build_research_candidate_universe
from services.telegram_bot.stocks import StockDatabase


def _db(tmp_path) -> StockDatabase:
    cache = tmp_path / "stock_db.json"
    cache.write_text(
        json.dumps(
            {
                "300750": {
                    "display_name": "CATL",
                    "cn_name": "宁德时代",
                    "ko_name": "닝더스다이",
                    "market": "SZ",
                },
                "US:NASDAQ:AAPL": {
                    "display_name": "Apple Inc. - Common Stock",
                    "cn_name": "",
                    "ko_name": "Apple Inc. - Common Stock",
                    "market": "US",
                },
                "US:NASDAQ:ONSC": {
                    "display_name": "On Semiconductor Corporation - Common Stock",
                    "cn_name": "",
                    "ko_name": "",
                    "market": "US",
                },
                "KR:KOSPI:005930": {
                    "display_name": "삼성전자",
                    "cn_name": "",
                    "ko_name": "삼성전자",
                    "market": "KR",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = StockDatabase(cache_file=cache)
    db.load()
    return db


def test_english_stopword_tokens_do_not_match_news(tmp_path):
    # "Common Stock" 같은 일반 단어로는 뉴스와 매칭되지 않아야 한다.
    candidates = build_research_candidate_universe(
        _db(tmp_path),
        watchlist={},
        news_items=[{"title": "Common stock market talk", "content": "shares up"}],
    )
    assert not any(c["code"].startswith("US:") for c in candidates)


def _db_from(entries: dict, tmp_path) -> StockDatabase:
    cache = tmp_path / "stock_db.json"
    cache.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    db = StockDatabase(cache_file=cache)
    db.load()
    return db


def test_tokens_shared_by_many_listings_do_not_match_news(tmp_path):
    # "Big Tech earnings" 한 줄이 이름에 TECH가 든 종목을 전부 끌어오면 안 된다.
    entries = {
        f"0{i:04d}": {"display_name": f"NEXION TECH {i}", "cn_name": "", "market": "HK"}
        for i in range(20)
    }
    db = _db_from(entries, tmp_path)

    candidates = build_research_candidate_universe(
        db,
        watchlist={},
        news_items=[{"title": "Stocks slide with Big Tech earnings ahead", "content": ""}],
        max_candidates=50,
        name_token_max_frequency=15,
    )

    assert candidates == []


def test_multi_word_english_names_need_two_matching_tokens(tmp_path):
    # 종목 DB에서는 드물지만 영어 기사에서는 흔한 단어(daily)가 하나 걸렸다고
    # 후보가 되면 안 된다. 이름이 한 단어인 종목은 하나만 걸려도 인정한다.
    db = _db_from(
        {
            "US:NASDAQ:DJCO": {
                "display_name": "Daily Journal Corp.",
                "cn_name": "",
                "market": "US",
            },
            "US:NASDAQ:GOOGL": {
                "display_name": "Alphabet Inc. - Class A Common Stock",
                "cn_name": "",
                "market": "US",
            },
        },
        tmp_path,
    )

    weak = build_research_candidate_universe(
        db,
        watchlist={},
        news_items=[{"title": "Wall Street daily wrap: Alphabet slides", "content": ""}],
        max_candidates=50,
    )
    assert {c["code"] for c in weak} == {"US:NASDAQ:GOOGL"}

    strong = build_research_candidate_universe(
        db,
        watchlist={},
        news_items=[{"title": "Daily Journal reports annual results", "content": ""}],
        max_candidates=50,
    )
    assert {c["code"] for c in strong} == {"US:NASDAQ:DJCO"}


def test_english_name_token_matches_news(tmp_path):
    candidates = build_research_candidate_universe(
        _db(tmp_path),
        watchlist={},
        news_items=[{"title": "Apple unveils new AI features", "content": ""}],
    )
    apple = next(c for c in candidates if c["code"] == "US:NASDAQ:AAPL")
    assert apple["matched_news"]


def test_stock_db_resolve_code_aliases(tmp_path):
    db = _db(tmp_path)
    assert db.resolve_code("300750") == "300750"
    assert db.resolve_code("aapl") == "US:NASDAQ:AAPL"
    assert db.resolve_code("005930") == "KR:KOSPI:005930"
    assert db.resolve_code("US:NASDAQ:AAPL") == "US:NASDAQ:AAPL"
    assert db.resolve_code("999999") is None


def test_normalize_code_preserves_universe_keys():
    from services.telegram_bot.research.results import normalize_code

    assert normalize_code("US:NASDAQ:AAPL") == "US:NASDAQ:AAPL"
    assert normalize_code("aapl") == "AAPL"
    assert normalize_code("300750") == "300750"
    assert normalize_code("700") == "00700"
