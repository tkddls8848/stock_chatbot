from datetime import timedelta

from services.telegram_bot.core.clock import now
from services.telegram_bot.news.registry import NewsSourceRegistry, build_source_specs


def _registry(**kwargs) -> NewsSourceRegistry:
    specs = build_source_specs(["futu", "sina"], [])
    return NewsSourceRegistry(specs, **kwargs)


def test_build_source_specs_ignores_unknown_and_duplicates():
    specs = build_source_specs(["futu", "unknown", "futu", "sina"], [("내RSS", "http://x/feed")])
    keys = [spec.key for spec in specs]
    assert keys == ["futu", "sina", "rss:내RSS"]


def test_build_source_specs_supports_google_news_provider():
    specs = build_source_specs(["gnews", "gnews_us", "gnews_kr"], [])

    assert [spec.key for spec in specs] == ["gnews", "gnews_us", "gnews_kr"]


def test_builtin_specs_carry_market_tags():
    specs = {
        spec.key: spec
        for spec in build_source_specs(["futu", "gnews", "gnews_us", "gnews_kr"], [])
    }

    assert specs["futu"].market == "CN"
    assert specs["gnews_us"].market == "US"
    assert specs["gnews_kr"].market == "KR"
    # 혼합 소스는 시장 태그를 비워 두고 기사별 extra["market"]로 구분한다.
    assert specs["gnews"].market == ""


def test_source_markets_config_tags_rss_and_overrides_builtin():
    specs = {
        spec.key: spec
        for spec in build_source_specs(
            ["futu"],
            [("mk-stock", "http://x/feed")],
            {"rss:mk-stock": "KR", "futu": "HK"},
        )
    }

    assert specs["rss:mk-stock"].market == "KR"
    assert specs["futu"].market == "HK"


def test_eastmoney_per_stock_news_provider_is_not_registered():
    # 2026-07-19(54d1779)에 뺀 `em`은 종목별 검색 API(stock_news_em)다. 기사별
    # 뉴스 경로 자체가 그 뒤 삭제됐으므로 이 키는 계속 비어 있어야 한다.
    # 전역 속보(stock_info_global_em)는 엔드포인트가 달라 `em_global`로 따로
    # 등록돼 있다 — 아래 test_eastmoney_global_wire_is_registered 가 그쪽을 본다.
    assert build_source_specs(["em"], []) == []


def test_source_cooldown_after_consecutive_failures():
    registry = _registry(failure_threshold=3, cooldown_minutes=60)
    assert [s.key for s in registry.active_specs()] == ["futu", "sina"]

    registry.record_failure("futu", "boom")
    registry.record_failure("futu", "boom")
    assert [s.key for s in registry.active_specs()] == ["futu", "sina"]

    registry.record_failure("futu", "boom")
    assert [s.key for s in registry.active_specs()] == ["sina"]


def test_cooldown_expires_and_source_returns():
    registry = _registry(failure_threshold=1, cooldown_minutes=60)
    registry.record_failure("sina", "boom")
    assert [s.key for s in registry.active_specs()] == ["futu"]

    # 쿨다운 만료를 시뮬레이션
    registry._health["sina"].cooldown_until = now() - timedelta(seconds=1)
    assert [s.key for s in registry.active_specs()] == ["futu", "sina"]


def test_success_resets_failure_streak():
    registry = _registry(failure_threshold=3, cooldown_minutes=60)
    registry.record_failure("futu", "boom")
    registry.record_failure("futu", "boom")
    registry.record_success("futu")
    registry.record_failure("futu", "boom")
    registry.record_failure("futu", "boom")
    assert [s.key for s in registry.active_specs()] == ["futu", "sina"]


def test_status_lines_report_states():
    registry = _registry(failure_threshold=1, cooldown_minutes=60)
    registry.record_failure("sina", "boom")
    lines = registry.status_lines()
    assert lines[0] == "futu: 정상"
    assert lines[1].startswith("sina: 쿨다운")


def test_cailianpress_is_registered_as_a_share_source():
    specs = {spec.key: spec for spec in build_source_specs(["cls"], [])}

    assert specs["cls"].market == "CN"
    assert "财联社" in specs["cls"].label


def test_eastmoney_global_wire_is_registered_as_a_share_source():
    specs = {spec.key: spec for spec in build_source_specs(["em_global"], [])}

    assert specs["em_global"].market == "CN"
    assert "东方财富" in specs["em_global"].label


def test_japan_stock_source_is_registered_with_its_own_market():
    # gnews 하나가 일곱 시장을 덮어 JP 몫이 큐에서 두 건 남짓이었다. 전용
    # 소스라야 per_source_limit 슬롯을 따로 받는다.
    specs = {spec.key: spec for spec in build_source_specs(["gnews_jp"], [])}

    assert specs["gnews_jp"].market == "JP"


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
