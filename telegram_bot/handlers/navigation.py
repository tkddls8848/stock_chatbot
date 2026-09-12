"""명령어 입력 없이 사용하는 텔레그램 인라인 메뉴."""

from types import SimpleNamespace

from telegram import (
    Message,
    Update,
)
from telegram.ext import ContextTypes

from telegram_bot.briefing.service import cmd_briefing
from telegram_bot.features.instruments.handlers import cmd_stockdb
from telegram_bot.features.market_sentiment.handlers import cmd_market
from telegram_bot.features.system_admin.handlers import cmd_system
from telegram_bot.handlers.menus import (
    _back,
    _keyboard,
    main_menu,
    market_menu,
    persistent_menu,
    refresh_persistent_menu,
    research_menu,
)
from telegram_bot.research.handlers import cmd_research
from telegram_bot.watchlist.handlers import cmd_add, cmd_menu


def _context(context: ContextTypes.DEFAULT_TYPE, args: list[str]):
    # user_data를 그대로 전달해야 프록시로 호출되는 핸들러(cmd_add 등)가
    # add_market 같은 대화 상태에 접근할 수 있다(SimpleNamespace에는 기본으로 없음).
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

    `system`은 인라인에서 상태를 실행하고 하단 메뉴에서는 관리 허브를 열어
    동작이 다르므로 각 호출부가 명시적으로 처리한다.
    """
    if action == "market":
        send = message.edit_text if edit_message else message.reply_text
        await send(
            "<b>국가별 뉴스 감성</b>\n조회 기간을 선택하세요.",
            parse_mode="HTML",
            reply_markup=market_menu(),
        )
        return True
    if action == "watch":
        await cmd_menu(update, _context(context, []))
        return True
    if action == "research":
        send = message.edit_text if edit_message else message.reply_text
        await send(
            "<b>리서치</b>",
            parse_mode="HTML",
            reply_markup=research_menu(),
        )
        return True
    if action == "briefing":
        await cmd_briefing(update, _context(context, []))
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
    if action == "market:sentiment":
        await message.edit_text(
            "<b>국가별 뉴스 감성</b>\n조회 기간을 선택하세요.",
            parse_mode="HTML",
            reply_markup=market_menu(),
        )
    elif action.startswith("market:sentiment:"):
        await cmd_market(update, _context(context, [action.rsplit(":", 1)[1]]))
    elif action.startswith("research:"):
        command = action.split(":", 1)[1]
        if command == "set":
            context.user_data["menu_input"] = "research_topic"
            await message.edit_text("저장할 리서치 주제를 입력하세요.", reply_markup=_keyboard(_back()))
        else:
            await cmd_research(update, _context(context, [command]))
    elif action == "system":
        await cmd_system(update, _context(context, []))
    elif action.startswith("system:"):
        await cmd_system(update, _context(context, [action.split(":", 1)[1]]))
    elif action == "stockdb":
        await cmd_stockdb(update, _context(context, ["build"]))
    elif action == "help":
        await message.edit_text(
            "버튼을 눌러 기능을 실행하세요.\n"
            "종목 코드와 리서치 주제만 일반 텍스트로 입력합니다.",
            reply_markup=main_menu(registry),
        )
    return True


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = message.text or ""
    registry = context.bot_data["feature_registry"]
    if text == "🏠 홈":
        await refresh_persistent_menu(message, registry)
        await message.reply_text(
            "<b>주식 뉴스 봇</b>\n원하는 기능을 선택하세요.",
            parse_mode="HTML",
            reply_markup=main_menu(registry),
        )
        return
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
        if action == "system":
            await message.reply_text("<b>관리</b>", parse_mode="HTML", reply_markup=_keyboard([
                [("시스템 상태", "nav:system"), ("종목 DB 갱신", "nav:stockdb")], *_back()
            ]))
        else:
            # 알 수 없는 persistent 버튼 — 새 메뉴를 추가할 때 이 분기를 잊으면
            # 예전에는 조용히 "⚙️ 관리" 화면으로 잘못 떨어졌다. 그 대신 홈으로
            # 보내 틀린 화면이 아니라 눈에 띄는 결과가 나오게 한다.
            await message.reply_text(
                "<b>주식 뉴스 봇</b>\n원하는 기능을 선택하세요.",
                parse_mode="HTML",
                reply_markup=main_menu(registry),
            )
        return

    if context.user_data.get("add_market"):
        await cmd_add(update, _context(context, [text.strip()]))
        return

    action = context.user_data.pop("menu_input", None)
    if action is None:
        return
    if action == "research_topic":
        await cmd_research(update, _context(context, ["set", text.strip()]))
    await message.reply_text(
        "하단 메뉴에서 다음 작업을 선택하세요.",
        reply_markup=persistent_menu(registry),
    )
