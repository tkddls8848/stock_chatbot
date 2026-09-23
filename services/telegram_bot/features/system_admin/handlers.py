"""시작·도움말·시스템 제어 명령 구현."""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from services.telegram_bot.handlers.menus import main_menu, persistent_menu, system_menu

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registry = context.bot_data["feature_registry"]
    await update.message.reply_text(
        registry.help_text(),
        parse_mode="HTML",
        reply_markup=persistent_menu(registry),
    )
    await update.message.reply_text(
        "원하는 기능을 선택하세요.",
        reply_markup=main_menu(registry),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


def _control_lines(registry) -> str:
    """`/system`이 받는 항목 목록. 기능이 선언한 것만 실린다."""
    items = ["  /system features — 기능 카탈로그"]
    if registry is not None:
        items.extend(
            f"  /system {spec.name} — {spec.label}" for spec in registry.status_reports()
        )
    return "\n".join(items)


def _usage(registry) -> str:
    names = ["features"]
    if registry is not None:
        names.extend(spec.name for spec in registry.status_reports())
    return "|".join(names)


def _format_system_status(registry, source_lines: list[str] | None = None) -> str:
    sources_part = ""
    if source_lines:
        sources_part = "\n\n<b>전역 뉴스 소스</b>\n" + "\n".join(
            f"  {line}" for line in source_lines
        )
    return (
        "<b>시스템 상태</b>\n\n"
        "추론: <b>Cloudflare Workers AI</b> (원격)"
        f"{sources_part}\n\n"
        "제어:\n" + _control_lines(registry)
    )




async def cmd_system(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    args = context.args or []
    command = args[0].lower() if args else ""
    feature_registry = context.bot_data.get("feature_registry")

    if command == "features":
        lines = (
            feature_registry.catalog_lines()
            if feature_registry is not None
            else ["기능 레지스트리가 준비되지 않았습니다."]
        )
        await message.reply_text(
            "<b>기능 카탈로그</b>\n" + "\n".join(f"  {line}" for line in lines),
            parse_mode="HTML",
        )
        return

    spec = (
        feature_registry.status_report(command)
        if feature_registry is not None and command
        else None
    )
    if spec is not None:
        text = await spec.render(context.bot_data)
        if text is None:
            await message.reply_text(
                f"{spec.label} 기능이 꺼져 있습니다(FEATURES_ENABLED)."
            )
            return
        await message.reply_text(text, parse_mode="HTML")
        return

    if command:
        await message.reply_text(
            f"알 수 없는 항목입니다. 사용법: /system [{_usage(feature_registry)}]",
            parse_mode="HTML",
        )
        return

    registry = context.bot_data.get("news_registry")
    source_lines = registry.status_lines() if registry is not None else None
    await message.reply_text(
        _format_system_status(feature_registry, source_lines),
        parse_mode="HTML",
        reply_markup=system_menu(feature_registry),
    )
