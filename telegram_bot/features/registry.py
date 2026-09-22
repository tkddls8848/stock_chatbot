"""활성 기능 검증과 텔레그램·스케줄러 기여점 조립."""

from __future__ import annotations

from html import escape
from typing import Iterable

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from telegram_bot.core.access import restricted
from telegram_bot.features.base import FeatureSpec


class FeatureConfigurationError(ValueError):
    pass


class FeatureRegistry:
    def __init__(
        self,
        specs: Iterable[FeatureSpec],
        enabled_keys: Iterable[str],
    ):
        all_specs = tuple(specs)
        by_key = {spec.key: spec for spec in all_specs}
        requested = {str(key).strip() for key in enabled_keys if str(key).strip()}
        unknown = requested - set(by_key)
        if unknown:
            raise FeatureConfigurationError(
                f"알 수 없는 기능: {', '.join(sorted(unknown))}"
            )

        # 선언한 의존성을 실제로 검사한다. 예전에는 이름만 확인하고 넘어가서,
        # `FEATURES_ENABLED`에서 의존 기능을 빼면 서비스 설치 중 KeyError로
        # 죽거나(watchlist만 켜면 'stock_db'), 기동은 되고 예약 작업만 조용히
        # 매번 실패했다. 어느 쪽이든 원인이 설정이라는 사실이 드러나지 않는다.
        missing = sorted(
            f"{spec.key} → {dependency}"
            for spec in all_specs
            if spec.key in requested
            for dependency in spec.requires
            if dependency not in requested
        )
        if missing:
            raise FeatureConfigurationError(
                f"의존 기능이 비활성입니다: {', '.join(missing)}"
            )

        self._all_specs = all_specs
        self._enabled_specs = tuple(
            spec for spec in all_specs if spec.key in requested
        )
        self._enabled_keys = frozenset(requested)
        self._menu_owners = {
            menu.callback_data: spec.key
            for spec in all_specs
            for menu in spec.menus
        }
        self._persistent_callbacks = {
            menu.persistent_label: menu.callback_data
            for spec in all_specs
            for menu in spec.menus
            if menu.persistent_label
        }

    @property
    def enabled_keys(self) -> frozenset[str]:
        return self._enabled_keys

    def is_enabled(self, key: str) -> bool:
        return key in self._enabled_keys

    def menu_owner(self, callback_data: str) -> str | None:
        owner = self._menu_owners.get(callback_data)
        if owner is not None:
            return owner
        matching_roots = (
            (root, root_owner)
            for root, root_owner in self._menu_owners.items()
            if callback_data.startswith(f"{root}:")
        )
        return next(
            (
                root_owner
                for _, root_owner in sorted(
                    matching_roots,
                    key=lambda item: len(item[0]),
                    reverse=True,
                )
            ),
            None,
        )

    def persistent_callback(self, label: str) -> str | None:
        return self._persistent_callbacks.get(label)

    def install_telegram_handlers(self, app: Application) -> None:
        # 조립 시점에 가져온다. 최상위에서 import하면
        # features/__init__ -> registry -> navigation 고리가 닫혀,
        # navigation이 기능 핸들러를 함수 안에서만 부를 수 있게 된다.
        from telegram_bot.handlers.commands import callback_handler
        from telegram_bot.handlers.navigation import handle_menu_text

        for feature in self._enabled_specs:
            for command in feature.commands:
                app.add_handler(
                    CommandHandler(command.name, restricted(command.handler))
                )
        app.add_handler(CallbackQueryHandler(restricted(callback_handler)))
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                restricted(handle_menu_text, show_status=False),
            )
        )

    async def dispatch_callback(self, query, context, data: str) -> bool:
        """callback_data를 접두사가 일치하는 기능의 핸들러에 위임한다.

        비활성 기능의 접두사에 해당하면 안내 메시지로 응답한다. 핸들러가
        False를 반환하면 다음 일치 후보를 계속 시도한다.
        """
        for spec in self._all_specs:
            for callback in spec.callbacks:
                if not data.startswith(callback.prefixes):
                    continue
                if spec.key not in self._enabled_keys:
                    await query.edit_message_text(
                        f"{spec.label} 기능이 비활성화되어 있습니다."
                    )
                    return True
                if await callback.handler(query, context, data):
                    return True
        return False

    def install_services(self, app: Application) -> None:
        for feature in self._enabled_specs:
            if feature.install_services is not None:
                feature.install_services(app)

    def install_jobs(self, scheduler, app: Application) -> None:
        for feature in self._enabled_specs:
            if feature.install_jobs is not None:
                feature.install_jobs(scheduler, app)

    def telegram_commands(self) -> list[BotCommand]:
        return [
            BotCommand(command.name, command.description)
            for feature in self._enabled_specs
            for command in feature.commands
        ]

    def menu_specs(self):
        return [
            menu
            for feature in self._enabled_specs
            for menu in feature.menus
        ]

    def help_text(self) -> str:
        lines = ["<b>명령어 안내</b>", ""]
        for feature in self._enabled_specs:
            for command in feature.commands:
                usage = f" {command.usage}" if command.usage else ""
                lines.append(escape(f"/{command.name}{usage} — {command.description}"))
        lines.extend(
            [
                "",
                "종목코드 예: 중국 600519 · 홍콩 09988",
                "한국 KR:KOSPI:005930 · 미국 US:NASDAQ:AAPL",
            ]
        )
        return "\n".join(lines)

    def status_reports(self):
        """활성 기능이 선언한 `/system <name>` 화면들."""
        return [
            spec
            for feature in self._enabled_specs
            for spec in feature.status_reports
        ]

    def status_report(self, name: str):
        for spec in self.status_reports():
            if spec.name == name:
                return spec
        return None

    def catalog_lines(self) -> list[str]:
        """기능 하나당 헤더 한 줄 + 있는 항목만 들여쓴 하위 줄로 이어진다.

        모든 항목을 ` · `로 한 줄에 이어붙이면 데이터 파일이 많은 기능에서
        줄이 길게 늘어져 훑어보기 어렵다. 헤더로 존재를 한눈에 보고, 궁금한
        기능만 하위 줄을 읽게 나눈다.
        """
        lines: list[str] = []
        for feature in self._all_specs:
            enabled = feature.key in self._enabled_keys
            state = "✅ 활성" if enabled else "⛔ 비활성"
            lines.append(f"• <b>{feature.key}</b> ({feature.label}) — {state}")
            if feature.commands:
                commands = ", ".join(f"/{item.name}" for item in feature.commands)
                lines.append(f"    명령: {commands}")
            if feature.requires:
                lines.append(f"    의존: {', '.join(sorted(feature.requires))}")
            if feature.install_jobs is not None:
                lines.append("    스케줄: 있음")
            if feature.data_files:
                lines.append(f"    데이터: {', '.join(feature.data_files)}")
        return lines
