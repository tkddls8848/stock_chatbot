"""인라인·하단 메뉴 키보드 생성기.

라우팅(`navigation.py`)에서 분리해 둔다. 기능 핸들러를 import하지 않는 순수
모듈이라 어느 기능에서 불러도 순환이 생기지 않는다 — `system_admin`이
`/system` 화면에 메뉴를 붙일 때 navigation을 통째로 끌어오던 고리를 끊는다.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in rows]
    )


def main_menu(registry) -> InlineKeyboardMarkup:
    grouped: dict[int, list[tuple[str, str]]] = {}
    for item in registry.menu_specs():
        grouped.setdefault(item.row, []).append((item.label, item.callback_data))
    return _keyboard([grouped[row] for row in sorted(grouped)])


def persistent_menu(registry) -> ReplyKeyboardMarkup:
    """채팅 입력창 위에 계속 표시되는 메뉴 진입 버튼. 두 줄·네 개로 둔다.

    텔레그램은 뉴스·브리핑을 받고 웹을 관리하는 곳이라, 자주 누르는 것만 남긴다.
    전체 메뉴는 /start가 인라인으로 연다.
    """
    grouped: dict[int, list[str]] = {}
    for item in registry.menu_specs():
        if item.persistent_label:
            grouped.setdefault(item.persistent_row, []).append(
                item.persistent_label
            )
    return ReplyKeyboardMarkup(
        [grouped[row] for row in sorted(grouped)],
        resize_keyboard=True,
        is_persistent=True,
    )


async def refresh_persistent_menu(
    message,
    registry,
) -> None:
    await message.reply_text(
        "⌨️ 하단 메뉴를 최신 상태로 갱신했습니다.",
        reply_markup=persistent_menu(registry),
    )


def _back() -> list[list[tuple[str, str]]]:
    return [[("🏠 처음", "nav:home")]]


def web_admin_menu(registry) -> InlineKeyboardMarkup:
    """하단 "🛠 웹 관리"가 여는 허브. 켜진 기능의 버튼만 보인다."""
    rows = []
    first = []
    if registry.is_enabled("research"):
        first.append(("🔎 리서치", "nav:research"))
    if registry.is_enabled("market_sentiment"):
        first.append(("📊 시장 감성 갱신", "nav:market"))
    if first:
        rows.append(first)
    rows.append([("🌐 웹 상태", "nav:web:status")])
    return _keyboard([*rows, *_back()])


def research_menu() -> InlineKeyboardMarkup:
    """리서치 관리 패널. 결과 자체는 웹 /research에서 본다."""
    return _keyboard([
        [("주제 보기", "nav:research:show"), ("지금 실행", "nav:research:run")],
        [("주제 변경", "nav:research:set"), ("주제 비우기", "nav:research:clear")],
        *_back(),
    ])


def system_menu(registry) -> InlineKeyboardMarkup:
    """시스템 상태 아래에 붙는 하위 항목.

    기능별 항목은 각 `FeatureSpec.status_reports`에서 온다. 여기에 기능
    이름을 적어 두면 그 기능을 끄거나 이름을 바꿀 때 이 파일도 함께
    고쳐야 한다.
    """
    rows = [[("📋 기능 카탈로그", "nav:system:features")]]
    rows[0].extend(
        (spec.label, f"nav:system:{spec.name}") for spec in registry.status_reports()
    )
    return _keyboard([*rows, *_back()])
