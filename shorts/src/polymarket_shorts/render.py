from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
import subprocess
import textwrap
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .scenario import Scenario, Scene
from .tts import Word, locate


WIDTH, HEIGHT = 1080, 1920
_COLORS = {
    "ink": "#F5F1E8",
    "muted": "#BDB6A8",
    "panel": "#24231F",
    "line": "#444039",
    "gold": "#D4A84F",
    "red": "#E0645C",
    "blue": "#5E8FC9",
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


# ── YouTube Shorts 안전 영역 ────────────────────────────
# 세로 1080x1920에서 플레이어 UI가 프레임을 덮는다. 위 ~180px은 검색·내비게이션,
# 아래 ~350px은 채널명·제목·음원 바, 오른쪽 ~192px은 좋아요·댓글·공유 버튼 줄이다.
# 그 밖에 그린 것은 실제 재생 화면에서 보이지 않는다 — 예전에는 고지문·출처·
# 페이지 번호·진행바가 전부 아래 220px 안에 있어 넷 다 가려져 있었다.
#
# 세로 순서는 본문 패널 → 고지문·출처 → 자막이다. 자막이 가장 아래이고
# 진행바는 헤더 밑줄 자리로 올라가 있다. SAFE_BOTTOM보다 더 내리면 자막이
# 채널명·제목 바에 가리므로 "최하단"은 여기까지다.
SAFE_TOP = 200
SAFE_BOTTOM = HEIGHT - 380              # 1540. 이 아래는 Shorts UI 구역
PANEL_BOTTOM_MAX = SAFE_BOTTOM - 240    # 1300. 본문 패널의 바닥
FOOTER_Y = PANEL_BOTTOM_MAX + 40        # 1340. 패널 아래, 자막 위
CAPTION_MARGIN_V = HEIGHT - SAFE_BOTTOM  # 380. 자막 아래 끝을 안전 영역 바닥에 붙인다
PROGRESS_Y = 677                        # 헤더 밑줄 자리. 예전에는 1580이었다
SAFE_LEFT = 82
SAFE_RIGHT = WIDTH - 200

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def _background(path: Path | None) -> Image.Image:
    if path is None or not path.is_file():
        return Image.new("RGB", (WIDTH, HEIGHT), "#151512")
    with Image.open(path) as source:
        image = ImageOps.fit(source.convert("RGB"), (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
    # Reframe the lower, illustrated part of the saved asset into the visible hero area.
    hero = ImageOps.fit(image.crop((0, 1050, WIDTH, HEIGHT)), (WIDTH, 850))
    image = Image.new("RGB", (WIDTH, HEIGHT), "#101B20")
    image.paste(ImageEnhance.Brightness(hero).enhance(0.65), (0, 0))
    return image


def _text_block(draw, text, font_path, box, *, size=48, color="#F5F1E8"):
    """Fit all text inside a bounded box; never silently discard lines."""
    x, y, right, bottom = box
    for candidate in range(size, 25, -2):
        font = _font(font_path, candidate)
        lines = _wrap(draw, text, font, right - x)
        spacing = round(candidate * 1.4)
        if len(lines) * spacing <= bottom - y:
            for line in lines:
                draw.text((x, y), line, font=font, fill=color)
                y += spacing
            return
    raise RenderError("화면 텍스트가 안전 영역을 넘습니다: " + text[:70])


def render_frame(
    scene: Scene,
    path: Path,
    *,
    font_path: Path,
    index: int,
    total: int,
    background_path: Path | None = None,
    beat: str = "detail",
    transparent: bool = False,
) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    accent = _COLORS.get(scene.accent, _COLORS["gold"])
    # Photographic upper half; opaque editorial lower half protects legibility.
    draw.rectangle((0, 720, WIDTH, HEIGHT), fill="#101B20")
    for y in range(520, 720):
        draw.line((0, y, WIDTH, y), fill=(16, 27, 32, int((y - 520) / 200 * 255)))
    draw.rounded_rectangle((72, 190, 195, 226), radius=7, fill=accent)
    draw.text((87, 193), "NUNCHI", font=_font(font_path, 22), fill="#101B20")
    draw.text((216, 193), "MARKET NOTES", font=_font(font_path, 23), fill="#F5F1E8")
    draw.text((72, 258), scene.kicker, font=_font(font_path, 24), fill=accent)
    _text_block(draw, scene.title, font_path, (72, 322, 878, 610), size=78)
    draw.text((72, 657), f"{index:02d} / {total:02d}", font=_font(font_path, 26), fill="#F5F1E8")
    # 진행 상태바. 화면 아래 끝은 자막 자리라 페이지 번호 옆으로 올렸다.
    for slot in range(total):
        left = 210 + slot * (668 / total)
        draw.rectangle((left, PROGRESS_Y, left + 668 / total - 8, PROGRESS_Y + 5),
                       fill=accent if slot < index else "#354348")

    labels = dict(b.partition(" · ")[::2] for b in scene.bullets if " · " in b)
    if beat == "metric" or scene.kind in {"intro", "outro"}:
        draw.rounded_rectangle((72, 754, 878, 1050), radius=22, fill="#F0EDE4")
        _text_block(draw, scene.metric_label or "MARKET SNAPSHOT", font_path, (108, 788, 836, 854), size=27, color="#455057")
        _text_block(draw, scene.metric or labels.get("24시간 거래량", "CHECK"), font_path, (100, 860, 836, 1035), size=144, color="#101B20")
        if scene.kind != "outro":
            draw.rounded_rectangle((72, 1084, 878, 1098), radius=6, fill="#354348")
            if scene.volume_share > 0:
                draw.rectangle((72, 1084, 72 + round(806 * min(1, scene.volume_share)), 1098), fill=accent)
            note = labels.get("이벤트", "") + (" 이벤트 / " if labels.get("이벤트") else "")
            note += f"선정 분야 거래 중 {scene.volume_share:.1%}"
            draw.text((72, 1120), note, font=_font(font_path, 25), fill="#AEBCC1")
        _text_block(draw, scene.takeaway or scene.body, font_path, (72, 1184, 878, 1318), size=42)
    else:
        draw.text((72, 760), "무엇을 예상하나", font=_font(font_path, 29), fill=accent)
        _text_block(draw, scene.body, font_path, (72, 826, 878, 1174), size=44)
        draw.line((72, 1204, 878, 1204), fill="#354348", width=2)
        _text_block(draw, f"{scene.metric} USD  /  이벤트 {labels.get('이벤트', '-')}",
                    font_path, (72, 1224, 878, 1310), size=30, color="#AEBCC1")

    draw.text((72, FOOTER_Y), "Polymarket / 예측시장 가격 · 투자 조언 아님",
              font=_font(font_path, 22), fill="#AEBCC1")
    draw.text((72, FOOTER_Y + 37), scene.source_note + (" / AI 배경" if background_path else ""),
              font=_font(font_path, 21), fill=accent)
    if not transparent:
        image = Image.alpha_composite(_background(background_path).convert("RGBA"), image).convert("RGB")
    image.save(path, "PNG")


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


def _concat_file(frames: Iterable[Path], durations: Iterable[float], target: Path) -> None:
    lines: list[str] = []
    frame_list = list(frames)
    for frame, duration in zip(frame_list, durations, strict=True):
        safe = frame.resolve().as_posix().replace("'", "'\\''")
        lines.extend((f"file '{safe}'", f"duration {duration:.3f}"))
    safe_last = frame_list[-1].resolve().as_posix().replace("'", "'\\''")
    lines.append(f"file '{safe_last}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _subtitle_filter(path: Path, font_name: str = "Noto Sans CJK KR") -> str:
    escaped = path.resolve().as_posix().replace(":", "\\:").replace("'", "\\'")
    style = (
        f"PlayResX={WIDTH},PlayResY={HEIGHT},FontName={font_name},FontSize=38,PrimaryColour=&H00F5F1E8,"
        "OutlineColour=&H00151512,BorderStyle=1,Outline=3,Shadow=0,"
        # MarginV는 아래 가장자리로부터의 거리다. 이 값이면 자막의 아래 끝이
        # SAFE_BOTTOM(1540)에 닿는다 — Shorts UI에 가리지 않는 가장 아래다.
        f"Alignment=2,MarginV={CAPTION_MARGIN_V},MarginL=110,MarginR=230"
    )
    return f"subtitles='{escaped}':force_style='{style}'"


# 자막은 자기 첫 단어보다 이만큼 먼저 뜬다. edge-tts의 문장 큐가 쓰던 값과 같다.
CAPTION_LEAD = 0.05
# 장면이 바뀔 때는 더 일찍 넘긴다. tts.SCENE_PAUSE_SECONDS로 넓혀 둔 쉼의 뒤쪽
# 이만큼이 새 화면 위에서 흐르므로, 화면이 먼저 자리를 잡은 뒤에 말이 시작된다.
SCENE_LEAD = 0.55
_PHRASE_CHARS = 25
_SENTENCE_END = (".", "?", "!")


@dataclass(frozen=True)
class Phrase:
    """화면에 한 번에 뜨는 자막 한 덩어리."""

    start: float
    end: float
    text: str


def _phrases(
    narrations: Sequence[str], scenes: Sequence[Sequence[Word]], duration: float,
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

        groups: list[list[int]] = []
        group: list[int] = []
        for index in range(len(words)):
            if group and (len(text_of(group[0], index)) > _PHRASE_CHARS
                          or text_of(group[-1], group[-1]).endswith(_SENTENCE_END)):
                groups.append(group)
                group = []
            group.append(index)
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


def _write_captions(scenes: Sequence[Sequence[Phrase]], path: Path) -> None:
    def stamp(seconds: float) -> str:
        ms = round(seconds * 1000)
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

    blocks = [
        f"{index}\n{stamp(phrase.start)} --> {stamp(phrase.end)}\n{phrase.text}\n"
        for index, phrase in enumerate((p for scene in scenes for p in scene), start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")


def render_video(
    scenario: Scenario,
    *,
    audio_path: Path,
    scene_words: Sequence[Sequence[Word]],
    output_path: Path,
    work_dir: Path,
    font_path: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    max_duration: float,
    background_paths: tuple[Path | None, ...] | None = None,
) -> float:
    # 목표 길이는 편집 참고값이다. 음성 전체와 마지막 여운을 먼저 보존한다.
    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin) + 0.6
    scene_phrases = _phrases([scene.narration for scene in scenario.scenes], scene_words, duration)
    scene_durations = _scene_durations(scene_phrases, duration)
    captions = work_dir / "phrases.srt"
    _write_captions(scene_phrases, captions)
    frames, backgrounds, holds, timeline = [], [], [], []
    selected = background_paths or tuple(None for _ in scenario.scenes)
    if len(selected) != len(scenario.scenes):
        raise RenderError("배경 수와 장면 수가 다릅니다")
    cursor = 0.0
    for index, (scene, seconds) in enumerate(zip(scenario.scenes, scene_durations), start=1):
        background = work_dir / f"background-{index:02d}.png"
        _background(selected[index - 1]).save(background)
        opening = min(3.0, seconds * .35)
        # 편집자가 지정한 문장·수치 줄바꿈은 화면에서도 보존한다.
        passages, current = [], ""
        for paragraph in scene.body.splitlines():
            for part in textwrap.wrap(paragraph, width=72, break_long_words=False, break_on_hyphens=False):
                if current and len(current) + len(part) + 1 > 72:
                    passages.append(current)
                    current = ""
                current = f"{current}\n{part}".strip()
        if current:
            passages.append(current)
        passages = passages or [scene.body]
        weight = sum(len(p) for p in passages)
        beats = [("metric", opening, scene)] + [
            (f"detail-{part + 1}", (seconds - opening) * len(passage) / max(1, weight), replace(scene, body=passage))
            for part, passage in enumerate(passages)
        ]
        if scene.kind != "consensus":
            beats = [("metric", seconds, scene)]
        for beat, hold, display_scene in beats:
            frame = work_dir / f"frame-{index:02d}-{beat}.png"
            render_frame(display_scene, frame, font_path=font_path, index=index, total=len(scenario.scenes),
                         background_path=selected[index - 1], beat=beat, transparent=True)
            frames.append(frame)
            backgrounds.append(background)
            holds.append(hold)
            timeline.append({"start": round(cursor, 3), "duration": round(hold, 3),
                             "scene": scene.title, "beat": beat})
            cursor += hold
    foreground_concat, background_concat = work_dir / "frames.txt", work_dir / "backgrounds.txt"
    _concat_file(frames, holds, foreground_concat)
    _concat_file(backgrounds, holds, background_concat)
    # Keep still backgrounds fixed; repeated zoom changes make the image wobble.
    filters = (
        "[0:v]fps=30[bg];"
        "[1:v]fps=30,format=rgba[fg];"
        "[bg][fg]overlay=shortest=1," + _subtitle_filter(captions, font_path.stem)
        + ",tpad=stop_mode=clone:stop_duration=1[video]"
    )
    command = [
        ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(background_concat),
        "-f", "concat", "-safe", "0", "-i", str(foreground_concat),
        "-i", str(audio_path), "-filter_complex", filters, "-map", "[video]", "-map", "2:a",
        "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-af", "apad=pad_dur=0.6",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode or not output_path.is_file():
        raise RenderError(f"ffmpeg 렌더링 실패: {(result.stderr or result.stdout)[-1000:]}")
    output_path.with_suffix(".timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return duration
