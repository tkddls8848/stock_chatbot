"""명령어 입력 없이 사용하는 텔레그램 인라인 메뉴."""

from types import SimpleNamespace

from telegram import (
    Message,
    Update,
)
from telegram.ext import ContextTypes

from services.telegram_bot.briefing.service import cmd_briefing
from services.telegram_bot.features.instruments.handlers import cmd_stockdb
from services.telegram_bot.features.market_sentiment.handlers import cmd_market
from services.telegram_bot.features.system_admin.handlers import cmd_system
from services.telegram_bot.features.web_status.handlers import cmd_web
from services.telegram_bot.handlers.menus import (
    _back,
    _keyboard,
    admin_menu,
    main_menu,
    persistent_menu,
    refresh_persistent_menu,
    research_menu,
    web_admin_menu,
)
from services.telegram_bot.research.handlers import cmd_research


def _context(context: ContextTypes.DEFAULT_TYPE, args: list[str]):
    # user_data를 그대로 전달해야 프록시로 호출되는 핸들러가 menu_input 같은
    # 대화 상태에 접근할 수 있다(SimpleNamespace에는 기본으로 없음).
    return SimpleNamespace(
        bot_data=context.bot_data,
        user_data=context.user_data,
        application=context.application,
        args=args,
    )


async def _dispatch_primary_menu_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    message: Message,
    *,
    edit_message: bool,
) -> bool:
    """인라인·하단 고정 메뉴가 공유하는 최상위 기능 진입점.

    /start와 하단 메뉴는 같은 관리 허브를 연다.
    """
    if action == "market":
        # 관리 패널: 누르면 바로 갱신해 웹에 굽는다. 차트는 웹에서 본다.
        await cmd_market(update, _context(context, []))
        return True
    if action == "web":
        send = message.edit_text if edit_message else message.reply_text
        await send(
            "<b>🛠 웹 관리</b>\n리서치·시장 감성은 예약으로 돕니다. 여기서는 지금 실행과 상태 확인만 합니다.",
            parse_mode="HTML",
            reply_markup=web_admin_menu(context.bot_data["feature_registry"]),
        )
        return True
    if action == "research":
        send = message.edit_text if edit_message else message.reply_text
        await send(
            "<b>🔎 리서치 관리</b>\n결과·근거는 웹 /research 에서 봅니다.",
            parse_mode="HTML",
            reply_markup=research_menu(),
        )
        return True
    if action == "briefing":
        await cmd_briefing(update, _context(context, []))
        return True
    if action == "system":
        send = message.edit_text if edit_message else message.reply_text
        await send(
            "<b>⚙️ 관리</b>", parse_mode="HTML",
            reply_markup=admin_menu(context.bot_data["feature_registry"]),
        )
        return True
    return False


async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    if not data.startswith("nav:"):
        return False

    query = update.callback_query
    message = query.message
    action = data.removeprefix("nav:")
    registry = context.bot_data["feature_registry"]
    required_feature = registry.menu_owner(data)
    if required_feature is not None and not registry.is_enabled(required_feature):
        await message.edit_text(
            "이 기능은 현재 비활성화되어 있습니다.",
            reply_markup=main_menu(registry),
        )
        return True
    if action == "home":
        await refresh_persistent_menu(message, registry)
        await message.edit_text(
            "<b>주식 뉴스 봇</b>\n원하는 기능을 선택하세요.",
            parse_mode="HTML",
            reply_markup=main_menu(registry),
        )
        return True
    if await _dispatch_primary_menu_action(
        update,
        context,
        action,
        message,
        edit_message=True,
    ):
        return True
    if action == "web:status":
        await cmd_web(update, _context(context, []))
    elif action.startswith("research:"):
        command = action.split(":", 1)[1]
        if command == "set":
            context.user_data["menu_input"] = "research_topic"
            await message.edit_text("저장할 리서치 주제를 입력하세요.", reply_markup=_keyboard(_back()))
        else:
            await cmd_research(update, _context(context, [command]))
    elif action == "system:show":
        await cmd_system(update, _context(context, []))
    elif action.startswith("system:"):
        await cmd_system(update, _context(context, [action.split(":", 1)[1]]))
    elif action == "stockdb":
        await cmd_stockdb(update, _context(context, ["build"]))
    elif action == "help":
        await message.edit_text(
            "버튼을 눌러 기능을 실행하세요.\n"
            "리서치 주제만 일반 텍스트로 입력합니다. 관심종목·자산은 웹 /portfolio 에서 고칩니다.",
            reply_markup=main_menu(registry),
        )
    return True


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = message.text or ""
    registry = context.bot_data["feature_registry"]
    callback_data = registry.persistent_callback(text)
    if callback_data is not None:
        required_feature = registry.menu_owner(callback_data)
        if required_feature is not None and not registry.is_enabled(required_feature):
            await message.reply_text("이 기능은 현재 비활성화되어 있습니다.")
            return
        action = callback_data.removeprefix("nav:")
        if await _dispatch_primary_menu_action(
            update,
            context,
            action,
            message,
            edit_message=False,
        ):
            return
        await message.reply_text(
            "<b>주식 뉴스 봇</b>\n원하는 기능을 선택하세요.",
            parse_mode="HTML",
            reply_markup=main_menu(registry),
        )
        return

    action = context.user_data.pop("menu_input", None)
    if action is None:
        await refresh_persistent_menu(message, registry)
        return
    if action == "research_topic":
        await cmd_research(update, _context(context, ["set", text.strip()]))
    await message.reply_text(
        "하단 메뉴에서 다음 작업을 선택하세요.",
        reply_markup=persistent_menu(registry),
    )
