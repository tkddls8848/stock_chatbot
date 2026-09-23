import asyncio
from types import SimpleNamespace

import pytest

from services.telegram_bot.core import access
import services.telegram_bot.handlers.commands as commands
from services.telegram_bot.core.menu_status import set_menu_button_text
from services.telegram_bot.features import ALL_FEATURES, build_feature_registry
from services.telegram_bot.handlers.navigation import (
    handle_menu_callback,
    handle_menu_text,
    research_menu,
)
from services.telegram_bot.handlers.commands import configure_telegram_menu
from services.telegram_bot.watchlist.handlers import cmd_menu, handle_watchlist_callback
from services.telegram_bot.watchlist.keyboards import build_list_keyboard


def _registry():
    return build_feature_registry(feature.key for feature in ALL_FEATURES)


def _update(chat_id):
    chat = SimpleNamespace(id=chat_id) if chat_id is not None else None
    return SimpleNamespace(effective_chat=chat)


def test_empty_allowlist_blocks_everyone(monkeypatch):
    monkeypatch.setattr(access, "ALLOWED_CHAT_IDS", frozenset())
    assert access.is_allowed_update(_update(12345)) is False


def test_config_refuses_to_start_without_a_usable_allowlist(monkeypatch):
    from services.telegram_bot.core import config

    for raw in ("", "   ", ",,", "abc", "abc, 12x"):
        monkeypatch.setenv("ALLOWED_CHAT_IDS", raw)
        with pytest.raises(config.ConfigurationError, match="ALLOWED_CHAT_IDS"):
            config._parse_allowed_chat_ids()


def test_config_keeps_valid_ids_and_drops_invalid_ones(monkeypatch):
    from services.telegram_bot.core import config

    monkeypatch.setenv("ALLOWED_CHAT_IDS", " 111 , oops , -222 ")
    assert config._parse_allowed_chat_ids() == frozenset({111, -222})


def test_allowlist_blocks_other_chats(monkeypatch):
    monkeypatch.setattr(access, "ALLOWED_CHAT_IDS", frozenset({111}))
    assert access.is_allowed_update(_update(111)) is True
    assert access.is_allowed_update(_update(222)) is False
    assert access.is_allowed_update(_update(None)) is False


def test_restricted_decorator_skips_disallowed(monkeypatch):
    monkeypatch.setattr(access, "ALLOWED_CHAT_IDS", frozenset({111}))
    calls = []

    @access.restricted
    async def handler(update, context):
        calls.append(update.effective_chat.id)

    asyncio.run(handler(_update(111), None))
    asyncio.run(handler(_update(222), None))
    assert calls == [111]


def test_restricted_decorator_updates_request_status(monkeypatch):
    monkeypatch.setattr(access, "ALLOWED_CHAT_IDS", frozenset({123}))
    edits = []

    class StatusMessage:
        async def edit_text(self, text):
            edits.append(text)

    class RequestMessage:
        async def reply_text(self, text):
            assert text == "⏳ 요청 작업 처리 중..."
            return StatusMessage()

    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=123),
        effective_message=RequestMessage(),
    )

    @access.restricted
    async def handler(update, context):
        return None

    async def scenario():
        await handler(update, SimpleNamespace(bot_data={}))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert edits == ["✅ 요청 작업 처리 완료"]


def test_menu_jobs_suppress_chat_status(monkeypatch):
    monkeypatch.setattr(access, "ALLOWED_CHAT_IDS", frozenset({123}))
    replies = []

    class RequestMessage:
        async def reply_text(self, text):
            replies.append(text)

    @access.restricted
    async def handler(update, context):
        return None

    for callback_data in (
        "nav:research:run",
        "nav:market:sentiment:7",
        "nav:briefing",
        "nav:market:sentiment:30",
    ):
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123),
            effective_message=RequestMessage(),
            callback_query=SimpleNamespace(data=callback_data),
        )
        asyncio.run(handler(update, SimpleNamespace(bot_data={})))
    assert replies == []


def test_menu_status_changes_only_target_button():
    class Message:
        def __init__(self):
            self.reply_markup = research_menu()

        async def edit_reply_markup(self, reply_markup):
            self.reply_markup = reply_markup

    message = Message()
    asyncio.run(
        set_menu_button_text(
            message,
            "nav:research:run",
            "◑ AI 분석 중",
        )
    )

    buttons = {
        button.callback_data: button.text
        for row in message.reply_markup.inline_keyboard
        for button in row
    }
    assert buttons["nav:research:run"] == "◑ AI 분석 중"
    assert buttons["nav:research:show"] == "주제 보기"


def test_home_text_refreshes_persistent_menu_before_inline_home():
    class Message:
        text = "🏠 홈"

        def __init__(self):
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append((text, kwargs["reply_markup"]))

    message = Message()
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(
        user_data={},
        bot_data={"feature_registry": _registry()},
        application=None,
    )

    asyncio.run(handle_menu_text(update, context))

    assert len(message.replies) == 2
    assert "하단 메뉴" in message.replies[0][0]
    labels = {
        button.text
        for row in message.replies[0][1].keyboard
        for button in row
    }
    assert "📈 성과" not in labels
    assert message.replies[1][1].inline_keyboard


def test_persistent_market_button_opens_the_markets_hub_not_the_admin_menu():
    """회귀: 이름 없는 persistent 버튼은 예전 코드에서 조용히 '⚙️ 관리' 화면으로
    떨어졌다. 감성·이상을 묶은 '📊 시장' 허브 버튼이 그 분기를 다시 밟으면
    안 된다."""

    class Message:
        def __init__(self, text):
            self.text = text
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append((text, kwargs.get("reply_markup")))

    registry = _registry()
    message = Message("📊 시장")
    update = SimpleNamespace(effective_message=message)
    context = SimpleNamespace(
        user_data={},
        bot_data={"feature_registry": registry},
        application=None,
    )

    asyncio.run(handle_menu_text(update, context))

    assert message.replies, "📊 시장 produced no reply"
    text, markup = message.replies[-1]
    assert "<b>관리</b>" not in text
    assert "<b>국가별 뉴스 감성</b>" in text
    buttons = {button.callback_data for row in markup.inline_keyboard for button in row}
    assert {"nav:market:sentiment:7", "nav:market:sentiment:30"} <= buttons
    # 폴리마켓은 공개 웹으로 철수했다. 텔레그램 허브로 되살아나면 안 된다.
    assert not any("polymarket" in str(data) for data in buttons)


def test_every_persistent_label_is_wired_in_handle_menu_text(monkeypatch):
    """회귀(일반화): `MenuSpec.persistent_label`을 새로 추가하고 `handle_menu_text`의
    `elif` 나열에 분기를 잊으면, 예전엔 조용히 '⚙️ 관리' 화면으로, 지금은 조용히
    홈 화면으로 떨어진다 — 둘 다 눈에 안 띈다. 무거운 의존(watchlist_manager,
    브리핑 발송 파이프라인 등)이 필요한 라벨은 그 진입점 함수만 스텁으로 바꿔
    "제대로 된 분기로 갔는가"만 보고, 나머지는 실제 응답 텍스트가 catch-all
    홈 응답과 다른지로 검증한다."""
    from services.telegram_bot.handlers import navigation

    called = {}

    async def fake_cmd_menu(update, context):
        called["watch"] = True

    async def fake_cmd_briefing(update, context):
        called["briefing"] = True

    monkeypatch.setattr(navigation, "cmd_menu", fake_cmd_menu)
    monkeypatch.setattr(navigation, "cmd_briefing", fake_cmd_briefing)

    class Message:
        def __init__(self, text):
            self.text = text
            self.replies = []

        async def reply_text(self, text, **kwargs):
            self.replies.append((text, kwargs.get("reply_markup")))

    registry = _registry()
    _FALLBACK_TEXT = "<b>주식 뉴스 봇</b>\n원하는 기능을 선택하세요."
    _STUBBED_ACTIONS = {"watch": "watch", "briefing": "briefing"}
    labels = {
        item.persistent_label: item.callback_data
        for item in registry.menu_specs()
        if item.persistent_label
    }
    assert labels, "no persistent labels registered — test itself is broken"

    for label, callback_data in labels.items():
        action = callback_data.removeprefix("nav:")
        message = Message(label)
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(
            user_data={},
            bot_data={"feature_registry": registry},
            application=None,
        )

        asyncio.run(handle_menu_text(update, context))

        if action in _STUBBED_ACTIONS:
            assert called.get(_STUBBED_ACTIONS[action]), (
                f"{label} did not reach its stubbed command handler"
            )
            continue

        assert message.replies, f"{label} produced no reply"
        text, _markup = message.replies[-1]
        assert text != _FALLBACK_TEXT, (
            f"{label} fell through handle_menu_text's catch-all — "
            "it needs its own elif branch"
        )


def test_bot_startup_pushes_latest_persistent_menu(monkeypatch):
    sent = []

    class Bot:
        async def set_my_commands(self, commands):
            self.commands = commands

        async def set_chat_menu_button(self, menu_button):
            self.menu_button = menu_button

        async def send_message(self, **kwargs):
            sent.append(kwargs)

    registry = _registry()
    app = SimpleNamespace(
        bot=Bot(),
        bot_data={"feature_registry": registry},
    )
    monkeypatch.setattr("services.telegram_bot.handlers.commands.TELEGRAM_CHAT_ID", "chat")

    asyncio.run(configure_telegram_menu(app))

    assert sent[0]["chat_id"] == "chat"
    labels = {
        button.text
        for row in sent[0]["reply_markup"].keyboard
        for button in row
    }
    assert "📈 성과" not in labels


def test_callback_timeout_does_not_abort_button_action(monkeypatch, caplog):
    from telegram.error import TimedOut

    action_handled = False

    class Query:
        data = "test_action"

        async def answer(self):
            raise TimedOut

    async def handle_menu(_update, _context, data):
        nonlocal action_handled
        action_handled = data == "test_action"
        return True

    monkeypatch.setattr(commands, "handle_menu_callback", handle_menu)
    update = SimpleNamespace(callback_query=Query())
    context = SimpleNamespace(bot_data={})

    with caplog.at_level("WARNING"):
        asyncio.run(commands.callback_handler(update, context))

    assert action_handled
    assert "동작은 계속" in caplog.text


def test_menu_command_opens_delete_list_directly():
    class Watchlist:
        async def get_all(self):
            return {"600519": "귀주모태주"}

    class Message:
        async def reply_text(self, text, **kwargs):
            self.text = text
            self.reply_markup = kwargs["reply_markup"]

    message = Message()
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(bot_data={"watchlist_manager": Watchlist()})
    asyncio.run(cmd_menu(update, context))

    buttons = {
        button.callback_data: button.text
        for row in message.reply_markup.inline_keyboard
        for button in row
    }
    assert message.text.startswith("<b>관심종목 관리</b>")
    assert buttons["remove:600519"] == "삭제: 귀주모태주 (600519)"
    assert buttons["add_stock"] == "종목추가"


def test_navigation_watch_opens_delete_list_directly():
    class Watchlist:
        async def get_all(self):
            return {"600519": "귀주모태주"}

    class Message:
        async def edit_text(self, text, **kwargs):
            self.text = text
            self.reply_markup = kwargs["reply_markup"]

    message = Message()
    update = SimpleNamespace(
        effective_message=message,
        callback_query=SimpleNamespace(message=message),
    )
    context = SimpleNamespace(
        user_data={},
        bot_data={
            "feature_registry": _registry(),
            "watchlist_manager": Watchlist(),
        },
        application=None,
    )
    handled = asyncio.run(
        handle_menu_callback(update, context, "nav:watch")
    )
    buttons = {
        button.callback_data: button.text
        for row in message.reply_markup.inline_keyboard
        for button in row
    }
    assert handled is True
    assert message.text.startswith("<b>관심종목 관리</b>")
    assert buttons["remove:600519"] == "삭제: 귀주모태주 (600519)"
    assert buttons["add_stock"] == "종목추가"


def test_watchlist_add_button_opens_market_selector_directly():
    keyboard = build_list_keyboard({"600519": "귀주모태주"})
    add_button = next(
        button
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data == "add_stock"
    )
    assert add_button.text == "종목추가"

    class Message:
        async def edit_text(self, text, **kwargs):
            self.text = text
            self.reply_markup = kwargs["reply_markup"]

    message = Message()
    context = SimpleNamespace(
        user_data={"menu_input": "add_stock", "add_market": "CN:SH"},
    )
    handled = asyncio.run(
        handle_watchlist_callback(
            SimpleNamespace(message=message),
            context,
            "add_stock",
        )
    )
    callbacks = {
        button.callback_data
        for row in message.reply_markup.inline_keyboard
        for button in row
    }
    assert handled is True
    assert message.text.startswith("<b>종목추가</b>")
    assert "add_market:CN:SH" in callbacks
    assert context.user_data == {}


def test_inline_briefing_button_runs_time_aware_briefing_directly(monkeypatch):
    from services.telegram_bot.handlers import navigation

    seen = []

    async def briefing(_update, context):
        seen.append(context.args)

    monkeypatch.setattr(navigation, "cmd_briefing", briefing)
    message = SimpleNamespace()
    update = SimpleNamespace(
        effective_message=message,
        callback_query=SimpleNamespace(message=message),
    )
    context = SimpleNamespace(
        user_data={},
        bot_data={"feature_registry": _registry()},
        application=object(),
    )

    handled = asyncio.run(handle_menu_callback(update, context, "nav:briefing"))

    assert handled is True
    assert seen == [[]]


def test_persistent_briefing_button_runs_time_aware_briefing_directly(monkeypatch):
    from services.telegram_bot.handlers import navigation

    seen = []

    async def briefing(_update, context):
        seen.append(context.args)

    monkeypatch.setattr(navigation, "cmd_briefing", briefing)
    message = SimpleNamespace(text="📰 브리핑")
    update = SimpleNamespace(effective_message=message, callback_query=None)
    context = SimpleNamespace(
        user_data={},
        bot_data={"feature_registry": _registry()},
        application=object(),
    )

    asyncio.run(handle_menu_text(update, context))

    assert seen == [[]]
