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
    install_services: ServiceInstaller | None = None
    install_jobs: JobInstaller | None = None
    data_files: tuple[str, ...] = ()
