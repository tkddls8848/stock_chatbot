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


def test_cls_adapter_returns_newest_first_with_split_timestamp(monkeypatch):
    # akshare는 财联社 전보를 발행 시각 오름차순으로, 날짜와 시각을 두 열로
    # 쪼개서 돌려준다. 뒤집지 않으면 feed_rank(신선도)가 거꾸로 매겨진다.
    import pandas as pd

    frame = pd.DataFrame(
        [
            {"标题": "오래된 전보", "内容": "본문 A", "发布日期": "2026-09-20", "发布时间": "09:00:00"},
            {"标题": "", "内容": "", "发布日期": "2026-09-20", "发布时间": "10:00:00"},
            {"标题": "최신 전보", "内容": "본문 B", "发布日期": "2026-09-20", "发布时间": "11:00:00"},
        ]
    )
    monkeypatch.setattr(sources, "fetch_cls_raw", lambda: frame)

    articles = sources.fetch_cls_articles()

    # 제목·본문이 모두 빈 행은 버린다.
    assert [article.title for article in articles] == ["최신 전보", "오래된 전보"]
    assert articles[0].published_at == "11:00:00"
    assert articles[0].published_date == "2026-09-20"
    assert articles[0].article_id.startswith("cls:2026-09-20 11:00:00:")


def test_em_adapter_reads_summary_column_and_single_timestamp(monkeypatch):
    # 东方财富는 본문을 "摘要"에 담고 날짜·시각을 "发布时间" 한 열에 준다.
    # cls 와 달리 published_date 를 따로 넘기지 않아야 한다.
    import pandas as pd

    frame = pd.DataFrame(
        [
            {"标题": "속보 A", "摘要": "본문 A", "发布时间": "2026-09-22 11:32:10", "链接": "https://x/1"},
            {"标题": "", "摘要": "", "发布时间": "2026-09-22 11:00:00", "链接": ""},
            {"标题": "속보 B", "摘要": "본문 B", "发布时间": "2026-09-22 10:00:00", "链接": "https://x/2"},
        ]
    )
    monkeypatch.setattr(sources, "fetch_em_raw", lambda: frame)

    articles = sources.fetch_em_articles()

    assert [article.title for article in articles] == ["속보 A", "속보 B"]
    assert articles[0].published_at == "2026-09-22 11:32:10"
    assert articles[0].published_date == ""
    assert articles[0].url == "https://x/1"
    assert articles[0].article_id.startswith("em_global:2026-09-22 11:32:10:")


def test_japan_market_uses_japanese_locale_and_stock_queries():
    # 영어 로케일로 받으면 종목명이 현지 표기로 남지 않아 사전선별의 종목
    # 매칭과 리서치 후보 발굴이 본문에서 이름을 찾지 못한다.
    assert "hl=ja&gl=JP&ceid=JP:ja" in sources._google_news_url("日経平均", "JP")
    assert "JP" in sources._MARKET_STOCK_NEWS_QUERIES
    assert len(sources._MARKET_STOCK_NEWS_QUERIES["JP"]) == 3


def test_cls_timestamp_parses_only_with_published_date():
    from telegram_bot.news.utils import parse_news_datetime

    # 시각만으로는 날짜를 알 수 없어 None이다. 어댑터가 published_date를 따로
    # 넘기는 이유가 이것이다.
    assert parse_news_datetime("11:00:00") is None
    assert parse_news_datetime("11:00:00", "2026-09-20") is not None


def test_hong_kong_market_uses_traditional_chinese_locale():
    # 보고서·감성 차트가 HK를 시장으로 갖는데 HK 기사를 내는 소스가 없었다.
    assert "HK" in sources._REGIONAL_MARKET_QUERIES
    assert "hl=zh-HK&gl=HK&ceid=HK:zh-Hant" in sources._google_news_url("恒指", "HK")


def test_regional_limit_is_divided_by_actual_market_count(monkeypatch):
    monkeypatch.setattr(sources, "NEWS_SOURCE_ARTICLE_LIMIT", _ARTICLE_LIMIT)
    captured = {}

    def fake_rss(url, label, max_articles=None):
        captured["limit"] = max_articles
        return []

    monkeypatch.setattr(sources, "fetch_rss_articles", fake_rss)
    sources._fetch_google_news_market("HK")

    market_count = len(sources._REGIONAL_MARKET_QUERIES)
    assert captured["limit"] == (_ARTICLE_LIMIT + market_count - 1) // market_count
