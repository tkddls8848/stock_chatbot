from datetime import timedelta

from shared.core.clock import now
from telegram_bot.news.registry import NewsSourceRegistry, build_source_specs


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


def test_eastmoney_news_provider_is_not_registered():
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
