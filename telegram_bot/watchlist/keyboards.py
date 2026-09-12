from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_add_market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("중국 상하이", callback_data="add_market:CN:SH"), InlineKeyboardButton("중국 선전", callback_data="add_market:CN:SZ")],
        [InlineKeyboardButton("홍콩", callback_data="add_market:HK:HKEX"), InlineKeyboardButton("한국 KOSPI", callback_data="add_market:KR:KOSPI")],
        [InlineKeyboardButton("한국 KOSDAQ", callback_data="add_market:KR:KOSDAQ"), InlineKeyboardButton("미국 NASDAQ", callback_data="add_market:US:NASDAQ")],
        [InlineKeyboardButton("미국 NYSE", callback_data="add_market:US:NYSE")],
    ])


def build_list_keyboard(watchlist: dict[str, str]) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"삭제: {name} ({code})",
                callback_data=f"remove:{code}",
            ),
            InlineKeyboardButton(text="📈 감성", callback_data=f"view:{code}"),
        ]
        for code, name in watchlist.items()
    ]
    buttons.append([InlineKeyboardButton("종목추가", callback_data="add_stock")])
    buttons.append([InlineKeyboardButton("닫기", callback_data="close")])
    buttons.append([
        InlineKeyboardButton("⬅️ 관심종목", callback_data="nav:watch"),
        InlineKeyboardButton("🏠 처음", callback_data="nav:home"),
    ])
    return InlineKeyboardMarkup(buttons)
