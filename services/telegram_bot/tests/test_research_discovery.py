"""리서치 뉴스 수집·후보 발굴이 중화권·미국·한국을 함께 다루는지 검증."""
import asyncio
import json
import sys
from datetime import timedelta

from services.telegram_bot.core.clock import now

from services.telegram_bot.news.registry import NewsSourceRegistry, SourceSpec
from services.telegram_bot.news.sources import GlobalArticle
from services.telegram_bot.research import discovery, news as research_news
from services.telegram_bot.stocks import StockDatabase


# ── 뉴스 수집(시장 균형) ─────────────────────────────

def _article(source: str, index: int, market: str = "") -> GlobalArticle:
    # 수집은 최근 뉴스 창(NEWS_LIVE_MAX_AGE_HOURS) 안의 기사만 남긴다. 그 창을
    # 재는 쪽이 KST라 발행시각도 KST로 만든다 — 호스트 시각을 쓰면 다른
    # 타임존에서 전부 창 밖으로 밀려 0건이 된다.
    published = now() - timedelta(minutes=index + 1)
    return GlobalArticle(
        article_id=f"{source}:{index}",
        title=f"{source} headline {index}",
        content="body",
        published_at=published.strftime("%Y-%m-%d %H:%M:%S"),
        url=f"https://example.com/{source}/{index}",
        extra={"market": market} if market else {},
    )


def _spec(key: str, market: str, count: int = 5) -> SourceSpec:
    return SourceSpec(
        key=key,
        label=key,
        fetch=lambda: [_article(key, i, market) for i in range(count)],
        market=market,
    )


def _collect(specs, **kwargs) -> list[dict]:
    registry = NewsSourceRegistry(specs)
    return asyncio.run(
        research_news.collect_global_market_news_items(registry=registry, **kwargs)
    )


def test_collection_rotates_markets_instead_of_filling_from_first_source():
    items = _collect(
        [_spec("futu", "CN"), _spec("gnews_us", "US"), _spec("gnews_kr", "KR")],
        max_items=6,
    )

    assert [item["market"] for item in items] == ["CN", "US", "KR", "CN", "US", "KR"]


def test_collection_falls_back_to_available_markets():
    # 미국·한국 소스가 없어도 남은 시장으로 채운다(소스당 상한까지).
    items = _collect([_spec("futu", "CN"), _spec("cls", "CN")], max_items=4)

    assert len(items) == 4
    assert {item["market"] for item in items} == {"CN"}


def test_mixed_source_articles_are_bucketed_per_article():
    mixed = SourceSpec(
        key="gnews",
        label="gnews",
        fetch=lambda: [
            _article("gnews", 0, "CN"),
            _article("gnews", 1, "KR"),
            _article("gnews", 2, "US"),
        ],
        market="",  # 혼합 소스
    )

    items = _collect([mixed], max_items=3)

    assert sorted(item["market"] for item in items) == ["CN", "KR", "US"]


def test_untagged_source_articles_still_collected():
    untagged = SourceSpec(
        key="rss:mystery",
        label="mystery",
        fetch=lambda: [_article("mystery", 0)],
        market="",
    )

    items = _collect([untagged], max_items=3)

    assert [item["market"] for item in items] == ["OTHER"]


def test_failed_source_is_recorded_and_skipped():
    def boom() -> list[GlobalArticle]:
        raise RuntimeError("source down")

    broken = SourceSpec(
        key="futu", label="futu", fetch=boom, market="CN"
    )
    registry = NewsSourceRegistry([broken, _spec("gnews_us", "US")])

    items = asyncio.run(
        research_news.collect_global_market_news_items(registry=registry, max_items=3)
    )

    assert [item["market"] for item in items] == ["US", "US", "US"]
    assert "futu: 실패 1회" in registry.status_lines()[0]


def test_raw_articles_are_used_without_translation():
    registry = NewsSourceRegistry([_spec("futu", "CN")])
    items = asyncio.run(
        research_news.collect_global_market_news_items(
            registry=registry,
            max_items=2,
        )
    )

    assert [item["title"] for item in items] == ["futu headline 0", "futu headline 1"]
    assert all("mentioned_stocks" not in item for item in items)


def test_title_falls_back_to_content_for_titleless_sources():
    titleless = SourceSpec(
        key="sina",
        label="sina",
        fetch=lambda: [
            GlobalArticle(
                article_id="sina:1",
                title="",
                content="上证指数收涨 0.5%",
                published_at=now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        ],
        market="CN",
    )

    items = _collect([titleless], max_items=1)

    assert items[0]["title"] == "上证指数收涨 0.5%"


def test_select_balanced_articles_prefers_configured_market_order():
    buckets = {
        "US": ["us1", "us2"],
        "CN": ["cn1", "cn2"],
        "OTHER": ["other1"],
    }

    selected = research_news.select_balanced_articles(
        buckets, max_items=4, markets=("CN", "US", "KR")
    )

    assert selected == ["cn1", "us1", "other1", "cn2"]


# ── 후보 발굴(미국·한국) ─────────────────────────────

def _db(tmp_path) -> StockDatabase:
    cache = tmp_path / "stock_db.json"
    cache.write_text(
        json.dumps(
            {
                "300750": {"display_name": "CATL", "cn_name": "宁德时代", "market": "SZ"},
                "US:NASDAQ:AAPL": {"display_name": "Apple Inc.", "market": "US"},
                "US:NYSE:BE": {"display_name": "Bloom Energy", "market": "US"},
                "KR:KOSPI:005930": {"display_name": "삼성전자", "market": "KR"},
                "KR:KOSDAQ:247540": {"display_name": "에코프로비엠", "market": "KR"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = StockDatabase(cache_file=cache)
    db.load()
    return db


class _FakeYFinance:
    def __init__(self, quotes_by_screener: dict[str, list[dict]]):
        self._quotes = quotes_by_screener
        self.calls: list[str] = []

    def screen(self, screener, count=None):
        self.calls.append(screener)
        return {"quotes": self._quotes.get(screener, [])}


def _install_fake_yfinance(monkeypatch, fake) -> None:
    monkeypatch.setitem(sys.modules, "yfinance", fake)


def test_us_candidates_resolve_universe_keys_and_skip_non_equity(tmp_path, monkeypatch):
    fake = _FakeYFinance(
        {
            "day_gainers": [
                {"symbol": "AAPL", "quoteType": "EQUITY", "regularMarketChangePercent": 3.2},
                {"symbol": "SPY", "quoteType": "ETF", "regularMarketChangePercent": 1.0},
                {"symbol": "NOTLISTED", "quoteType": "EQUITY"},
                {"symbol": "BE", "quoteType": "EQUITY", "regularMarketChangePercent": -0.7},
            ]
        }
    )
    _install_fake_yfinance(monkeypatch, fake)

    candidates = discovery.build_us_candidates(
        _db(tmp_path), watchlist={}, limit=5, screeners=("day_gainers",)
    )

    assert [c["code"] for c in candidates] == ["US:NASDAQ:AAPL", "US:NYSE:BE"]
    assert all(c["market"] == "US" for c in candidates)
    assert "3.20%" in candidates[0]["relation_reason"]


def test_us_candidates_skip_watchlist_and_respect_limit(tmp_path, monkeypatch):
    fake = _FakeYFinance(
        {
            "day_gainers": [
                {"symbol": "AAPL", "quoteType": "EQUITY"},
                {"symbol": "BE", "quoteType": "EQUITY"},
            ]
        }
    )
    _install_fake_yfinance(monkeypatch, fake)

    candidates = discovery.build_us_candidates(
        _db(tmp_path),
        watchlist={"US:NASDAQ:AAPL": "Apple"},
        limit=1,
        screeners=("day_gainers",),
    )

    assert [c["code"] for c in candidates] == ["US:NYSE:BE"]


def test_kr_candidates_rank_by_change_and_filter_small_trades(tmp_path, monkeypatch):
    import pandas as pd

    frame = pd.DataFrame(
        [
            # 급등이지만 거래대금이 작아 제외
            {"Code": "247540", "Market": "KOSDAQ", "ChagesRatio": 25.0, "Amount": 1_000},
            {"Code": "005930", "Market": "KOSPI", "ChagesRatio": 3.5, "Amount": 9e11},
            # 종목 DB에 없는 코드는 제외
            {"Code": "999999", "Market": "KOSPI", "ChagesRatio": 9.9, "Amount": 9e11},
        ]
    )

    class _FakeFdr:
        @staticmethod
        def StockListing(market):
            assert market == "KRX"
            return frame

    monkeypatch.setitem(sys.modules, "FinanceDataReader", _FakeFdr)

    candidates = discovery.build_kr_candidates(
        _db(tmp_path), watchlist={}, limit=5, min_trading_value=5e9
    )

    assert [c["code"] for c in candidates] == ["KR:KOSPI:005930"]
    assert candidates[0]["market"] == "KR"
    assert candidates[0]["name"] == "삼성전자"


def test_extra_candidates_interleave_markets(monkeypatch):
    def entry(code: str, market: str) -> dict:
        return {"code": code, "name": code, "market": market, "matched_news": []}

    monkeypatch.setattr(
        discovery,
        "build_sector_candidates",
        lambda *a, **k: [entry(f"60000{i}", "CN") for i in range(5)],
    )
    monkeypatch.setattr(
        discovery,
        "build_us_candidates",
        lambda *a, **k: [entry("US:NASDAQ:AAPL", "US"), entry("US:NYSE:BE", "US")],
    )
    monkeypatch.setattr(
        discovery,
        "build_kr_candidates",
        lambda *a, **k: [entry("KR:KOSPI:005930", "KR")],
    )

    extras = discovery.collect_extra_candidates(None, None, {})

    # 앞에서 잘려도 세 시장이 모두 살아남아야 한다.
    assert [c["market"] for c in extras[:3]] == ["CN", "US", "KR"]
    assert len(extras) == 8
