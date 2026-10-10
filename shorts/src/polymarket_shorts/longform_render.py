"""롱폼(가로 1920×1080) 화면. 쇼츠 화면(`render.py`)과 같은 색·글꼴·바탕을 쓰고 배치만 가로로 다시 짠다.

화면은 넷으로 나뉜다. 왼쪽 큰 칸이 지금 장면의 내용, 오른쪽 좁은 칸이 목차, 그 아래 띠가 자막, 위아래 끝 띠가
머리와 고지다. 내용 칸은 지금 말하는 대상(기사 한 줄, 분석의 주제 하나)을 밝게 짚고, 말은 아래 자막으로 읽힌다
(운영자 수정 대사 2026-10-10: 보고서는 주제를 화면에, 내용을 하단 자막에). 도입·마무리는 쇼츠와 같이 자막 없는
고정 화면이다.

장면이 바뀌는 때, 줄을 짚는 때, 자막이 바뀌는 때는 전부 edge-tts가 보고한 단어 시각에서 온다(`render._phrases`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter

from .longform import Longform, Segment
from .render import (
    _BACKDROP_BOTTOM, _BACKDROP_CENTER, _BACKDROP_TOP, _COLORS, _HIGHLIGHT, CAPTION_LEAD, SCENE_LEAD,
    RenderError, _bold, _font, _glow, _ink_text, _mono, _phrases, _rgb, _wrap, probe_duration,
)
from .tts import Word, locate


WIDTH, HEIGHT = 1920, 1080
MARGIN = 96
HEADER_Y = 52
CONTENT_TOP, CONTENT_BOTTOM = 150, 830
CONTENT_LEFT, CONTENT_RIGHT = MARGIN, 1380
PANEL_LEFT, PANEL_RIGHT = 1452, WIDTH - MARGIN
BODY_TOP = CONTENT_TOP + 76            # 장면 제목 아래
CAPTION_BOTTOM = 972                   # 자막 마지막 줄의 아래 끝
CAPTION_SIZE = 42
CAPTION_WIDTH = WIDTH - 2 * MARGIN - 160
# 자막 한 덩어리의 글자 수. 가로 1,568px에 42px 굵은 글씨 두 줄이 넉넉히 든다.
PHRASE_CHARS = 64
FOOTER_Y = 1012
_DIM = "#4F5B6A"                       # 짚지 않은 줄
_ACCENT = _COLORS["brand"]
UNCAPTIONED = ("intro", "outro")
_CHAPTERED = ("sentiment", "headlines", "report")


@lru_cache(maxsize=1)
def _backdrop() -> Image.Image:
    """쇼츠와 같은 짙은 남색 세로 그라데이션에 가운데가 조금 밝은 바탕."""
    base = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(base)
    for y in range(HEIGHT):
        share = y / (HEIGHT - 1)
        draw.line((0, y, WIDTH, y), fill=tuple(round(a + (b - a) * share)
                                               for a, b in zip(_BACKDROP_TOP, _BACKDROP_BOTTOM)))
    glow = Image.new("RGBA", (WIDTH // 4, HEIGHT // 4), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((40, -20, WIDTH // 4 - 40, HEIGHT // 4 + 20), fill=(*_BACKDROP_CENTER, 150))
    glow = glow.filter(ImageFilter.GaussianBlur(40)).resize((WIDTH, HEIGHT), Image.Resampling.BICUBIC)
    return Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")


def _chrome(draw, longform: Longform, font_path: Path) -> None:
    """위 띠(터미널 꼴 머리와 보고서 시각)와 아래 띠(고지·주소)."""
    mono = _mono(30, font_path)
    x = MARGIN
    for text, color in (("~/nunchi ", _COLORS["muted"]), ("report", _ACCENT), (f"  {longform.market}", _COLORS["ink"])):
        draw.text((x, HEADER_Y), text, font=mono, fill=color)
        x += draw.textlength(text, font=mono)
    published = datetime.fromisoformat(longform.published_at)
    stamp = f"{longform.label} 시장상황 보고서 · {published:%Y-%m-%d %H:%M} 발행 · 구간 {longform.window}"
    small = _font(font_path, 26)
    draw.text((WIDTH - MARGIN - draw.textlength(stamp, font=small), HEADER_Y + 4), stamp, font=small,
              fill=_COLORS["muted"])
    draw.line((MARGIN, HEADER_Y + 58, WIDTH - MARGIN, HEADER_Y + 58), fill=_COLORS["line"], width=2)
    note = "자체 수집 뉴스 기반 시장 요약 · 투자 조언 아님"
    draw.text((MARGIN, FOOTER_Y), note, font=_font(font_path, 22), fill=_COLORS["muted"])
    site = _font(_bold(font_path), 24)
    draw.text((WIDTH - MARGIN - draw.textlength("nunchi.live", font=site), FOOTER_Y - 2), "nunchi.live",
              font=site, fill=_ACCENT)


def _contents(image, longform: Longform, current: int, font_path: Path) -> None:
    """오른쪽 목차. 지나온 장면은 흐리게, 지금 장면은 강조색 막대와 굵은 글씨로."""
    draw = ImageDraw.Draw(image)
    box = (PANEL_LEFT, CONTENT_TOP, PANEL_RIGHT, CONTENT_BOTTOM)
    draw.rounded_rectangle(box, radius=24, fill=(*_rgb(_COLORS["panel"]), 235), outline=_COLORS["line"], width=2)
    draw.text((PANEL_LEFT + 36, CONTENT_TOP + 32), "목차", font=_font(_bold(font_path), 30), fill=_COLORS["muted"])
    y = CONTENT_TOP + 104
    for index, segment in enumerate(longform.segments):
        if segment.kind not in _CHAPTERED:
            continue
        active = index == current
        font = _font(_bold(font_path) if active else font_path, 32 if active else 30)
        color = _COLORS["ink"] if active else (_COLORS["muted"] if index < current else _DIM)
        if active:
            draw.rounded_rectangle((PANEL_LEFT + 20, y - 12, PANEL_RIGHT - 20, y + 54), radius=14,
                                   fill=(*_rgb(_ACCENT), 34))
            draw.rounded_rectangle((PANEL_LEFT + 20, y - 12, PANEL_LEFT + 28, y + 54), radius=4, fill=_ACCENT)
        draw.text((PANEL_LEFT + 52, y), segment.chapter, font=font, fill=color)
        y += 84


def _scene_title(draw, text: str, font_path: Path, note: str = "") -> None:
    font = _font(_bold(font_path), 44)
    draw.text((CONTENT_LEFT, CONTENT_TOP), text, font=font, fill=_COLORS["ink"], stroke_width=1,
              stroke_fill=_COLORS["ink"])
    if note:
        x = CONTENT_LEFT + draw.textlength(text, font=font) + 24
        draw.text((x, CONTENT_TOP + 14), note, font=_font(font_path, 28), fill=_ACCENT)


def _agenda(image, segment: Segment, font_path: Path) -> None:
    """목차 장면. 영상이 다룰 차례를 화면 가운데에 크게 세운다."""
    draw = ImageDraw.Draw(image)
    label = _font(_bold(font_path), 34)
    item_font, number_font = _font(_bold(font_path), 64), _font(_bold(font_path), 44)
    pitch = 128
    height = 70 + pitch * len(segment.rows)
    top = (CONTENT_TOP + CONTENT_BOTTOM) / 2 - height / 2
    widest = max(draw.textlength(row["item"], font=item_font) for row in segment.rows) + 120
    left = (WIDTH - widest) / 2
    draw.text((left, top), "이번 영상 순서", font=label, fill=_COLORS["muted"])
    y = top + 70
    for number, row in enumerate(segment.rows, start=1):
        badge = (left, y + 6, left + 76, y + 82)
        draw.rounded_rectangle(badge, radius=18, fill=_ACCENT)
        _ink_text(draw, badge, str(number), number_font, _COLORS["on_accent"], stroke=1)
        draw.text((left + 120, y), row["item"], font=item_font, fill=_COLORS["ink"])
        y += pitch


def _sentiment(image, segment: Segment, font_path: Path, label: str) -> None:
    """최근 며칠의 일일 감성 막대. 0 위는 초록, 아래는 빨강이고 값과 날짜를 단다."""
    draw = ImageDraw.Draw(image)
    rows = segment.rows
    _scene_title(draw, f"최근 {len(rows)}일 {label} 뉴스 감성", font_path, "일일 평균 · -1 ~ 1")
    top, bottom = BODY_TOP + 20, 610
    left, right = CONTENT_LEFT + 20, CONTENT_RIGHT - 20
    scale = max(.25, max(abs(row["value"]) for row in rows))
    zero = (top + bottom) / 2
    half = (bottom - top) / 2 - 36
    draw.line((left, zero, right, zero), fill=_COLORS["line"], width=2)
    slot = (right - left) / len(rows)
    value_font, date_font = _font(_bold(font_path), 22), _font(font_path, 22)
    for position, row in enumerate(rows):
        center = left + slot * (position + .5)
        height = half * row["value"] / scale
        color = _COLORS["green"] if row["value"] >= 0 else _COLORS["red"]
        bar = (center - slot * .3, min(zero, zero - height), center + slot * .3, max(zero, zero - height))
        draw.rounded_rectangle(bar, radius=6, fill=color)
        value = f"{row['value']:+.2f}"
        y = bar[1] - 30 if row["value"] >= 0 else bar[3] + 6
        draw.text((center - draw.textlength(value, font=value_font) / 2, y), value, font=value_font, fill=color)
        stamp = f"{datetime.strptime(row['date'], '%Y-%m-%d'):%m/%d}"
        draw.text((center - draw.textlength(stamp, font=date_font) / 2, bottom + 12), stamp, font=date_font,
                  fill=_COLORS["muted"])
    values = [row["value"] for row in rows]
    high = max(rows, key=lambda row: row["value"])
    low = min(rows, key=lambda row: row["value"])
    chips = (f"평균 {sum(values) / len(values):+.2f}",
             f"최고 {high['date'][5:].replace('-', '/')} {high['value']:+.2f}",
             f"최저 {low['date'][5:].replace('-', '/')} {low['value']:+.2f}")
    chip_font = _font(_bold(font_path), 28)
    x, y = CONTENT_LEFT + 20, bottom + 70
    for chip in chips:
        width = draw.textlength(chip, font=chip_font) + 48
        draw.rounded_rectangle((x, y, x + width, y + 60), radius=14, fill=(255, 255, 255, 20),
                               outline=_COLORS["line"], width=2)
        _ink_text(draw, (x, y, x + width, y + 60), chip, chip_font, _COLORS["ink"])
        x += width + 18


def _highlight_box(image, box, *, radius: int = 18) -> ImageDraw.ImageDraw:
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(box, radius=radius, fill=(*_rgb(_COLORS["panel"]), 245))
    _glow(image, lambda layer: layer.rounded_rectangle(box, radius=radius, outline=_ACCENT, width=3),
          radius=10, strength=.6)
    return ImageDraw.Draw(image)


def _headlines(image, segment: Segment, focus: int | None, font_path: Path) -> None:
    """그 구간 주요 기사 목록. 지금 말하는 기사 줄을 카드로 띄운다."""
    draw = ImageDraw.Draw(image)
    rows = segment.rows
    _scene_title(draw, "이번 구간 주요 기사", font_path, f"{len(rows)}건 · 최신순")
    gap = 10
    pitch = min(96, (CONTENT_BOTTOM - BODY_TOP - gap * (len(rows) - 1)) // len(rows))
    title_font, bold_font = _font(font_path, 30), _font(_bold(font_path), 30)
    meta_font, badge_font = _font(font_path, 20), _font(_bold(font_path), 26)
    y = BODY_TOP
    for position, row in enumerate(rows):
        now = position == focus
        if now:
            draw = _highlight_box(image, (CONTENT_LEFT, y, CONTENT_RIGHT, y + pitch), radius=16)
        badge = (CONTENT_LEFT + 20, y + (pitch - 46) / 2, CONTENT_LEFT + 66, y + (pitch + 46) / 2)
        draw.rounded_rectangle(badge, radius=10, fill=_ACCENT if now else _COLORS["track"])
        _ink_text(draw, badge, str(position + 1), badge_font, _COLORS["on_accent"] if now else _COLORS["muted"])
        width = CONTENT_RIGHT - CONTENT_LEFT - 120
        title, font = row["title"], bold_font if now else title_font
        while draw.textlength(title, font=font) > width and len(title) > 4:
            title = title[:-2] + "…"
        color = _COLORS["ink"] if now or focus is None else _DIM
        draw.text((CONTENT_LEFT + 92, y + pitch / 2 - 34), title, font=font, fill=color)
        meta = " · ".join(part for part in (row["source"], row["time"]) if part)
        draw.text((CONTENT_LEFT + 92, y + pitch / 2 + 8), meta, font=meta_font,
                  fill=_ACCENT if now else _COLORS["muted"])
        y += pitch + gap


def _analysis(image, segment: Segment, focus: int | None, font_path: Path, label: str) -> None:
    """시장 분석 한 장면. 문단마다의 핵심 주제를 카드로 세우고 지금 말하는 주제를 크게 짚는다. 내용은 자막이 읽는다."""
    draw = ImageDraw.Draw(image)
    _scene_title(draw, "시장 분석", font_path, f"{label} 시장상황 보고서 요약")
    rows = segment.rows
    gap = 22
    pitch = min(176, (CONTENT_BOTTOM - BODY_TOP - gap * (len(rows) - 1)) // len(rows))
    number_font = _font(_bold(font_path), 40)
    y = BODY_TOP + 6
    for position, row in enumerate(rows):
        now = position == focus
        box = (CONTENT_LEFT, y, CONTENT_RIGHT, y + pitch)
        if now:
            draw = _highlight_box(image, box, radius=22)
        else:
            draw.rounded_rectangle(box, radius=22, outline=_COLORS["line"], width=2)
        badge = (CONTENT_LEFT + 32, y + (pitch - 64) / 2, CONTENT_LEFT + 96, y + (pitch + 64) / 2)
        draw.rounded_rectangle(badge, radius=14, fill=_ACCENT if now else _COLORS["track"])
        _ink_text(draw, badge, str(position + 1), number_font, _COLORS["on_accent"] if now else _COLORS["muted"],
                  stroke=1 if now else 0)
        size = 56 if now else 46
        font = _font(_bold(font_path), size)
        lines = _wrap(draw, row["topic"], font, CONTENT_RIGHT - CONTENT_LEFT - 180)
        line_pitch = round(size * 1.3)
        text_top = y + (pitch - line_pitch * len(lines)) / 2
        color = _COLORS["ink"] if now or focus is None else _DIM
        for line in lines:
            draw.text((CONTENT_LEFT + 132, text_top), line, font=font, fill=color)
            text_top += line_pitch
        y += pitch + gap


def _headline(image, lines: Sequence[tuple[str, str]], font_path: Path) -> None:
    """도입·마무리의 고정 머리말. 쇼츠 `render._headline`의 가로판이다 — 화면 가운데 크게 선다."""
    draw = ImageDraw.Draw(image)
    sizes = {"ink": 104, "accent": 140, "plain": 54}
    fonts = [(text, role, _font(font_path if role == "plain" else _bold(font_path), sizes[role]))
             for text, role in lines]
    pitch = [round(font.size * (1.4 if role == "accent" else 1.32)) for _, role, font in fonts]
    y = HEIGHT / 2 - sum(pitch) / 2
    for (text, role, font), step in zip(fonts, pitch):
        left = (WIDTH - draw.textlength(text, font=font)) / 2
        if role == "accent":
            _glow(image, lambda layer, text=text, font=font, left=left, y=y: layer.text(
                (left, y), text, font=font, fill=_ACCENT, stroke_width=2, stroke_fill=_ACCENT), radius=12, strength=.3)
            draw = ImageDraw.Draw(image)
        else:
            stroke = 2 if role == "ink" else 0
            draw.text((left, y), text, font=font, fill=_COLORS["ink"], stroke_width=stroke,
                      stroke_fill=_COLORS["ink"])
        y += step


def render_longform_frame(longform: Longform, index: int, focus: int | None, path: Path, *,
                          font_path: Path) -> None:
    segment = longform.segments[index]
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _chrome(draw, longform, font_path)
    published = datetime.fromisoformat(longform.published_at)
    if segment.kind == "intro":
        _headline(image, ((f"{longform.label} 시장상황 보고서", "ink"),
                          (f"{published.month}월 {published.day}일 {published:%H:%M}", "accent"),
                          ("지금 시작합니다", "plain")), font_path)
    elif segment.kind == "outro":
        _headline(image, (("자세한 내용은", "ink"), ("nunchi.live", "accent"), ("에서 확인하세요", "plain")), font_path)
    elif segment.kind == "agenda":
        _agenda(image, segment, font_path)
    else:
        _contents(image, longform, index, font_path)
        if segment.kind == "sentiment":
            _sentiment(image, segment, font_path, longform.label)
        elif segment.kind == "headlines":
            _headlines(image, segment, focus, font_path)
        else:
            _analysis(image, segment, focus, font_path, longform.label)
    Image.alpha_composite(_backdrop().convert("RGBA"), image).convert("RGB").save(path, "PNG", compress_level=1)


def caption_frame(text: str, path: Path, *, font_path: Path) -> None:
    """자막 한 덩어리를 화면 크기 투명 PNG로 그린다. 굵은 흰 글씨 두 줄까지, 숫자는 강조색이다(쇼츠 자막과 같다)."""
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = _font(_bold(font_path), CAPTION_SIZE)
    lines = _wrap(draw, text, font, CAPTION_WIDTH)
    if len(lines) > 2:
        font = _font(_bold(font_path), CAPTION_SIZE - 6)
        lines = _wrap(draw, text, font, CAPTION_WIDTH)
    pitch = round(font.size * 1.45)
    y = CAPTION_BOTTOM - pitch * (len(lines) - 1) - draw.textbbox((0, 0), lines[-1], font=font)[3]
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    for line in lines:
        x = (WIDTH - draw.textlength(line, font=font)) / 2
        shadow_draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 190))
        cursor = 0
        for match in _HIGHLIGHT.finditer(line):
            for part, color in ((line[cursor:match.start()], _COLORS["ink"]), (match.group(), _ACCENT)):
                draw.text((x, y), part, font=font, fill=color)
                x += draw.textlength(part, font=font)
            cursor = match.end()
        draw.text((x, y), line[cursor:], font=font, fill=_COLORS["ink"])
        y += pitch
    Image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(3)), image).save(path, "PNG", compress_level=1)


@dataclass(frozen=True)
class Beat:
    segment: int
    focus: int | None
    start: float
    end: float


def beats(longform: Longform, segment_words: Sequence[Sequence[Word]], duration: float) -> list[Beat]:
    """화면이 바뀌는 순간들. 문장이 시작될 때 그 문장이 짚는 줄로 바뀌고, 같은 줄이 이어지면 한 박자로 묶는다.

    문장은 자기 첫 단어보다 CAPTION_LEAD 먼저, 장면은 SCENE_LEAD 먼저 바뀐다(자막과 같은 규칙).
    """
    marks: list[tuple[int, int | None, float]] = []
    for index, (segment, words) in enumerate(zip(longform.segments, segment_words, strict=True)):
        starts = locate(segment.narration, words)
        offset = 0
        for number, (sentence, focus) in enumerate(zip(segment.sentences, segment.marks)):
            first = next((n for n, at in enumerate(starts) if at >= offset), None)
            offset += len(sentence) + 1
            if first is None:
                raise RenderError(f"문장을 읽은 음성을 찾지 못했습니다: {sentence[:30]}")
            if number and marks[-1][:2] == (index, focus):
                continue
            lead = SCENE_LEAD if number == 0 else CAPTION_LEAD
            at = 0.0 if index == 0 and number == 0 else max(0.0, words[first].start - lead)
            marks.append((index, focus, at))
    for (_, _, previous), (_, _, following) in zip(marks, marks[1:]):
        if following <= previous:
            raise RenderError("화면 전환 시각이 겹칩니다")
    stops = [at for _, _, at in marks[1:]] + [duration]
    return [Beat(index, focus, at, stop) for (index, focus, at), stop in zip(marks, stops)]


def render_longform(longform: Longform, *, audio_path: Path, segment_words: Sequence[Sequence[Word]],
                    output_path: Path, work_dir: Path, font_path: Path, blender_bin: str, ffprobe_bin: str) -> float:
    from .blender_render import compose

    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + 0.6
    images, timeline, drawn = [], [], {}
    for beat in beats(longform, segment_words, duration):
        key = (beat.segment, beat.focus)
        if key not in drawn:
            frame = work_dir / f"frame-{beat.segment:02d}-{-1 if beat.focus is None else beat.focus:02d}.png"
            render_longform_frame(longform, beat.segment, beat.focus, frame, font_path=font_path)
            drawn[key] = frame
        images.append({"path": str(drawn[key].resolve()), "start": beat.start, "duration": beat.end - beat.start})
        timeline.append({"start": round(beat.start, 3), "duration": round(beat.end - beat.start, 3),
                         "segment": longform.segments[beat.segment].chapter, "focus": beat.focus})
    phrases = _phrases([segment.narration for segment in longform.segments], segment_words, duration,
                       phrase_chars=PHRASE_CHARS)
    subtitles, captions = [], []
    for segment, group in zip(longform.segments, phrases, strict=True):
        if segment.kind in UNCAPTIONED:
            continue
        for phrase in group:
            caption = work_dir / f"caption-{len(subtitles) + 1:03d}.png"
            caption_frame(phrase.text, caption, font_path=font_path)
            subtitles.append({"start": phrase.start, "end": phrase.end, "path": str(caption.resolve())})
            captions.append({"start": round(phrase.start, 3), "end": round(phrase.end, 3), "text": phrase.text})
    compose(images=images, subtitles=subtitles, audio_path=audio_path, output_path=output_path,
            work_dir=work_dir, duration=duration, blender_bin=blender_bin, size=(WIDTH, HEIGHT))
    output_path.with_suffix(".timeline.json").write_text(
        json.dumps({"screens": timeline, "captions": captions}, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")
    return duration
