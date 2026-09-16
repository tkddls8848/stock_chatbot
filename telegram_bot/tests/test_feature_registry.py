from dataclasses import replace
from types import SimpleNamespace

import pytest

from telegram_bot.features import ALL_FEATURES, build_feature_registry
from telegram_bot.features.registry import FeatureConfigurationError, FeatureRegistry
from telegram_bot.handlers.navigation import main_menu, persistent_menu


EXPECTED_FEATURES = {
    "instruments",
    "quant",
    "watchlist",
    "news_prefilter",
    "news",
    "market_sentiment",
    "research",
    "briefing",
    "system_admin",
    "web_admin",
}


def test_feature_catalog_declares_every_supported_capability():
    assert {feature.key for feature in ALL_FEATURES} == EXPECTED_FEATURES
    assert "em" not in {feature.key for feature in ALL_FEATURES}


def test_full_feature_set_has_unique_commands_and_valid_dependencies():
    registry = build_feature_registry(EXPECTED_FEATURES)

    command_names = [
        command.command
        for command in registry.telegram_commands()
    ]

    assert len(command_names) == len(set(command_names))
    assert {"market", "research", "briefing", "system"} <= set(
        command_names
    )
    assert "score" not in command_names


def test_service_installation_preserves_catalog_order():
    installed = []

    def installer(spec):
        def install(app):
            installed.append(spec.key)

        return install

    specs = tuple(
        replace(spec, install_services=installer(spec))
        for spec in ALL_FEATURES
    )
    registry = FeatureRegistry(specs, EXPECTED_FEATURES)
    app = SimpleNamespace(bot_data={})

    registry.install_services(app)

    assert installed == [spec.key for spec in ALL_FEATURES]


def test_registry_rejects_unknown_feature():
    with pytest.raises(FeatureConfigurationError, match="알 수 없는 기능"):
        build_feature_registry({"unknown"})


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ({"watchlist"}, "watchlist → instruments"),
        ({"research", "instruments"}, "research → news"),
        ({"news"}, "news → watchlist"),
        ({"market_sentiment"}, "market_sentiment → news"),
        ({"briefing"}, "briefing → news"),
        ({"quant"}, "quant → instruments"),
        ({"news_prefilter"}, "news_prefilter → instruments"),
    ],
)
def test_registry_rejects_unsatisfied_dependencies(enabled, expected):
    with pytest.raises(FeatureConfigurationError, match="의존 기능이 비활성"):
        build_feature_registry(enabled)

    with pytest.raises(FeatureConfigurationError, match=expected):
        build_feature_registry(enabled)


def test_every_enabled_subset_that_passes_can_install_services(monkeypatch):
    """검증을 통과한 조합은 서비스 설치가 KeyError 없이 끝나야 한다."""
    # 기능 조립 검증에서 실제 종목 DB 다운로드를 시작하지 않는다.
    monkeypatch.setattr("telegram_bot.stocks.StockDatabase.load_or_build", lambda self: None)
    closures = [
        {"instruments"},
        {"instruments", "quant"},
        {"instruments", "watchlist"},
        {"instruments", "watchlist", "news_prefilter"},
        {"instruments", "watchlist", "news"},
        {"instruments", "watchlist", "news", "market_sentiment"},
        {"instruments", "watchlist", "news", "quant", "research"},
        {"system_admin"},
        {"web_admin"},
        EXPECTED_FEATURES,
    ]
    for enabled in closures:
        registry = build_feature_registry(enabled)
        registry.install_services(SimpleNamespace(bot_data={}))


def test_default_feature_set_satisfies_its_own_dependencies():
    from telegram_bot.core.config import FEATURES_ENABLED

    build_feature_registry(FEATURES_ENABLED)


def test_disabled_features_are_removed_from_both_menus():
    enabled = frozenset({"instruments", "system_admin"})
    registry = build_feature_registry(enabled)

    inline_labels = {
        button.text
        for row in main_menu(registry).inline_keyboard
        for button in row
    }
    persistent_labels = {
        button.text
        for row in persistent_menu(registry).keyboard
        for button in row
    }

    assert inline_labels == {"🗂 종목 DB 갱신", "❔ 도움말", "⚙️ 시스템"}
    assert persistent_labels == {"🏠 홈", "⚙️ 관리"}


def test_registry_resolves_menu_ownership_and_persistent_labels():
    registry = build_feature_registry(EXPECTED_FEATURES)

    assert registry.menu_owner("nav:market") == "market_sentiment"
    assert registry.menu_owner("nav:market:sentiment") == "market_sentiment"
    assert registry.menu_owner("nav:market:sentiment:30") == "market_sentiment"
    assert registry.menu_owner("nav:market:sentiment:14") == "market_sentiment"
    assert registry.menu_owner("nav:marketplace") is None
    assert registry.persistent_callback("📊 시장") == "nav:market"
    assert registry.persistent_callback("없는 메뉴") is None


def test_catalog_reports_enabled_and_disabled_states():
    registry = build_feature_registry({"instruments", "system_admin"})
    lines = registry.catalog_lines()

    assert any(
        line.startswith("• <b>instruments</b>") and "✅ 활성" in line for line in lines
    )
    assert any(
        line.startswith("• <b>news</b>") and "⛔ 비활성" in line for line in lines
    )


def test_disabled_features_do_not_initialize_their_services():
    registry = build_feature_registry({"system_admin"})
    app = SimpleNamespace(bot_data={})

    registry.install_services(app)

    assert "stock_db" not in app.bot_data
    assert "translator" not in app.bot_data
    assert "market_view_analyzer" not in app.bot_data


def test_generated_help_contains_only_enabled_commands():
    registry = build_feature_registry({"system_admin"})

    text = registry.help_text()

    assert "/system" in text
    assert "/help" in text
    assert "/research" not in text
    assert "/market" not in text


def test_generated_help_includes_command_usage_and_code_examples():
    registry = build_feature_registry(EXPECTED_FEATURES)

    text = registry.help_text()

    assert "/market [일수]" in text
    assert "/research show|set|run|clear" in text
    assert "/briefing [morning|intraday|evening]" in text
    assert "/score" not in text
    assert "KR:KOSPI:005930" in text


def test_persistent_menu_matches_current_primary_workflows():
    registry = build_feature_registry(EXPECTED_FEATURES)

    rows = [[button.text for button in row] for row in persistent_menu(registry).keyboard]

    assert rows == [
        ["🏠 홈"],
        ["⭐ 관심종목", "📊 시장"],
        ["🔎 리서치", "📰 브리핑"],
        ["⚙️ 관리"],
    ]
