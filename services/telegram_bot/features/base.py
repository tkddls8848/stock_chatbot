"""기능 모듈이 애플리케이션에 제공하는 공개 선언 계약."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

CommandHandlerFunc = Callable[
    [Update, ContextTypes.DEFAULT_TYPE],
    Awaitable[None],
]
CallbackHandlerFunc = Callable[
    [Any, ContextTypes.DEFAULT_TYPE, str],
    Awaitable[bool],
]
# bot_data를 받아 완성된 화면 문자열을 돌려준다. 기능이 꺼져 있거나 자료가
# 없으면 None을 돌려주고 호출한 쪽이 안내 문구를 낸다.
StatusRenderFunc = Callable[[Any], Awaitable["str | None"]]
JobInstaller = Callable[[Any, Any], None]
ServiceInstaller = Callable[[Any], None]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    handler: CommandHandlerFunc
    usage: str = ""


@dataclass(frozen=True)
class CallbackSpec:
    """인라인 버튼 callback_data 접두사와 처리 함수의 연결.

    handler는 처리했으면 True를 반환한다. 접두사가 겹치는 다른 기능이
    있으면 False 반환 시 다음 후보로 넘어간다.
    """

    prefixes: tuple[str, ...]
    handler: CallbackHandlerFunc


@dataclass(frozen=True)
class StatusReportSpec:
    """`/system <name>`에 붙는 기능별 상태 화면.

    기능이 **완성된 문자열**을 돌려준다. 이렇게 두면 `system_admin`이 남의
    기능의 자료구조를 알 필요가 없어, 관측 항목을 바꿔도 그쪽 파일을 함께
    고치지 않는다.
    """

    name: str
    label: str
    render: StatusRenderFunc


@dataclass(frozen=True)
class MenuSpec:
    label: str
    callback_data: str
    row: int
    persistent_label: str = ""
    persistent_row: int = 0


@dataclass(frozen=True)
class FeatureSpec:
    """한 기능의 의존성·진입점·소유 자원을 한곳에 선언한다."""

    key: str
    label: str
    requires: frozenset[str] = frozenset()
    commands: tuple[CommandSpec, ...] = ()
    menus: tuple[MenuSpec, ...] = ()
    callbacks: tuple[CallbackSpec, ...] = ()
    status_reports: tuple[StatusReportSpec, ...] = ()
    install_services: ServiceInstaller | None = None
    install_jobs: JobInstaller | None = None
    data_files: tuple[str, ...] = ()
