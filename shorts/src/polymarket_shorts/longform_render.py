"""롱폼(가로 1920×1080) 화면. 쇼츠 화면(`render.py`)과 같은 색·글꼴·바탕을 쓰고 배치만 가로로 다시 짠다.

화면은 셋으로 나뉜다. 왼쪽 큰 칸이 지금 장면의 내용, 오른쪽 좁은 칸이 목차(지금 어디쯤인지), 위아래 띠가 머리와 고지다.
자막은 따로 띄우지 않는다 — 보고서·기사 장면은 지금 읽는 문장(줄)을 밝게 짚어 그 자체가 자막이 되고, 감성 장면만
그래프 아래에 지금 문장을 띄운다. 도입·마무리는 쇼츠와 같이 자막 없는 고정 화면이다(운영자 결정 2026-10-10).

장면이 바뀌는 때와 문장을 짚는 때는 전부 edge-tts가 보고한 단어 시각에서 온다(쇼츠 `render._phrases`와 같다).
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
    _BACKDROP_BOTTOM, _BACKDROP_CENTER, _BACKDROP_TOP, _COLORS, CAPTION_LEAD, SCENE_LEAD,
    RenderError, _bold, _font, _glow, _ink_text, _mono, _rgb, _wrap, probe_duration,
)
from .tts import Word, locate


WIDTH, HEIGHT = 1920, 1080
MARGIN = 96
HEADER_Y = 52
CONTENT_TOP, CONTENT_BOTTOM = 150, 968
CONTENT_LEFT, CONTENT_RIGHT = MARGIN, 1380
PANEL_LEFT, PANEL_RIGHT = 1452, WIDTH - MARGIN
FOOTER_Y = 1006
BODY_TOP = CONTENT_TOP + 76            # 장면 제목 아래
_DIM = "#4F5B6A"                       # 아직 읽지 않은 줄
_ACCENT = _COLORS["brand"]
_UNCAPTIONED = ("intro", "outro")


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
    note = "눈치 시장상황 보고서 · 수집한 뉴스의 요약 · 투자 조언 아님"
    draw.text((MARGIN, FOOTER_Y), note, font=_font(font_path, 24), fill=_COLORS["muted"])
    site = _font(_bold(font_path), 26)
    draw.text((WIDTH - MARGIN - draw.textlength("nunchi.live", font=site), FOOTER_Y - 2), "nunchi.live",
              font=site, fill=_ACCENT)


def _contents(image, longform: Longform, current: int, font_path: Path) -> None:
    """오른쪽 목차. 지나온 장면은 흐리게, 지금 장면은 강조색 막대와 굵은 글씨로."""
    draw = ImageDraw.Draw(image)
    box = (PANEL_LEFT, CONTENT_TOP, PANEL_RIGHT, CONTENT_BOTTOM)
    draw.rounded_rectangle(box, radius=24, fill=(*_rgb(_COLORS["panel"]), 235), outline=_COLORS["line"], width=2)
    draw.text((PANEL_LEFT + 36, CONTENT_TOP + 32), "목차", font=_font(_bold(font_path), 30), fill=_COLORS["muted"])
    chapters = [(index, segment) for index, segment in enumerate(longform.segments) if segment.kind not in _UNCAPTIONED]
    pitch = min(84, (CONTENT_BOTTOM - CONTENT_TOP - 120) // max(1, len(chapters)))
    y = CONTENT_TOP + 104
    for index, segment in chapters:
        active = index == current
        font = _font(_bold(font_path) if active else font_path, 32 if active else 30)
        color = _COLORS["ink"] if active else (_COLORS["muted"] if index < current else _DIM)
        if active:
            draw.rounded_rectangle((PANEL_LEFT + 20, y - 10, PANEL_RIGHT - 20, y + pitch - 26), radius=14,
                                   fill=(*_rgb(_ACCENT), 34))
            draw.rounded_rectangle((PANEL_LEFT + 20, y - 10, PANEL_LEFT + 28, y + pitch - 26), radius=4, fill=_ACCENT)
        draw.text((PANEL_LEFT + 52, y), segment.chapter, font=font, fill=color)
        y += pitch


def _scene_title(draw, text: str, font_path: Path, note: str = "") -> None:
    font = _font(_bold(font_path), 44)
    draw.text((CONTENT_LEFT, CONTENT_TOP), text, font=font, fill=_COLORS["ink"], stroke_width=1,
              stroke_fill=_COLORS["ink"])
    if note:
        x = CONTENT_LEFT + draw.textlength(text, font=font) + 24
        draw.text((x, CONTENT_TOP + 14), note, font=_font(font_path, 28), fill=_ACCENT)


def _fit_blocks(draw, blocks: Sequence[str], font_path: Path, width: int, height: int, sizes: Sequence[int],
                gap: float = .55):
    """문장 묶음을 칸 높이에 모두 넣는 가장 큰 글자. 한 문장도 버리지 않는다."""
    for size in sizes:
        font = _font(font_path, size)
        wrapped = [_wrap(draw, block, font, width) for block in blocks]
        pitch = round(size * 1.5)
        total = sum(len(lines) * pitch for lines in wrapped) + round(size * gap) * (len(blocks) - 1)
        if total <= height:
            return size, pitch, wrapped
    raise RenderError("롱폼 화면 텍스트가 칸을 넘습니다: " + blocks[0][:60])


def _report(image, segment: Segment, active: int | None, font_path: Path, number: str) -> None:
    """보고서 한 문단. 문장마다 한 덩어리로 세우고 지금 읽는 문장만 밝게 짚는다."""
    draw = ImageDraw.Draw(image)
    _scene_title(draw, "보고서 본문", font_path, number)
    shown = segment.sentences[segment.lead:]
    focus = None if active is None or active < segment.lead else active - segment.lead
    width = CONTENT_RIGHT - CONTENT_LEFT - 40
    size, pitch, wrapped = _fit_blocks(draw, shown, font_path, width, CONTENT_BOTTOM - BODY_TOP - 10,
                                       range(46, 27, -2))
    regular, bold = _font(font_path, size), _font(_bold(font_path), size)
    y = BODY_TOP + 10
    for position, lines in enumerate(wrapped):
        now = position == focus
        color = _COLORS["ink"] if now else (_COLORS["muted"] if focus is not None and position < focus else _DIM)
        if focus is None:
            color = _COLORS["muted"]
        top = y
        for line in lines:
            draw.text((CONTENT_LEFT + 40, y), line, font=bold if now else regular, fill=color)
            y += pitch
        if now:
            draw.rounded_rectangle((CONTENT_LEFT, top + 6, CONTENT_LEFT + 8, y - pitch * .3), radius=4, fill=_ACCENT)
        y += round(size * .55)


def _caption(draw, text: str, font_path: Path) -> None:
    """감성 장면 아래의 지금 문장. 두 줄까지, 왼쪽 칸 가운데."""
    for size in range(42, 27, -2):
        font = _font(_bold(font_path), size)
        lines = _wrap(draw, text, font, CONTENT_RIGHT - CONTENT_LEFT - 40)
        if len(lines) <= 2:
            break
    pitch = round(size * 1.45)
    y = CONTENT_BOTTOM - pitch * len(lines) + 4
    for line in lines:
        x = CONTENT_LEFT + (CONTENT_RIGHT - CONTENT_LEFT - draw.textlength(line, font=font)) / 2
        draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=_COLORS["ink"])
        y += pitch


def _sentiment(image, segment: Segment, active: int | None, font_path: Path, label: str) -> None:
    """최근 며칠의 일일 감성 막대. 0 위는 초록, 아래는 빨강이고 값과 날짜를 단다."""
    draw = ImageDraw.Draw(image)
    rows = segment.rows
    _scene_title(draw, f"최근 {len(rows)}일 {label} 뉴스 감성", font_path, "일일 평균 · -1 ~ 1")
    top, bottom = BODY_TOP + 30, 700
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
        day = datetime.strptime(row["date"], "%Y-%m-%d")
        stamp = f"{day:%m/%d}"
        draw.text((center - draw.textlength(stamp, font=date_font) / 2, bottom + 12), stamp, font=date_font,
                  fill=_COLORS["muted"])
    values = [row["value"] for row in rows]
    high = max(rows, key=lambda row: row["value"])
    low = min(rows, key=lambda row: row["value"])
    chips = (f"평균 {sum(values) / len(values):+.2f}",
             f"최고 {high['date'][5:].replace('-', '/')} {high['value']:+.2f}",
             f"최저 {low['date'][5:].replace('-', '/')} {low['value']:+.2f}",
             f"최근일 기사 {rows[-1]['count']}건")
    chip_font = _font(_bold(font_path), 28)
    x, y = CONTENT_LEFT + 20, bottom + 64
    for chip in chips:
        width = draw.textlength(chip, font=chip_font) + 48
        draw.rounded_rectangle((x, y, x + width, y + 60), radius=14, fill=(255, 255, 255, 20),
                               outline=_COLORS["line"], width=2)
        _ink_text(draw, (x, y, x + width, y + 60), chip, chip_font, _COLORS["ink"])
        x += width + 18
    if active is not None:
        _caption(draw, segment.sentences[active], font_path)


def _headlines(image, segment: Segment, active: int | None, font_path: Path) -> None:
    """그 구간 주요 기사 목록. 지금 읽는 줄을 카드로 띄운다."""
    draw = ImageDraw.Draw(image)
    rows = segment.rows
    _scene_title(draw, "이번 구간 주요 기사", font_path, f"{len(rows)}건 · 최신순")
    focus = None if active is None or active < segment.lead else active - segment.lead
    gap = 14
    pitch = min(112, (CONTENT_BOTTOM - BODY_TOP - gap * (len(rows) - 1)) // len(rows))
    title_font, bold_font = _font(font_path, 34), _font(_bold(font_path), 34)
    meta_font, badge_font = _font(font_path, 22), _font(_bold(font_path), 28)
    y = BODY_TOP
    for position, row in enumerate(rows):
        now = position == focus
        box = (CONTENT_LEFT, y, CONTENT_RIGHT, y + pitch)
        if now:
            draw.rounded_rectangle(box, radius=18, fill=(*_rgb(_COLORS["panel"]), 245))
            _glow(image, lambda layer, box=box: layer.rounded_rectangle(box, radius=18, outline=_ACCENT, width=3),
                  radius=10, strength=.6)
            draw = ImageDraw.Draw(image)
        badge = (CONTENT_LEFT + 22, y + (pitch - 52) / 2, CONTENT_LEFT + 74, y + (pitch + 52) / 2)
        draw.rounded_rectangle(badge, radius=12, fill=_ACCENT if now else _COLORS["track"])
        _ink_text(draw, badge, str(position + 1), badge_font, _COLORS["on_accent"] if now else _COLORS["muted"])
        width = CONTENT_RIGHT - CONTENT_LEFT - 130
        title = row["title"]
        font = bold_font if now else title_font
        while draw.textlength(title, font=font) > width and len(title) > 4:
            title = title[:-2] + "…"
        color = _COLORS["ink"] if now or focus is None else (_COLORS["muted"] if position < focus else _DIM)
        draw.text((CONTENT_LEFT + 100, y + pitch / 2 - 40), title, font=font, fill=color)
        meta = " · ".join(part for part in (row["source"], row["time"]) if part)
        draw.text((CONTENT_LEFT + 100, y + pitch / 2 + 8), meta, font=meta_font,
                  fill=_ACCENT if now else _COLORS["muted"])
        y += pitch + gap


def _headline(image, lines: Sequence[tuple[str, str]], font_path: Path) -> None:
    """도입·마무리의 고정 머리말. 쇼츠 `render._headline`의 가로판이다 — 화면 가운데 크게 선다."""
    draw = ImageDraw.Draw(image)
    sizes = {"ink": 104, "accent": 140, "plain": 54}
    fonts = [(text, role, _font(font_path if role == "plain" else _bold(font_path), sizes[role]))
             for text, role in lines]
    pitch = [round(font.size * (1.4 if role == "accent" else 1.32)) for _, role, font in fonts]
    y = (CONTENT_TOP + CONTENT_BOTTOM) / 2 - sum(pitch) / 2
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


def render_longform_frame(longform: Longform, index: int, active: int | None, path: Path, *,
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
    else:
        _contents(image, longform, index, font_path)
        if segment.kind == "sentiment":
            _sentiment(image, segment, active, font_path, longform.label)
        elif segment.kind == "headlines":
            _headlines(image, segment, active, font_path)
        else:
            reports = [n for n, row in enumerate(longform.segments) if row.kind == "report"]
            _report(image, segment, active, font_path, f"{reports.index(index) + 1} / {len(reports)}")
    Image.alpha_composite(_backdrop().convert("RGBA"), image).convert("RGB").save(path, "PNG", compress_level=1)


@dataclass(frozen=True)
class Beat:
    segment: int
    sentence: int | None
    start: float
    end: float


def beats(longform: Longform, segment_words: Sequence[Sequence[Word]], duration: float) -> list[Beat]:
    """문장마다 한 박자. 문장은 자기 첫 단어보다 CAPTION_LEAD 먼저, 장면은 SCENE_LEAD 먼저 바뀐다."""
    marks: list[tuple[int, int | None, float]] = []
    for index, (segment, words) in enumerate(zip(longform.segments, segment_words, strict=True)):
        starts = locate(segment.narration, words)
        offset = 0
        for number, sentence in enumerate(segment.sentences):
            first = next((n for n, at in enumerate(starts) if at >= offset), None)
            offset += len(sentence) + 1
            if first is None:
                raise RenderError(f"문장을 읽은 음성을 찾지 못했습니다: {sentence[:30]}")
            lead = SCENE_LEAD if number == 0 else CAPTION_LEAD
            at = 0.0 if index == 0 and number == 0 else max(0.0, words[first].start - lead)
            marks.append((index, None if segment.kind in _UNCAPTIONED else number, at))
    for (_, _, previous), (_, _, following) in zip(marks, marks[1:]):
        if following <= previous:
            raise RenderError("문장 시작 시각이 겹칩니다")
    stops = [at for _, _, at in marks[1:]] + [duration]
    return [Beat(index, sentence, at, stop) for (index, sentence, at), stop in zip(marks, stops)]


def render_longform(longform: Longform, *, audio_path: Path, segment_words: Sequence[Sequence[Word]],
                    output_path: Path, work_dir: Path, font_path: Path, blender_bin: str, ffprobe_bin: str) -> float:
    from .blender_render import compose

    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + 0.6
    images, timeline, drawn = [], [], {}
    for beat in beats(longform, segment_words, duration):
        key = (beat.segment, beat.sentence)
        if key not in drawn:
            frame = work_dir / f"frame-{beat.segment:02d}-{beat.sentence if beat.sentence is not None else 0:02d}.png"
            render_longform_frame(longform, beat.segment, beat.sentence, frame, font_path=font_path)
            drawn[key] = frame
        if images and images[-1]["path"] == str(drawn[key].resolve()):
            images[-1]["duration"] += beat.end - beat.start
        else:
            images.append({"path": str(drawn[key].resolve()), "start": beat.start, "duration": beat.end - beat.start})
        timeline.append({"start": round(beat.start, 3), "duration": round(beat.end - beat.start, 3),
                         "segment": longform.segments[beat.segment].chapter, "sentence": beat.sentence})
    compose(images=images, movies=[], subtitles=[], audio_path=audio_path, output_path=output_path,
            work_dir=work_dir, duration=duration, blender_bin=blender_bin, size=(WIDTH, HEIGHT))
    output_path.with_suffix(".timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return duration
