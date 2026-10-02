import pytest

from services.telegram_bot.news import sources

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
    assert {key: articles[0].extra[key] for key in ("market", "provider", "publisher")} == \
        {"market": "KR", "provider": "google-news", "publisher": ""}


def test_korean_queries_use_korean_locale():
    # 한국 기사를 영어 로케일로 질의하면 종목명이 빠진 요약만 돌아온다.
    assert "hl=ko&gl=KR&ceid=KR:ko" in sources._google_news_url("코스피", "KR")
    assert "hl=en-US&gl=US&ceid=US:en" in sources._google_news_url("US stocks", "US")
    assert all(
        any("가" <= ch <= "힣" for ch in query)
        for query in sources._MARKET_STOCK_NEWS_QUERIES["KR"]
    )


def _rss(title, publisher, content=""):
    return sources.GlobalArticle(article_id="rss:t:1", title=title, content=content,
                                 published_at="", url="https://x/1", extra={"source": publisher})


@pytest.mark.parametrize(("title", "publisher", "expected"), [
    ("코스피 7000선 회복 - 연합뉴스", "연합뉴스", "코스피 7000선 회복"),
    # 매체명과 다른 꼬리는 제목의 일부일 수 있다. 건드리지 않는다.
    ("금리 정책 - 시행 전 점검", "연합뉴스", "금리 정책 - 시행 전 점검"),
    ("Fed holds - what it means - Reuters", "Reuters", "Fed holds - what it means"),
    ("코스피 7000 탈환 - 머니투데이 - 머니투데이", "머니투데이", "코스피 7000 탈환"),
    ("제목만 있다", "", "제목만 있다"),
    (" - 연합뉴스", "연합뉴스", " - 연합뉴스"),   # 떼고 나면 남는 제목이 없다
])
def test_google_titles_lose_only_the_exact_publisher_tail(title, publisher, expected):
    """매체명은 화면의 출처로 남기고 제목에서는 뗀다(운영자 결정 2026-10-02)."""
    article = sources._google_article(_rss(title, publisher), "gnews-kr:1", "KR")
    assert article.title == expected
    assert {key: article.extra[key] for key in ("market", "provider", "publisher")} == \
        {"market": "KR", "provider": "google-news", "publisher": publisher}
    # 사전선별의 사건 비교는 떼기 전 원문을 계속 쓴다(사건 메모리 연속성).
    assert article.extra["event_title"] == title


def test_google_summary_loses_the_trailing_publisher_too():
    # Google News 요약은 "제목  매체명"이다. 리서치가 이 요약을 모델에 넣는다.
    article = sources._google_article(
        _rss("코스피 마감 - 연합인포맥스", "연합인포맥스", "코스피 마감\xa0\xa0연합인포맥스"), "x", "KR")
    assert article.content == "코스피 마감"


def test_china_analysis_queries_use_simplified_chinese_but_the_mixed_query_stays_english(monkeypatch):
    urls = []
    monkeypatch.setattr(sources, "fetch_rss_articles", lambda url, label, max_articles=None: urls.append(url) or [])
    sources.fetch_google_news_cn_stock_articles()
    sources._fetch_google_news_market("CN")
    stock_urls, mixed_url = urls[:-1], urls[-1]
    assert len(stock_urls) == len(sources._MARKET_STOCK_NEWS_QUERIES["CN"])
    assert all("hl=zh-CN" in url for url in stock_urls)
    assert "hl=en-US" in mixed_url


def test_japan_market_uses_japanese_locale_and_stock_queries():
    # 영어 로케일로 받으면 종목명이 현지 표기로 남지 않아 사전선별의 종목
    # 매칭과 리서치 후보 발굴이 본문에서 이름을 찾지 못한다.
    assert "hl=ja&gl=JP&ceid=JP:ja" in sources._google_news_url("日経平均", "JP")
    assert "JP" in sources._MARKET_STOCK_NEWS_QUERIES
    assert len(sources._MARKET_STOCK_NEWS_QUERIES["JP"]) == 3


def test_europe_market_uses_british_english_locale_and_stock_queries():
    # 미국판 로케일은 같은 질의에도 월가 기사가 앞을 채운다.
    assert "hl=en-GB&gl=GB&ceid=GB:en" in sources._google_news_url("STOXX 600", "EU")
    assert len(sources._MARKET_STOCK_NEWS_QUERIES["EU"]) == 3


def test_cls_timestamp_parses_only_with_published_date():
    from services.telegram_bot.news.utils import parse_news_datetime

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
