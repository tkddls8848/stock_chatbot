from telegram_bot.news import sources

# 소스 상한은 .env로 튜닝되는 값이다. 개발자 환경 설정에 따라 테스트가 깨지지
# 않도록 이 모듈에서는 상한을 고정해 두고 기대값을 그 값에서 유도한다.
_ARTICLE_LIMIT = 10
_QUERY_COUNT = 3
_PER_QUERY_LIMIT = (_ARTICLE_LIMIT + _QUERY_COUNT - 1) // _QUERY_COUNT


def _stub_stock_queries(monkeypatch, expected_market: str):
    monkeypatch.setattr(sources, "NEWS_SOURCE_ARTICLE_LIMIT", _ARTICLE_LIMIT)

    def fake_query(market, query, query_index, limit):
        assert market == expected_market
        assert "when:1d" in query
        assert limit == _PER_QUERY_LIMIT
        rows = [
            sources.GlobalArticle(
                article_id=f"{market.lower()}:{query_index}:{row}",
                title=f"{market} headline {query_index}-{row}",
                content="Market news",
                published_at="2026-07-18 10:00:00",
                url=f"https://example.com/{query_index}/{row}",
                extra={"market": market},
            )
            for row in range(4)
        ]
        if query_index == 2:
            rows[0] = sources.GlobalArticle(
                article_id="duplicate",
                title="Duplicate headline",
                content="Market news",
                published_at="2026-07-18 10:00:00",
                url="https://example.com/0/0",
                extra={"market": market},
            )
        return rows

    monkeypatch.setattr(sources, "_fetch_google_news_stock_query", fake_query)


def test_periodic_us_market_source_interleaves_queries(monkeypatch):
    _stub_stock_queries(monkeypatch, "US")

    articles = sources.fetch_google_news_us_stock_articles()

    assert len(articles) == 10
    assert [article.title for article in articles[:6]] == [
        "US headline 0-0",
        "US headline 1-0",
        "US headline 0-1",
        "US headline 1-1",
        "US headline 2-1",
        "US headline 0-2",
    ]
    assert sum(article.url == "https://example.com/0/0" for article in articles) == 1
    assert all(article.extra["market"] == "US" for article in articles)


def test_periodic_kr_market_source_uses_korean_queries(monkeypatch):
    _stub_stock_queries(monkeypatch, "KR")

    articles = sources.fetch_google_news_kr_stock_articles()

    assert len(articles) == 10
    assert all(article.extra["market"] == "KR" for article in articles)


def test_market_stock_query_tags_market_and_id_prefix(monkeypatch):
    def fake_rss(url, label, max_articles=None):
        assert label == "gnews-kr:0"
        assert "hl=ko&gl=KR" in url
        return [
            sources.GlobalArticle(
                article_id="rss:gnews-kr:0:abc",
                title="코스피 상승",
                content="본문",
                published_at="2026-07-18 10:00:00",
                url="https://example.com/kr",
            )
        ]

    monkeypatch.setattr(sources, "fetch_rss_articles", fake_rss)

    articles = sources._fetch_google_news_stock_query("KR", "코스피 when:1d", 0, 4)

    assert articles[0].article_id == "gnews-kr:rss:gnews-kr:0:abc"
    assert articles[0].extra == {"market": "KR", "provider": "google-news"}


def test_korean_queries_use_korean_locale():
    # 한국 기사를 영어 로케일로 질의하면 종목명이 빠진 요약만 돌아온다.
    assert "hl=ko&gl=KR&ceid=KR:ko" in sources._google_news_url("코스피", "KR")
    assert "hl=en-US&gl=US&ceid=US:en" in sources._google_news_url("US stocks", "US")
    assert all(
        any("가" <= ch <= "힣" for ch in query)
        for query in sources._MARKET_STOCK_NEWS_QUERIES["KR"]
    )
