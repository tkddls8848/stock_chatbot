from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
import subprocess
from typing import Sequence

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .scenario import Scenario, Scene
from .tts import Word, locate


WIDTH, HEIGHT = 1080, 1920
_COLORS = {
    "ink": "#F4F7FB",
    "muted": "#8E99A8",
    "panel": "#111821",
    "panel_alt": "#0D131A",
    "line": "#283444",
    "track": "#1F2935",
    "on_accent": "#06110B",
    "gold": "#FFC83D",
    "red": "#FF5C6C",
    "blue": "#4CC9FF",
    "green": "#3DDC84",
}

class RenderError(RuntimeError):
    pass


def find_font(configured: Path | None = None) -> Path:
    candidates = [
        configured,
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        Path("C:/Windows/Fonts/malgunbd.ttf"),
        Path("C:/Windows/Fonts/malgun.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RenderError("한글 폰트가 없습니다. SHORTS_FONT_FILE을 지정하세요")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


# ── YouTube Shorts 안전 영역과 세로 배치 ─────────────────
# 세로 1080x1920에서 플레이어 UI가 프레임을 덮는다. 위 ~180px은 검색·내비게이션,
# 아래 ~350px은 채널명·제목·음원 바, 오른쪽 ~192px은 좋아요·댓글·공유 버튼 줄이다.
# 그 밖에 그린 것은 실제 재생 화면에서 보이지 않는다.
#
# 안전 영역을 지키느라 프레임의 아래 절반을 비워 두면 안 된다. 2026-09-23
# 산출물이 정확히 그랬다 — 그림은 위 850px까지만 있고 그 아래는 #101B20 한 색,
# 정보는 위쪽 40%에 몰리고 자막만 중간 높이에 작게 떠 있었다. 배경 사진은 이제
# 프레임 전체를 덮고(`_background`), 본문은 SAFE_BOTTOM 바로 위까지 내려오며,
# 자막은 쇼츠 관례대로 하단 1/3을 크게 차지한다. 안전 영역 아래에는 아무 글자도
# 두지 않지만 사진은 계속 흐르므로 빈 띠가 생기지 않는다.
#
# 2026-10-07 운영자 요청으로 화면 문법을 참고 영상(Solostack "GitHub 트렌딩 Top 10")에 맞췄다 — 가운데
# 정렬한 굵은 머리말, 어두운 바탕 위 네온 테두리 카드와 번호 탭, 수치 칩, 상자 자막. 예전 화면은 가는
# 글씨가 왼쪽에 붙고 사진 위에 잔글씨가 흩어져 무엇을 봐야 할지 흐렸다.
#
# 세로 순서: 꼬리표 → 제목(도입·마무리는 두 줄 머리말) → 진행 점 → 카드 → 수치 칩 → 고지 → 자막.
SAFE_TOP = 190                          # 이 위는 검색·내비게이션 구역
SAFE_BOTTOM = HEIGHT - 380              # 1540. 이 아래는 Shorts UI 구역
SAFE_LEFT = 72
SAFE_RIGHT = WIDTH - 72                 # 1008. 카드 테두리까지는 버튼 줄과 겹쳐도 된다
BODY_RIGHT = WIDTH - 202                # 878. 카드 안 내용과 칩은 버튼 줄을 피해 여기까지만 쓴다
TAG_Y, TAG_HEIGHT = 196, 64
TITLE_TOP, TITLE_BOTTOM = 290, 580
PROGRESS_Y = 606                        # 질문 순서 점
CARD_TOP, CARD_BOTTOM = 690, 1150       # 카드 테두리. 번호 탭은 위로 걸친다
CARD_RADIUS = 32
HEADLINE_CENTER = 800                   # 도입·마무리 머리말 묶음의 세로 가운데
CARD_PAD = 44
BODY_TOP, BODY_BOTTOM = CARD_TOP + 48, CARD_BOTTOM - 36  # 카드 안 내용 칸
META_Y = 1176                           # 수치 칩
CHIP_HEIGHT = 108
# 선택지 한 줄 안에서 게이지가 앉는 높이(줄 높이의 비율)와 그 두께. 줄의 나머지는
# 다음 줄과의 간격이다 — 막대와 다음 줄 이름이 붙으면 둘이 한 덩어리로 보인다.
OPTION_BAR_TOP = .82
OPTION_BAR = 20
FOOTER_Y = 1302                         # 고지문과 자료 기준. 자막 바로 위다
CAPTION_MARGIN_V = HEIGHT - SAFE_BOTTOM  # 380. 자막 아래 끝을 안전 영역 바닥에 붙인다

# ── 배경 ─────────────────────────────────────────────
# 사진은 흐리고 어둡게 깔아 결만 남기고(`_backdrop`), 그 위에 세로 어둠과 머리말 뒤의 빛 번짐을
# 얹는다(`_atmosphere`). (y, 불투명도)이고 사이는 선형이다.
# 2026-10-07 운영자 지적: 흐림(14px)과 어둠이 세서 매일 새로 그리는 배경이 보이지 않았다. 사진은 거의 그대로
# 살리고(가는 흐림만 남겨 AI 그림의 잔결을 누른다), 글자가 앉는 자리 뒤에만 부드러운 그늘을 깐다(`_shade`).
_SCRIM = ((0, .30), (420, .30), (900, .38), (1500, .46), (HEIGHT, .40))
_SCRIM_RGB = (6, 9, 13)
BACKDROP_BLUR = 1.2
BACKDROP_BRIGHTNESS = .9

# 장면마다 같은 사진을 다르게 본다. 확대 여유 안에서 크롭 위치를 옮기고, 짝수
# 장면은 좌우를 뒤집고, 장면의 accent 색으로 색조를 입힌다 — 새 미디어 소스도
# 네트워크도 쓰지 않고 장면 성격에 따라 배경이 달라진다.
_ZOOM = 1.16
_TONE_STRENGTH = .5
_BRIGHTNESS = (1.12, .95, 1.05, 1.0)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """어절 경계에서 줄을 바꿔 '0회', '97.5%' 같은 수치를 한 덩어리로 남긴다."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = ""
            # 공백 없는 긴 원고도 누락하지 않는다. 한 어절 자체가 폭을 넘을 때만 나눈다.
            for char in word:
                if current and draw.textlength(current + char, font=font) > width:
                    lines.append(current)
                    current = ""
                current += char
        if current:
            lines.append(current)
    return lines


def _background(path: Path | None, *, index: int, total: int, accent: str) -> Image.Image:
    """저장된 배경 한 장을 장면마다 다르게 보이도록 잡는다.

    쓸 수 있는 그림이 둘뿐이라 2026-09-23 산출물은 네 장면이 모두 같은 도시
    야경이었다. 장면별 `visual_query`가 고르는 파일은 그대로 두고, 여기서
    **크롭 위치·좌우 방향·색조·밝기**를 장면 번호와 accent로 정한다. 새 자산도,
    생성 호출도, 네트워크도 없이 장면이 바뀐 것이 눈에 보인다.
    """
    if path is None or not path.is_file():
        return Image.new("RGB", (WIDTH, HEIGHT), _SCRIM_RGB)
    with Image.open(path) as source:
        wide = ImageOps.fit(source.convert("RGB"), (round(WIDTH * _ZOOM), round(HEIGHT * _ZOOM)),
                            method=Image.Resampling.LANCZOS)
    # 확대 여유를 장면 순서대로 가로는 왼쪽→오른쪽, 세로는 아래→위로 훑는다.
    share = (index - 1) / (total - 1) if total > 1 else .5
    left = round((wide.width - WIDTH) * share)
    top = round((wide.height - HEIGHT) * (1 - share))
    frame = wide.crop((left, top, left + WIDTH, top + HEIGHT))
    if index % 2 == 0:
        frame = ImageOps.mirror(frame)
    tone = Image.new("RGB", frame.size, _COLORS.get(accent, _COLORS["gold"]))
    frame = Image.blend(frame, ImageChops.multiply(frame, tone), _TONE_STRENGTH)
    return ImageEnhance.Brightness(frame).enhance(_BRIGHTNESS[index % len(_BRIGHTNESS)])


# 화면에 고정으로 찍히는 말. 영어판은 같은 틀에 이 문구만 바꿔 그린다.
_CHROME = {
    "ko": {"yes": "예", "site": "nunchi.live", "brand_tag": "집단 예측 컨센서스",
           "open_top": "오늘의 집단 예측", "open_low": "컨센서스 요약", "open_note": "지금 시작합니다",
           "close_top": "자세한 내용은", "close_note": "에서 확인하세요",
           "footer_options": "막대는 '예' 쪽 확률 · 집단 예측 컨센서스 · 투자 조언 아님",
           "footer": "집단 예측 컨센서스 · 투자 조언 아님"},
    "en": {"yes": "YES", "site": "nunchi.live", "brand_tag": "Crowd forecast consensus",
           "open_top": "TODAY'S CROWD FORECASTS", "open_low": "CONSENSUS SUMMARY", "open_note": "Starting now",
           "close_top": "Full details at", "close_note": "Check each question's conditions",
           "footer_options": "Bar = YES probability · crowd forecast consensus · not investment advice",
           "footer": "Crowd forecast consensus · not investment advice"},
}
_BRAND = "NUNCHI"
_LEADING_NUMBER = re.compile(r"^\s*(\d{1,2})\s*[·.]\s*")


def _bold(font_path: Path) -> Path:
    """같은 글꼴의 굵은 판. 머리말·숫자·칩은 굵게 쓴다(자막도 굵은 판이다, `blender_render.py`)."""
    sibling = {"NotoSansCJK-Regular.ttc": "NotoSansCJK-Bold.ttc", "malgun.ttf": "malgunbd.ttf"}.get(font_path.name)
    bold = font_path.with_name(sibling) if sibling else font_path
    return bold if bold.is_file() else font_path


def _rgb(color: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(color)[:3]


def _accent(scene: Scene) -> str:
    """도입·마무리는 채널 색(초록), 이슈 장면은 분야 색이다."""
    return _COLORS["green"] if scene.kind in {"intro", "outro"} else _COLORS.get(scene.accent, _COLORS["gold"])


@lru_cache(maxsize=16)
def _backdrop(path: Path | None, index: int, total: int, accent: str) -> Image.Image:
    """배경 사진은 흐리고 어둡게 깔아 결만 남긴다. 글자와 카드가 사진 앞에 선다."""
    frame = _background(path, index=index, total=total, accent=accent)
    if path is None or not path.is_file():
        return frame
    return ImageEnhance.Brightness(frame.filter(ImageFilter.GaussianBlur(BACKDROP_BLUR))).enhance(BACKDROP_BRIGHTNESS)


@lru_cache(maxsize=16)
def _atmosphere(accent: str, transparent: bool) -> Image.Image:
    """글자 뒤의 어둠과 머리말 뒤에 고이는 빛. 움직이는 배경 위(`transparent`)에서는 조금 더 어둡게 깐다."""
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lift = .12 if transparent else 0.0
    for (top, start), (bottom, stop) in zip(_SCRIM, _SCRIM[1:]):
        for y in range(top, min(bottom, HEIGHT)):
            share = (y - top) / max(1, bottom - top)
            alpha = min(1.0, start + (stop - start) * share + lift)
            draw.line((0, y, WIDTH, y), fill=(*_SCRIM_RGB, round(255 * alpha)))
    glow = Image.new("RGBA", (WIDTH // 4, HEIGHT // 4), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((10, -110, WIDTH // 4 - 10, 120), fill=(*_rgb(accent), 120))
    glow = glow.filter(ImageFilter.GaussianBlur(34)).resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC)
    return Image.alpha_composite(layer, glow)


def _glow(image: Image.Image, paint, *, radius: int = 14, strength: float = 1.0) -> None:
    """`paint(draw)`로 그린 것을 빛 번짐과 함께 얹는다. 흐림은 그린 자리 둘레만 한다.

    글씨에는 약하게만 쓴다(`strength`) — 번짐이 세면 글자 가장자리가 뭉개져 읽기 어렵다(운영자 지적, 2026-10-07).
    """
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    paint(ImageDraw.Draw(layer))
    box = layer.getbbox()
    if box is None:
        return
    pad = radius * 3
    left, top = max(0, box[0] - pad), max(0, box[1] - pad)
    right, bottom = min(image.width, box[2] + pad), min(image.height, box[3] + pad)
    piece = layer.crop((left, top, right, bottom))
    halo = piece.filter(ImageFilter.GaussianBlur(radius))
    if strength < 1:
        halo.putalpha(halo.getchannel("A").point(lambda value: round(value * strength)))
    image.alpha_composite(halo, (left, top))
    image.alpha_composite(piece, (left, top))


def _shade(image: Image.Image, box, *, alpha: int = 150, radius: int = 60) -> None:
    """글자 묶음 뒤에 가장자리가 번진 그늘을 깐다. 배경 사진은 살리고 글자만 떠오르게 한다."""
    layer = Image.new("RGBA", (WIDTH // 4, HEIGHT // 4), (0, 0, 0, 0))
    left, top, right, bottom = (round(value / 4) for value in box)
    ImageDraw.Draw(layer).rounded_rectangle((left, top, right, bottom), radius=radius // 4,
                                            fill=(*_SCRIM_RGB, alpha))
    layer = layer.filter(ImageFilter.GaussianBlur(radius / 4)).resize(image.size, Image.Resampling.BICUBIC)
    image.alpha_composite(layer)


def _fit(draw, text: str, font_path: Path, width: int, sizes: Sequence[int], max_lines: int):
    """가장 큰 글자로 `max_lines` 줄 안에 든다. 끝까지 안 들면 글자를 버리지 않고 실패한다."""
    for size in sizes:
        font = _font(font_path, size)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    raise RenderError("화면 텍스트가 안전 영역을 넘습니다: " + text[:70])


def _text_block(draw, text, font_path, box, *, size=48, color=None, center=False, align="left"):
    """칸 안에 글을 모두 넣는다. 한 줄도 버리지 않는다.

    `center`면 남는 세로 여백을 위아래로 나눈다 — 칸은 가장 긴 문구에 맞춰 잡고 짧은 문구는
    그 안에서 가운데 선다. `align="center"`는 줄마다 가로 가운데다.
    """
    x, y, right, bottom = box
    if not text.strip():
        return
    for candidate in range(size, 25, -2):
        font = _font(font_path, candidate)
        lines = _wrap(draw, text, font, right - x)
        spacing = round(candidate * 1.38)
        if len(lines) * spacing <= bottom - y:
            if center:
                y += (bottom - y - len(lines) * spacing) // 2
            for line in lines:
                left = x + (right - x - draw.textlength(line, font=font)) / 2 if align == "center" else x
                draw.text((left, y), line, font=font, fill=color or _COLORS["ink"])
                y += spacing
            return
    raise RenderError("화면 텍스트가 안전 영역을 넘습니다: " + text[:70])


def _tag(draw, text: str, font_path: Path, accent: str) -> None:
    """맨 위 꼬리표. 어두운 반투명 띠에 왼쪽 색 막대, 채널 이름과 장면 분류를 한 줄로."""
    font = _font(_bold(font_path), 30)
    label = f"{_BRAND} · {text}" if text else _BRAND
    while draw.textlength(label, font=font) > SAFE_RIGHT - SAFE_LEFT - 80 and len(label) > 8:
        label = label[:-2] + "…"
    width = draw.textlength(label, font=font) + 72
    left = (WIDTH - width) / 2
    draw.rounded_rectangle((left, TAG_Y, left + width, TAG_Y + TAG_HEIGHT), radius=12, fill=(255, 255, 255, 26))
    draw.rounded_rectangle((left, TAG_Y, left + 10, TAG_Y + TAG_HEIGHT), radius=4, fill=accent)
    _ink_text(draw, (left + 40, TAG_Y, left + width, TAG_Y + TAG_HEIGHT), label, font, _COLORS["ink"], center_x=False)


def _headline(image, lines: Sequence[tuple[str, str]], font_path: Path, accent: str) -> None:
    """도입·마무리의 고정 머리말. (문구, 역할)의 줄을 화면 가운데 세로로 세운다.

    역할은 "ink"(흰 굵은 줄), "accent"(큰 accent 줄 + 밑줄), "plain"(보통 굵기 흰 줄)이다.
    """
    draw = ImageDraw.Draw(image)
    bold = _bold(font_path)
    width = SAFE_RIGHT - SAFE_LEFT
    sizes = {"ink": range(92, 50, -4), "accent": range(124, 60, -4), "plain": range(58, 36, -2)}
    fitted = [(role, *_fit(draw, text, bold if role != "plain" else font_path, width, sizes[role], 1))
              for text, role in lines if text.strip()]
    pitch = [round(font.size * (1.42 if role == "accent" else 1.3)) for role, font, _ in fitted]
    y = HEADLINE_CENTER - sum(pitch) // 2
    for (role, font, (line,)), step in zip(fitted, pitch):
        left = (WIDTH - draw.textlength(line, font=font)) / 2
        if role == "accent":
            line_y = y + round(font.size * 1.2)
            right = left + draw.textlength(line, font=font)
            _glow(image, lambda layer: layer.rectangle((left, line_y, right, line_y + 10), fill=accent), radius=12)
            _glow(image, lambda layer: layer.text((left, y), line, font=font, fill=accent, stroke_width=2,
                                                  stroke_fill=accent), radius=10, strength=.3)
        else:
            stroke = 2 if role == "ink" else 0
            draw.text((left, y), line, font=font, fill=_COLORS["ink"], stroke_width=stroke, stroke_fill=_COLORS["ink"])
        y += step


def _ink_text(draw, box, text: str, font, fill, *, center_x: bool = True, stroke: int = 0) -> None:
    """글자의 실제 잉크 범위를 상자 세로 가운데에 놓는다.

    글꼴의 줄 높이로 맞추면 한글 글꼴의 큰 윗여백 때문에 글자가 상자 아래로 처진다(운영자 지적,
    2026-10-07 — 자막 상자는 위 57px·아래 6px였다).
    """
    left, top, right, bottom = box
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = left + (right - left - (x1 - x0)) / 2 - x0 if center_x else left - x0
    y = top + (bottom - top - (y1 - y0)) / 2 - y0
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=fill)


def _title(draw, title: str, font_path: Path) -> None:
    """이슈 장면의 제목. 굵게 두 줄까지, 칸 안에서 가운데 선다."""
    font, lines = _fit(draw, title, _bold(font_path), SAFE_RIGHT - SAFE_LEFT, range(84, 50, -4), 2)
    spacing = round(font.size * 1.3)
    y = TITLE_TOP + (TITLE_BOTTOM - TITLE_TOP - spacing * len(lines)) // 2
    for line in lines:
        draw.text(((WIDTH - draw.textlength(line, font=font)) / 2, y), line, font=font,
                  fill=_COLORS["ink"], stroke_width=1, stroke_fill=_COLORS["ink"])
        y += spacing


def _progress(draw, current: int, count: int, accent: str) -> None:
    """질문 몇 번째인지. 지금 질문은 길쭉한 accent 막대, 나머지는 점이다."""
    if count < 2:
        return
    widths = [56 if slot == current else 14 for slot in range(1, count + 1)]
    left = (WIDTH - sum(widths) - 14 * (count - 1)) / 2
    for slot, width in enumerate(widths, start=1):
        draw.rounded_rectangle((left, PROGRESS_Y, left + width, PROGRESS_Y + 14), radius=7,
                               fill=accent if slot <= current else _COLORS["line"])
        left += width + 14


def _card(image, box, accent: str, font_path: Path, *, tab: str = "") -> None:
    """네온 테두리 카드와 왼쪽 위에 걸친 번호 탭."""
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=CARD_RADIUS, fill=(*_rgb(_COLORS["panel"]), 240))
    _glow(image, lambda layer: layer.rounded_rectangle(box, radius=CARD_RADIUS, outline=accent, width=4),
          radius=16, strength=.7)
    if not tab:
        return
    font = _font(_bold(font_path), 46)
    width = draw.textlength(tab, font=font) + 56
    left, top = box[0] + 34, box[1] - 44
    draw.rounded_rectangle((left, top, left + width, top + 76), radius=16, fill=accent)
    _ink_text(draw, (left, top, left + width, top + 76), tab, font, _COLORS["on_accent"], stroke=1)


def _options_block(draw, scene: Scene, font_path: Path, accent: str, box, *, shown: int,
                   yes: str = "예") -> None:
    """선택지를 이름 + 큰 '예' 확률 + 게이지 한 줄로 그린다.

    예전 화면은 원자료 형식을 그대로 옮겨 "9월 WTI 90달러 이하: 예 99.95%,
    아니오 0.05%" 한 덩어리였다. 이지선다에서 아니오는 예의 나머지이므로 화면은
    '예' 확률 하나만 크게 세우고 막대로 그 크기를 보여 준다.

    `shown`은 지금까지 등장한 줄 수다. 아직 말하지 않은 줄도 이름(흐린 색)과 빈 게이지를
    처음부터 그려 두어, 질문을 듣는 동안 무엇을 고르는지 보이고 이미 뜬 줄이 밀리지 않는다.
    """
    x, top, right, bottom = box
    bold = _bold(font_path)
    height = (bottom - top) / max(1, len(scene.options))
    for position, (label, percent, probability) in enumerate(scene.options):
        y = top + position * height
        bar_y = y + round(height * OPTION_BAR_TOP)
        draw.rounded_rectangle((x, bar_y, right, bar_y + OPTION_BAR), radius=OPTION_BAR // 2, fill=_COLORS["track"])
        revealed = position < shown
        label_font, (label_line,) = _fit(draw, label, bold, right - x, range(38, 24, -2), 1)
        draw.text((x, y), label_line, font=label_font, fill=_COLORS["ink"] if revealed else _COLORS["muted"])
        if not revealed:
            continue
        number = _font(bold, min(120, round(height * .47)))
        number_y = y + round(height * .19)
        draw.text((x, number_y), percent, font=number, fill=accent, stroke_width=1, stroke_fill=accent)
        draw.text((x + draw.textlength(percent, font=number) + 18, number_y + round(number.size * .52)),
                  yes, font=_font(bold, 32), fill=_COLORS["muted"])
        filled = round((right - x) * min(1.0, max(0.0, probability)))
        if filled > OPTION_BAR:
            draw.rounded_rectangle((x, bar_y, x + filled, bar_y + OPTION_BAR), radius=OPTION_BAR // 2, fill=accent)


def _chips(draw, bullets: Sequence[str], font_path: Path) -> None:
    """근거 수치를 칩으로 나눈다. "24시간 참여 규모 · 20.3K달러"는 작은 이름과 굵은 값이 된다."""
    items = [tuple(bullet.split(" · ", 1)) if " · " in bullet else ("", bullet) for bullet in bullets][:3]
    if not items:
        return
    bold = _bold(font_path)
    gap = 16
    width = (BODY_RIGHT - SAFE_LEFT - gap * (len(items) - 1)) / len(items)
    for slot, (label, value) in enumerate(items):
        left = SAFE_LEFT + slot * (width + gap)
        draw.rounded_rectangle((left, META_Y, left + width, META_Y + CHIP_HEIGHT), radius=18,
                               fill=(*_rgb(_COLORS["panel_alt"]), 235), outline=_COLORS["line"], width=2)
        inner = round(width - 40)
        if label:
            small, (label_line,) = _fit(draw, label, font_path, inner, range(24, 15, -1), 1)
            draw.text((left + 20, META_Y + 14), label_line, font=small, fill=_COLORS["muted"])
        value = _compact(value)
        try:
            font, lines = _fit(draw, value, bold, inner, range(34, 21, -2), 1)
        except RenderError:
            font, lines = _fit(draw, value, bold, inner, range(26, 17, -2), 2)
        y = META_Y + (50 if label else 24) - (12 if len(lines) == 2 else 0) - (14 if len(lines) == 2 and not label else 0)
        for line in lines:
            draw.text((left + 20, y), line, font=font, fill=_COLORS["ink"])
            y += round(font.size * 1.15)


def _compact(value: str) -> str:
    """칩 한 칸에 들게 줄인다: "2027-01-01 세계 표준시" → "2027.01.01", "유효 13개 중 상위 2개" → "13개 중 2개"."""
    value = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", r"\1.\2.\3", value)
    value = re.sub(r"(\d{4}\.\d{2}\.\d{2}) (?:세계 표준시|UTC)\b", r"\1", value)
    return re.sub(r"유효 (\d+개) 중 상위 (\d+개)", r"\1 중 \2", value)


def render_frame(
    scene: Scene,
    path: Path,
    *,
    font_path: Path,
    index: int,
    total: int,
    background_path: Path | None = None,
    shown: int | None = None,
    transparent: bool = False,
    language: str = "ko",
) -> None:
    chrome = _CHROME[language]
    accent = _accent(scene)
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    number = _LEADING_NUMBER.match(scene.kicker)
    category = scene.kicker[number.end():] if number else scene.kicker
    # 배경 사진을 살린 만큼 글자가 앉는 자리 뒤에만 그늘을 깐다. 글자보다 먼저 깐다.
    if scene.kind in {"intro", "outro"}:
        _shade(image, (60, HEADLINE_CENTER - 240, WIDTH - 60, HEADLINE_CENTER + 240), alpha=170)
    else:
        _shade(image, (40, TITLE_TOP - 24, WIDTH - 40, PROGRESS_Y + 40), alpha=165)
        _shade(image, (40, META_Y - 16, WIDTH - 40, META_Y + CHIP_HEIGHT + 16), alpha=120)
    _shade(image, (40, FOOTER_Y - 14, WIDTH - 40, FOOTER_Y + 74), alpha=150)
    _tag(draw, chrome["brand_tag"] if scene.kind in {"intro", "outro"} else category, font_path, accent)
    card = (SAFE_LEFT, CARD_TOP, SAFE_RIGHT, CARD_BOTTOM)
    inner = (SAFE_LEFT + CARD_PAD, BODY_TOP, BODY_RIGHT, BODY_BOTTOM)

    if scene.kind == "intro":
        # 도입과 마무리는 그날 내용과 무관하게 늘 같은 화면이다(운영자 결정 2026-10-07). 내용은 이슈 장면이 맡는다.
        _headline(image, ((chrome["open_top"], "ink"), (chrome["open_low"], "accent"), (chrome["open_note"], "plain")),
                  font_path, accent)
    elif scene.kind == "outro":
        _headline(image, ((chrome["close_top"], "ink"), (chrome["site"], "accent"), (chrome["close_note"], "plain")),
                  font_path, accent)
    else:
        _title(draw, scene.title, font_path)
        if number and total > 2:
            _progress(draw, int(number[1]), total - 2, accent)
        _card(image, card, accent, font_path, tab=f"Q{int(number[1])}" if number else "")
        if scene.options:
            _options_block(draw, scene, font_path, accent, inner,
                           shown=len(scene.options) if shown is None else shown, yes=chrome["yes"])
        else:
            _text_block(draw, scene.body, font_path, inner, size=62, center=True)
        _chips(draw, scene.bullets, font_path)

    draw.text((SAFE_LEFT, FOOTER_Y), chrome["footer_options"] if scene.options else chrome["footer"],
              font=_font(font_path, 23), fill=_COLORS["muted"])
    draw.text((SAFE_LEFT, FOOTER_Y + 34), scene.source_note, font=_font(font_path, 22), fill=accent)
    framed = Image.alpha_composite(_atmosphere(accent, transparent), image)
    if not transparent:
        backdrop = _backdrop(background_path, index, total, scene.accent).convert("RGBA")
        framed = Image.alpha_composite(backdrop, framed).convert("RGB")
    framed.save(path, "PNG", compress_level=1)


def probe_duration(audio_path: Path, *, ffprobe_bin: str) -> float:
    command = [
        ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RenderError(f"ffprobe 실패: {(result.stderr or result.stdout)[-500:]}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RenderError("오디오 길이를 읽지 못했습니다") from exc


# 자막은 하단 1/3에 두고 libass FontSize=56과 같은 글자 크기를 유지한다.
CAPTION_FONT_SIZE = 56
# 자막은 화면 가운데에 선다. 오른쪽 좋아요·댓글 버튼 줄을 피하는 여백을 왼쪽에도
# 똑같이 둔다 — 왼쪽만 72로 두면 자막 상자의 가운데가 59px 왼쪽으로 쏠린다.
CAPTION_MARGIN_R = 190                  # 오른쪽 좋아요·댓글 버튼 줄
CAPTION_MARGIN_L = CAPTION_MARGIN_R
# 자막은 둥근 먹색 상자 위의 흰 굵은 글씨다(참고 영상의 자막 띠, 2026-10-07). Blender text 스트립의
# 상자는 한글 글꼴의 큰 윗여백까지 상자에 넣어 글자가 아래로 처졌다(위 57px·아래 6px) — 그래서 자막도
# Pillow로 그려 잉크 범위를 상자 가운데에 놓는다(`_caption_frame`). 줄바꿈 폭은 `CAPTION_FONT_SIZE`로 잰다.
CAPTION_RENDER_SIZE = 46
CAPTION_BOX_RGBA = (6, 9, 13, 210)
CAPTION_PAD_X, CAPTION_PAD_Y = 30, 20
# 줄바꿈은 우리가 어절 경계에서 넣고 libass는 그대로 그린다(WrapStyle=2). libass는
# 한글도 중국어·일본어처럼 아무 글자에서나 끊어서, "10월 금리 변동 없음과"가
# "…없" / "음과 …"로 갈라졌다. 합성 볼드가 측정보다 넓어질 수 있어 여유를 둔다.
CAPTION_WIDTH = round((WIDTH - CAPTION_MARGIN_L - CAPTION_MARGIN_R) * .92)


# 자막은 자기 첫 단어보다 이만큼 먼저 뜬다.
CAPTION_LEAD = 0.05
# 장면이 바뀔 때는 더 일찍 넘긴다. tts가 넓혀 둔 장면 경계 쉼의 뒤쪽 이만큼이
# 새 화면 위에서 흐르므로, 화면이 먼저 자리를 잡은 뒤에 말이 시작된다.
SCENE_LEAD = 0.55
# 한 자막에 담는 글자 수. 커진 자막(CAPTION_FONT_SIZE)에서 두 줄에 들어가는 양이다.
# 영문은 글자 폭이 한글의 절반쯤이라 같은 두 줄에 더 담는다. 자막을 가운데로 옮기며
# 폭이 좁아져(752→644px) 26·44에서 24·40으로 줄였다 — 그대로 두면 세 줄이 네 배로 는다.
_PHRASE_CHARS = 24
_PHRASE_CHARS_BY_LANGUAGE = {"ko": _PHRASE_CHARS, "en": 40}
_SENTENCE_END = (".", "?", "!")
# 끊기 좋은 자리와 나쁜 자리. 쉼표는 말하는 사람이 이미 쉬는 자리이고 연결어미
# ("…다르니")도 한 마디가 끝나는 자리다. 반대로 "…와·…과·…의"는 다음 말에 붙는
# 조사라 그 뒤에서 끊으면 "숫자와" / "함께"처럼 한 덩어리가 갈라진다.
_CLAUSE_END = re.compile(r"(?:니|고|며|면|서|는데|지만)$")
_BINDING_END = ("와", "과", "의")

# ── 움직임 ─────────────────────────────────────────────
# 정지 카드를 20초씩 그대로 세워 두면 화면이 멈춘 것처럼 보인다. 움직임은 셋뿐이고
# 전부 기존 렌더 경로 안에서 만든다 — 새 입력도, 새 의존성도, 새 네트워크도 없다.
#
# 1. 수치 카운트업: 큰 숫자가 0에서 제자리까지 차오른다. 정지 PNG 여러 장일 뿐이다.
# 2. 선택지의 순차 등장: 선택지 줄이 말의 순서대로 하나씩 쌓이고, 새로 뜬 줄의
#    숫자가 차오른다. 자리는 처음부터 잡혀 있어 이미 뜬 줄이 밀리지 않는다.
# 3. 프레임 전체의 느린 흐름: 가장자리를 조금 잘라 내고 그 안에서 천천히 움직인다.
COUNTUP_SECONDS = 1.0
# 영상 한 프레임(30fps)마다 한 장이다 — 8장(0.09초 간격)이던 때는 막대가 계단처럼 튀었다(운영자 지적,
# 2026-10-07). 처음에 빠르고 끝에서 느려지게(ease-out) 차오른다.
COUNTUP_STEPS = 30
# 카운트업이 끝난 뒤에도 제자리 숫자가 머물 시간이 남아야 한다.
COUNTUP_MIN_BEAT = 1.4
# 화면 이미지의 크기와 위치는 고정한다(운영자 결정 2026-09-27). 예전에는 정지 장면을
# 가장자리 8px 안에서 ±7px 흘렸는데, 화면 틀이 움직이는 것처럼 보였다.
_METRIC_NUMBER = re.compile(r"(\d+(?:\.\d+)?)(%?)$")


@dataclass(frozen=True)
class Phrase:
    """화면에 한 번에 뜨는 자막 한 덩어리."""

    start: float
    end: float
    text: str


def _phrases(
    narrations: Sequence[str], scenes: Sequence[Sequence[Word]], duration: float,
    *, phrase_chars: int = _PHRASE_CHARS,
) -> tuple[tuple[Phrase, ...], ...]:
    """장면 원고를 실제 단어 경계에 맞춰 자막 문구로 나눈다.

    시각은 전부 edge-tts가 보고한 단어 구간에서 온다 — 긴 문장을 구절로 쪼갤 때
    글자 수로 시간을 배분하던 추정이 없다. 그 추정은 문장 뒤 쉼(약 0.86초)까지
    포함한 창을 나눠서 분할점이 늘 뒤로 밀렸고, 뒷 구절이 말보다 0.4~1.0초 늦게
    떴다. 앞 구절은 그만큼 더 남아 다음 구절의 음성과 겹쳤다.

    문구는 자기 첫 단어보다 CAPTION_LEAD(장면의 첫 문구는 SCENE_LEAD)만큼 먼저
    떠서 다음 문구가 뜰 때까지 남는다. 그래서 빈틈도 겹침도 생기지 않는다.
    """
    marked: list[tuple[float, str]] = []
    counts: list[int] = []
    for narration, words in zip(narrations, scenes, strict=True):
        starts = locate(narration, words)
        # 한 단어가 차지하는 원고 구간은 다음 단어 직전까지다. 사이의 문장부호는 앞
        # 단어에 붙어 화면에 그대로 남고, 공백과 줄바꿈만 한 칸으로 줄어든다.
        ends = starts[1:] + [len(narration)]

        def text_of(first: int, last: int, starts=starts, ends=ends, narration=narration) -> str:
            return re.sub(r"\s+", " ", narration[starts[first]:ends[last]]).strip()

        def breakable(at: int, starts=starts, words=words, narration=narration) -> bool:
            """다음 단어와 사이에 공백이 있는가.

            edge-tts는 한 어절도 여러 단어로 돌려준다 — "25bp"가 "25"와 "bp"로
            나뉘어 왔다. 그 사이에서 자막을 끊으면 화면에 "…금리 25"와
            "bp 인상,"이 따로 뜬다.
            """
            between = narration[starts[at] + len(words[at].text):starts[at + 1]]
            return bool(re.search(r"\s", between))

        # 먼저 문장으로 끊는다. 한 문장이 한 문구로 다 들어가면 그대로 띄운다.
        sentences: list[list[int]] = []
        sentence: list[int] = []
        for index in range(len(words)):
            sentence.append(index)
            if text_of(index, index).endswith(_SENTENCE_END):
                sentences.append(sentence)
                sentence = []
        if sentence:
            sentences.append(sentence)

        groups: list[list[int]] = []
        for sentence in sentences:
            length = len(text_of(sentence[0], sentence[-1]))
            parts = max(1, -(-length // phrase_chars))
            if parts == 1 or len(sentence) < parts:
                groups.append(sentence)
                continue
            # 긴 문장은 균등하게 나눈다. 앞에서부터 한도까지 채우면 꼬리에
            # "분위기입니다." 한 조각만 남아 화면에 글자 몇 개가 덩그러니 뜬다.
            budget = length / parts
            group, cut = [], budget
            for index in sentence:
                group.append(index)
                if index == sentence[-1] or not breakable(index):
                    continue
                so_far = len(text_of(sentence[0], index))
                tail = text_of(group[0], index)
                if tail.endswith(_BINDING_END):
                    continue
                # 쉼표·연결어미는 말하는 사람이 이미 쉬는 자리다. 한도에 조금 못
                # 미쳐도 거기서 끊는 편이 "…전망일 뿐, 정해진"보다 자연스럽다.
                at_pause = ((tail.endswith(",") or _CLAUSE_END.search(tail))
                            and so_far >= cut - budget * .4)
                if so_far >= cut or at_pause:
                    groups.append(group)
                    group, cut = [], max(cut, so_far) + budget
            if group:
                groups.append(group)
        counts.append(len(groups))
        for position, group in enumerate(groups):
            lead = SCENE_LEAD if position == 0 else CAPTION_LEAD
            marked.append((max(0.0, words[group[0]].start - lead),
                           text_of(group[0], group[-1])))

    for (previous, _), (following, _) in zip(marked, marked[1:]):
        if following <= previous:
            raise RenderError("연속 음성에서 찾은 자막 시작 시각이 겹칩니다")
    stops = [start for start, _ in marked[1:]] + [duration]

    phrases: list[tuple[Phrase, ...]] = []
    position = 0
    for count in counts:
        phrases.append(tuple(
            Phrase(marked[position + n][0], stops[position + n], marked[position + n][1])
            for n in range(count)
        ))
        position += count
    return tuple(phrases)


def _scene_durations(scenes: Sequence[Sequence[Phrase]], duration: float) -> list[float]:
    """장면은 자기 첫 자막이 뜨는 순간 바뀐다 — 화면과 글자가 같이 넘어간다."""
    starts = [0.0] + [scene[0].start for scene in scenes[1:]]
    return [stop - start for start, stop in zip(starts, starts[1:] + [duration])]


def _caption_lines(draw, text: str, font: ImageFont.FreeTypeFont) -> list[str]:
    """자막 한 덩어리를 폭에 맞추되 줄 길이를 고르게 나눈다.

    앞줄을 끝까지 채우면 "…전망일" / "뿐," 처럼 뒷줄에 한 어절만 남는다. 줄 수가
    늘지 않는 선까지 폭을 좁혀 보면 같은 줄 수로 가장 고르게 나뉜 자리가 나온다.
    """
    lines = _wrap(draw, text, font, CAPTION_WIDTH)
    narrow = CAPTION_WIDTH
    while len(lines) > 1 and narrow > 160:
        candidate = _wrap(draw, text, font, narrow - 20)
        if len(candidate) != len(lines):
            break
        narrow -= 20
        lines = candidate
    return lines


def _caption_frame(lines: Sequence[str], path: Path, *, font_path: Path) -> None:
    """자막 한 덩어리를 화면 크기 투명 PNG로 그린다. 상자 아래 끝은 안전 영역 바닥이다."""
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(_bold(font_path), CAPTION_RENDER_SIZE)
    pitch = round(CAPTION_RENDER_SIZE * 1.34)
    # 줄마다 글자가 달라도 상자 높이가 흔들리지 않게 기준 글자로 잉크 높이를 잰다.
    _, ink_top, _, ink_bottom = draw.textbbox((0, 0), "한Ag", font=font)
    width = max(draw.textlength(line, font=font) for line in lines) + 2 * CAPTION_PAD_X
    height = (len(lines) - 1) * pitch + (ink_bottom - ink_top) + 2 * CAPTION_PAD_Y
    center = CAPTION_MARGIN_L + (WIDTH - CAPTION_MARGIN_L - CAPTION_MARGIN_R) / 2
    # Pillow 사각형은 끝 좌표 픽셀까지 칠한다. 상자의 마지막 줄이 SAFE_BOTTOM 바로 위다.
    box = (center - width / 2, SAFE_BOTTOM - height, center + width / 2, SAFE_BOTTOM - 1)
    draw.rounded_rectangle(box, radius=22, fill=CAPTION_BOX_RGBA)
    # 상자 높이는 줄 수로만 정하고(덩어리마다 상자가 출렁이지 않게), 글자는 실제 잉크 범위를 가운데에 둔다.
    top = draw.textbbox((0, 0), lines[0], font=font)[1]
    bottom = (len(lines) - 1) * pitch + draw.textbbox((0, 0), lines[-1], font=font)[3]
    y = box[1] + (box[3] + 1 - box[1] - (bottom - top)) / 2 - top
    for line in lines:
        draw.text((center - draw.textlength(line, font=font) / 2, y), line, font=font, fill=_COLORS["ink"])
        y += pitch
    image.save(path, "PNG", compress_level=1)


def _write_captions(scenes: Sequence[Sequence[Phrase]], path: Path, *, font_path: Path) -> None:
    """자막 파일. 줄바꿈은 여기서 어절 경계에 넣는다(`CAPTION_WIDTH` 참고)."""
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = _font(font_path, CAPTION_FONT_SIZE)
    blocks = [
        f"{index}\n{stamp(phrase.start)} --> {stamp(phrase.end)}\n"
        + "\n".join(_caption_lines(draw, phrase.text, font)) + "\n"
        for index, phrase in enumerate((p for scene in scenes for p in scene), start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8", newline="\n")


Beat = tuple[str, float, Scene, "int | None"]


def _ease(share: float) -> float:
    """처음에 빠르고 끝에서 느려지는 진행률(cubic ease-out)."""
    return 1 - (1 - share) ** 3


def _countup(scene: Scene, seconds: float, *, shown: int) -> list[Beat]:
    """새로 뜬 선택지의 확률이 0에서 제자리까지 차오르는 정지 프레임들.

    올라가는 동안에는 정수만 보여 준다 — 소수점 둘째 자리까지 흔들리면 읽히지
    않고 어지럽기만 하다. 확정된 값은 마지막 프레임에 한 번 제대로 선다.
    숫자로 읽히지 않는 수치나 짧은 구간은 그대로 한 장이다.
    """
    beat = f"option-{shown}"
    label, percent, probability = scene.options[shown - 1]
    match = _METRIC_NUMBER.fullmatch(percent.strip())
    if not match or seconds < COUNTUP_MIN_BEAT:
        return [(beat, seconds, scene, shown)]
    target, unit = float(match[1]), match[2]
    step = COUNTUP_SECONDS / COUNTUP_STEPS
    rising = [
        (beat, step, replace(scene, options=(
            scene.options[:shown - 1]
            + ((label, f"{target * _ease(n / COUNTUP_STEPS):.0f}{unit}", probability * _ease(n / COUNTUP_STEPS)),)
            + scene.options[shown:]
        )), shown)
        for n in range(COUNTUP_STEPS)
    ]
    return rising + [(beat, seconds - COUNTUP_SECONDS, scene, shown)]


def _beats(scene: Scene, seconds: float) -> list[Beat]:
    """한 장면을 화면이 바뀌는 순간들로 나눈다.

    선택지가 있는 장면은 질문만 선 화면으로 열고, 선택지가 말의 순서대로 하나씩
    쌓인다. 도입·마무리는 제목만 먼저 세운 뒤 문구를 얹는다 — 한 장을 20초씩
    그대로 두면 화면이 멈춘 것처럼 보인다.
    """
    if scene.options:
        # 확률을 말하기 시작하는 자리에서 선택지를 띄운다. 이유·질문이 먼저 나오므로
        # 고정 3.2초를 쓰면 말보다 화면이 앞서 간다. 비율이 없으면 예전 값.
        opening = (min(max(seconds * scene.options_at, 1.0), seconds * .8)
                   if scene.options_at else min(3.2, seconds * .35))
        beats: list[Beat] = [("question", opening, scene, 0)]
        share = (seconds - opening) / len(scene.options)
        for shown in range(1, len(scene.options) + 1):
            beats.extend(_countup(scene, share, shown=shown))
        return beats
    if seconds < 2 * COUNTUP_MIN_BEAT:
        return [("card", seconds, scene, None)]
    opening = min(2.2, seconds * .3)
    return [("open", opening, replace(scene, body=""), None), ("card", seconds - opening, scene, None)]


def render_video(
    scenario: Scenario,
    *,
    audio_path: Path,
    scene_words: Sequence[Sequence[Word]],
    output_path: Path,
    work_dir: Path,
    font_path: Path,
    blender_bin: str,
    ffprobe_bin: str,
    max_duration: float,
    background_paths: tuple[Path | None, ...] | None = None,
) -> float:
    # 목표 길이는 편집 참고값이다. 음성 전체와 마지막 여운을 먼저 보존한다.
    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + 0.6
    scene_phrases = _phrases([scene.narration for scene in scenario.scenes], scene_words, duration,
                             phrase_chars=_PHRASE_CHARS_BY_LANGUAGE[scenario.language])
    scene_durations = _scene_durations(scene_phrases, duration)
    captions = work_dir / "phrases.srt"
    _write_captions(scene_phrases, captions, font_path=font_path)
    images, movies, timeline = [], [], []
    selected = background_paths or tuple(None for _ in scenario.scenes)
    if len(selected) != len(scenario.scenes):
        raise RenderError("배경 수와 장면 수가 다릅니다")
    cursor, merging = 0.0, None
    for index, (scene, seconds) in enumerate(zip(scenario.scenes, scene_durations), start=1):
        background = selected[index - 1]
        is_clip = background is not None and background.suffix.lower() == ".mp4"
        if is_clip:
            tone = _COLORS.get(scene.accent, _COLORS["gold"]).lstrip("#")
            movies.append({"path": str(background.resolve()), "start": cursor, "duration": seconds,
                           "multiply": [1 - _TONE_STRENGTH + _TONE_STRENGTH * int(tone[n:n + 2], 16) / 255
                                        for n in (0, 2, 4)],
                           "brightness": (_BRIGHTNESS[index % len(_BRIGHTNESS)] - 1) * .2})
        for position, (beat, hold, display_scene, shown) in enumerate(_beats(scene, seconds), start=1):
            frame = work_dir / f"frame-{index:02d}-{position:02d}-{beat}.png"
            render_frame(display_scene, frame, font_path=font_path, index=index, total=len(scenario.scenes),
                         background_path=background, shown=shown, transparent=is_clip,
                         language=scenario.language)
            images.append({"path": str(frame.resolve()), "start": cursor, "duration": hold})
            # 카운트업은 한 프레임씩 기록하지 않는다 — 검수자가 보는 것은 수치가
            # 머무는 구간이지 그 안의 정지 화면 여덟 장이 아니다. 앞 장면과 제목이
            # 같을 수 있으므로(도입 제목 = 첫 이슈 제목) 장면 번호로 구분한다.
            if merging == (index, beat):
                timeline[-1]["duration"] = round(timeline[-1]["duration"] + hold, 3)
            else:
                timeline.append({"start": round(cursor, 3), "duration": round(hold, 3),
                                 "scene": scene.title, "beat": beat})
            merging = (index, beat)
            cursor += hold
    from .blender_render import compose

    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = _font(font_path, CAPTION_FONT_SIZE)
    subtitles = []
    for number, phrase in enumerate((phrase for group in scene_phrases for phrase in group), start=1):
        caption = work_dir / f"caption-{number:03d}.png"
        _caption_frame(_caption_lines(draw, phrase.text, font), caption, font_path=font_path)
        subtitles.append({"start": phrase.start, "end": phrase.end, "path": str(caption.resolve())})
    compose(images=images, movies=movies, subtitles=subtitles, audio_path=audio_path,
            output_path=output_path, work_dir=work_dir, duration=duration, blender_bin=blender_bin)
    output_path.with_suffix(".timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    return duration
