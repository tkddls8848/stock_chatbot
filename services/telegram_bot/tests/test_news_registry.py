from datetime import timedelta

from services.telegram_bot.core.clock import now
from services.telegram_bot.news.registry import NewsSourceRegistry, build_source_specs


def _registry(**kwargs) -> NewsSourceRegistry:
    specs = build_source_specs(["gnews_us", "gnews_kr"], [])
    return NewsSourceRegistry(specs, **kwargs)


def test_build_source_specs_ignores_unknown_and_duplicates():
    specs = build_source_specs(["gnews_us", "unknown", "gnews_us", "gnews_kr"], [("내RSS", "http://x/feed")])
    keys = [spec.key for spec in specs]
    assert keys == ["gnews_us", "gnews_kr", "rss:내RSS"]


def test_build_source_specs_supports_google_news_provider():
    specs = build_source_specs(["gnews", "gnews_us", "gnews_kr"], [])

    assert [spec.key for spec in specs] == ["gnews", "gnews_us", "gnews_kr"]


def test_builtin_specs_carry_market_tags():
    specs = {
        spec.key: spec
        for spec in build_source_specs(["gnews_us", "gnews", "gnews_us", "gnews_kr"], [])
    }

    assert specs["gnews_us"].market == "US"
    assert specs["gnews_kr"].market == "KR"
    # 혼합 소스는 시장 태그를 비워 두고 기사별 extra["market"]로 구분한다.
    assert specs["gnews"].market == ""


def test_source_markets_config_tags_rss_and_overrides_builtin():
    specs = {
        spec.key: spec
        for spec in build_source_specs(
            ["gnews_us"],
            [("mk-stock", "http://x/feed")],
            {"rss:mk-stock": "KR", "gnews_us": "HK"},
        )
    }

    assert specs["rss:mk-stock"].market == "KR"
    assert specs["gnews_us"].market == "HK"


def test_eastmoney_per_stock_news_provider_is_not_registered():
    # 2026-07-19(54d1779)에 뺀 `em`은 종목별 검색 API(stock_news_em)다. 기사별
    # 뉴스 경로 자체가 그 뒤 삭제됐으므로 이 키는 계속 비어 있어야 한다.
    assert build_source_specs(["em"], []) == []


def test_source_cooldown_after_consecutive_failures():
    registry = _registry(failure_threshold=3, cooldown_minutes=60)
    assert [s.key for s in registry.active_specs()] == ["gnews_us", "gnews_kr"]

    registry.record_failure("gnews_us", "boom")
    registry.record_failure("gnews_us", "boom")
    assert [s.key for s in registry.active_specs()] == ["gnews_us", "gnews_kr"]

    registry.record_failure("gnews_us", "boom")
    assert [s.key for s in registry.active_specs()] == ["gnews_kr"]


def test_cooldown_expires_and_source_returns():
    registry = _registry(failure_threshold=1, cooldown_minutes=60)
    registry.record_failure("gnews_kr", "boom")
    assert [s.key for s in registry.active_specs()] == ["gnews_us"]

    # 쿨다운 만료를 시뮬레이션
    registry._health["gnews_kr"].cooldown_until = now() - timedelta(seconds=1)
    assert [s.key for s in registry.active_specs()] == ["gnews_us", "gnews_kr"]


def test_success_resets_failure_streak():
    registry = _registry(failure_threshold=3, cooldown_minutes=60)
    registry.record_failure("gnews_us", "boom")
    registry.record_failure("gnews_us", "boom")
    registry.record_success("gnews_us")
    registry.record_failure("gnews_us", "boom")
    registry.record_failure("gnews_us", "boom")
    assert [s.key for s in registry.active_specs()] == ["gnews_us", "gnews_kr"]


def test_status_lines_report_states():
    registry = _registry(failure_threshold=1, cooldown_minutes=60)
    registry.record_failure("gnews_kr", "boom")
    lines = registry.status_lines()
    assert lines[0] == "gnews_us: 정상"
    assert lines[1].startswith("gnews_kr: 쿨다운")


def test_flash_wire_sources_are_retired():
    """속보(7×24 快讯) 소스는 쓰지 않는다(운영자 결정 2026-10-02). 키가 남아 있으면
    설정 한 줄로 되살아나 보고서가 다시 한 줄짜리 속보를 국면 분석의 재료로 쓴다."""
    from services.telegram_bot.core.config import NEWS_GLOBAL_SOURCE_KEYS, NEWS_SOURCE_MARKETS

    assert build_source_specs(["futu", "em_global", "sina", "cls"], []) == []
    assert not {"futu", "em_global", "sina", "cls"} & set(NEWS_GLOBAL_SOURCE_KEYS)
    assert not {"futu", "em_global", "sina", "cls"} & set(NEWS_SOURCE_MARKETS)


def test_china_analysis_source_is_registered_with_its_own_market():
    specs = {spec.key: spec for spec in build_source_specs(["gnews_cn"], [])}

    assert specs["gnews_cn"].market == "CN"


def test_japan_stock_source_is_registered_with_its_own_market():
    # gnews 하나가 일곱 시장을 덮어 JP 몫이 큐에서 두 건 남짓이었다. 전용
    # 소스라야 per_source_limit 슬롯을 따로 받는다.
    specs = {spec.key: spec for spec in build_source_specs(["gnews_jp"], [])}

    assert specs["gnews_jp"].market == "JP"


def test_europe_sources_are_registered_with_their_own_market():
    from services.telegram_bot.core.config import NEWS_SOURCE_MARKETS

    # gnews 혼합 질의 하나로는 EU 몫이 큐에서 두 건 남짓이라 발행 게이트에 걸렸다.
    specs = {
        spec.key: spec
        for spec in build_source_specs(
            ["gnews_eu"],
            [("ecb-press", "https://example.test/ecb.xml")],
            NEWS_SOURCE_MARKETS,
        )
    }

    assert specs["gnews_eu"].market == "EU"
    assert specs["rss:ecb-press"].market == "EU"


def test_added_primary_and_broadcast_feeds_carry_their_market():
    from services.telegram_bot.core.config import NEWS_SOURCE_MARKETS

    feeds = [(label, f"https://example.test/{label}.xml") for label in
             ("cnbc-finance", "boj-whatsnew", "nhk-economy", "rthk-finance")]
    specs = {spec.key: spec.market for spec in build_source_specs([], feeds, NEWS_SOURCE_MARKETS)}

    assert specs == {
        "rss:cnbc-finance": "US",
        "rss:boj-whatsnew": "JP",
        "rss:nhk-economy": "JP",
        "rss:rthk-finance": "HK",
    }


def test_configured_sources_and_feeds_all_resolve():
    # 설정에 적힌 키가 빌트인에 없으면 조용히 무시된다(경고만 남는다).
    # 소스를 추가하고 등록을 빠뜨리면 이 테스트가 잡는다.
    from services.telegram_bot.core.config import (
        NEWS_GLOBAL_SOURCE_KEYS,
        NEWS_RSS_FEEDS,
        NEWS_SOURCE_MARKETS,
    )

    specs = build_source_specs(NEWS_GLOBAL_SOURCE_KEYS, NEWS_RSS_FEEDS, NEWS_SOURCE_MARKETS)

    assert len(specs) == len(NEWS_GLOBAL_SOURCE_KEYS) + len(NEWS_RSS_FEEDS)
    # 혼합 소스(gnews)만 시장 태그가 비어 있다. 나머지는 보고서가 시장별로
    # 묶을 수 있도록 태그를 갖는다.
    untagged = {spec.key for spec in specs if not spec.market}
    assert untagged == {"gnews"}
