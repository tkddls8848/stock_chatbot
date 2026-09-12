from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .scenario import Scenario, Scene


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
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/malgunbd.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RenderError("한글 폰트가 없습니다. SHORTS_FONT_FILE을 지정하세요")


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


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
    image = image.filter(ImageFilter.GaussianBlur(radius=1.2))
    return ImageEnhance.Brightness(image).enhance(0.43)


def render_frame(
    scene: Scene,
    path: Path,
    *,
    font_path: Path,
    index: int,
    total: int,
    background_path: Path | None = None,
) -> None:
    image = _background(background_path)
    draw = ImageDraw.Draw(image)
    accent = _COLORS.get(scene.accent, _COLORS["gold"])
    draw.rectangle((0, 0, 22, HEIGHT), fill=accent)
    draw.rectangle((22, 0, WIDTH, 470), fill="#151512A0")
    draw.rectangle((22, 1570, WIDTH, HEIGHT), fill="#151512C8")

    brand_font = _font(font_path, 34)
    kicker_font = _font(font_path, 34)
    title_font = _font(font_path, 72 if scene.kind != "consensus" else 60)
    body_font = _font(font_path, 42 if scene.kind == "consensus" else 48)
    label_font = _font(font_path, 27)
    small_font = _font(font_path, 28)
    draw.text((82, 76), "NUNCHI · POLYMARKET", font=brand_font, fill=_COLORS["muted"])
    draw.text((82, 205), scene.kicker, font=kicker_font, fill=accent)

    title_lines = _wrap(draw, scene.title, title_font, 900)
    y = 285
    for line in title_lines[:3]:
        draw.text((82, y), line, font=title_font, fill=_COLORS["ink"])
        y += 102
    panel_top = y + 36
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (82, panel_top, 998, 1500),
        radius=42,
        fill=(28, 27, 24, 225),
        outline=(92, 87, 77, 255),
        width=2,
    )
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    bullets = scene.bullets or tuple(scene.body.splitlines())
    body_y = y + 105
    for bullet_index, bullet in enumerate(bullets[:4]):
        label, separator, value = bullet.partition(" · ")
        if separator:
            draw.text((142, body_y), label.upper(), font=label_font, fill=accent)
            body_y += 42
            display = value
        else:
            display = bullet
        wrapped = _wrap(draw, display, body_font, 765)
        draw.ellipse((112, body_y + 18, 126, body_y + 32), fill=accent)
        for line in wrapped[:3]:
            draw.text((142, body_y), line, font=body_font, fill=_COLORS["ink"])
            body_y += 62
        body_y += 38 if bullet_index < len(bullets) - 1 else 0

    draw.text((82, 1750), "예측시장 가격 기반 · 투자 조언 아님", font=small_font, fill=_COLORS["muted"])
    if background_path is not None:
        draw.text((82, 1698), "VISUAL · WIKIMEDIA COMMONS", font=small_font, fill=_COLORS["muted"])
    draw.text((900, 1750), f"{index}/{total}", font=small_font, fill=accent, anchor="ra")
    draw.rounded_rectangle((82, 1820, 998, 1832), radius=6, fill="#34322D")
    draw.rounded_rectangle((82, 1820, 82 + int(916 * index / total), 1832), radius=6, fill=accent)
    image.save(path, "PNG", optimize=True)


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
        f"FontName={font_name},FontSize=18,PrimaryColour=&H00F5F1E8," 
        "OutlineColour=&H00151512,BorderStyle=1,Outline=3,Shadow=0," 
        "Alignment=2,MarginV=260"
    )
    return f"subtitles='{escaped}':force_style='{style}'"


def render_video(
    scenario: Scenario,
    *,
    audio_path: Path,
    subtitle_path: Path,
    output_path: Path,
    work_dir: Path,
    font_path: Path,
    ffmpeg_bin: str,
    ffprobe_bin: str,
    max_duration: float,
    background_paths: tuple[Path | None, ...] | None = None,
) -> float:
    duration = probe_duration(audio_path, ffprobe_bin=ffprobe_bin)
    if duration > max_duration:
        raise RenderError(
            f"내레이션이 {duration:.1f}초로 쇼츠 상한 {max_duration:.1f}초를 넘습니다. "
            "SHORTS_TARGET_SCRIPT_CHARS를 낮추세요."
        )
    weights = [max(1, len(scene.narration)) for scene in scenario.scenes]
    total_weight = sum(weights)
    scene_durations = [duration * weight / total_weight for weight in weights]
    frames: list[Path] = []
    backgrounds = background_paths or tuple(None for _ in scenario.scenes)
    for index, scene in enumerate(scenario.scenes, start=1):
        frame = work_dir / f"frame-{index:02d}.png"
        render_frame(
            scene,
            frame,
            font_path=font_path,
            index=index,
            total=len(scenario.scenes),
            background_path=backgrounds[index - 1],
        )
        frames.append(frame)
    concat = work_dir / "frames.txt"
    _concat_file(frames, scene_durations, concat)
    command = [
        ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-i", str(audio_path), "-vf", _subtitle_filter(subtitle_path),
        "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode or not output_path.is_file():
        raise RenderError(f"ffmpeg 렌더링 실패: {(result.stderr or result.stdout)[-1000:]}")
    return duration
