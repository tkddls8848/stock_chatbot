"""FeatureRegistry의 CallbackSpec 기반 콜백 디스패치 검증."""
import asyncio
from types import SimpleNamespace

from shared.core.config import VIEW_LOOKBACK_DAYS
from telegram_bot.features import ALL_FEATURES, build_feature_registry
from telegram_bot.features.base import CallbackSpec, FeatureSpec
from telegram_bot.features.registry import FeatureRegistry


class _Query:
    def __init__(self):
        self.edited: list[str] = []

    async def edit_message_text(self, text: str):
        self.edited.append(text)


def _registry(enabled: set[str], handled: list[str]) -> FeatureRegistry:
    async def alpha_handler(query, context, data: str) -> bool:
        handled.append(f"alpha:{data}")
        return True

    async def beta_passthrough(query, context, data: str) -> bool:
        # 접두사는 겹치지만 처리하지 않는 핸들러(다음 후보로 넘어가야 함)
        handled.append(f"beta-skip:{data}")
        return False

    async def gamma_handler(query, context, data: str) -> bool:
        handled.append(f"gamma:{data}")
        return True

    specs = [
        FeatureSpec(
            key="beta",
            label="베타",
            callbacks=(CallbackSpec(("shared:",), beta_passthrough),),
        ),
        FeatureSpec(
            key="alpha",
            label="알파",
            callbacks=(CallbackSpec(("alpha:", "shared:"), alpha_handler),),
        ),
        FeatureSpec(
            key="gamma",
            label="감마",
            callbacks=(CallbackSpec(("gamma:",), gamma_handler),),
        ),
    ]
    return FeatureRegistry(specs, enabled)


def test_dispatch_routes_by_prefix():
    handled: list[str] = []
    registry = _registry({"alpha", "beta", "gamma"}, handled)
    query = _Query()
    assert asyncio.run(registry.dispatch_callback(query, None, "alpha:run")) is True
    assert handled == ["alpha:alpha:run"]
    assert query.edited == []


def test_dispatch_falls_through_when_handler_declines():
    handled: list[str] = []
    registry = _registry({"alpha", "beta", "gamma"}, handled)
    assert asyncio.run(registry.dispatch_callback(_Query(), None, "shared:x")) is True
    # beta가 먼저 시도하고 거절하면 alpha가 이어받는다.
    assert handled == ["beta-skip:shared:x", "alpha:shared:x"]


def test_dispatch_replies_disabled_notice_for_inactive_feature():
    handled: list[str] = []
    registry = _registry({"alpha", "beta"}, handled)
    query = _Query()
    assert asyncio.run(registry.dispatch_callback(query, None, "gamma:go")) is True
    assert handled == []
    assert query.edited == ["감마 기능이 비활성화되어 있습니다."]


def test_dispatch_returns_false_for_unknown_data():
    handled: list[str] = []
    registry = _registry({"alpha", "beta", "gamma"}, handled)
    assert asyncio.run(registry.dispatch_callback(_Query(), None, "nav:home")) is False
    assert handled == []


def test_signal_view_callback_uses_the_selected_stock_code():
    class MessageStub:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text: str, **kwargs):
            self.replies.append((text, kwargs))

    class PredictionLogStub:
        async def snapshot(self, _days):
            return []

    class WatchlistStub:
        async def get_all(self):
            return {"600519": "귀주모태주"}

    message = MessageStub()
    query = SimpleNamespace(message=message)
    context = SimpleNamespace(
        bot_data={
            "prediction_log": PredictionLogStub(),
            "watchlist_manager": WatchlistStub(),
            "stock_db": SimpleNamespace(get_display_name=lambda _code: None),
        }
    )
    registry = build_feature_registry(feature.key for feature in ALL_FEATURES)

    handled = asyncio.run(
        registry.dispatch_callback(query, context, "view:600519")
    )

    assert handled is True
    assert message.replies == [
        (
            f"귀주모태주 (600519): 최근 {VIEW_LOOKBACK_DAYS}일 감성 신호가 없습니다.",
            {},
        )
    ]
