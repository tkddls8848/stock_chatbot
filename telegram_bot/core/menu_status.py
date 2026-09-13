"""인라인 메뉴 본문을 유지하면서 실행 버튼 문구만 갱신한다."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


async def set_menu_button_text(message, callback_data: str, text: str) -> bool:
    markup = getattr(message, "reply_markup", None)
    if markup is None:
        return False

    changed = False
    rows = []
    for row in markup.inline_keyboard:
        next_row = []
        for button in row:
            if button.callback_data == callback_data:
                next_row.append(
                    InlineKeyboardButton(text, callback_data=callback_data)
                )
                changed = True
            else:
                next_row.append(button)
        rows.append(next_row)
    if changed:
        await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
    return changed
