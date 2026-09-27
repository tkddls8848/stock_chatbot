"""Pillow가 만든 비트와 발화 시각을 Blender VSE에 전달한다."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from PIL import ImageFont

from .render import (
    CAPTION_FONT_SIZE, CAPTION_MARGIN_L, CAPTION_MARGIN_R, CAPTION_MARGIN_V,
    HEIGHT, WIDTH, RenderError,
)


def compose(*, images: list[dict], movies: list[dict], subtitles: list[dict],
            audio_path: Path, output_path: Path, work_dir: Path, font_path: Path,
            duration: float, blender_bin: str) -> None:
    manifest = work_dir / "blender-manifest.json"
    # 장면 프레임용 기본 폰트는 바꾸지 않고 자막만 기존 Bold 스타일을 따른다.
    bold = font_path.with_name("NotoSansCJK-Bold.ttc")
    caption_font = bold if font_path.name == "NotoSansCJK-Regular.ttc" and bold.is_file() else font_path
    font = ImageFont.truetype(str(caption_font), CAPTION_FONT_SIZE)
    ascent, descent = font.getmetrics()
    manifest.write_text(json.dumps({
        "width": WIDTH, "height": HEIGHT, "fps": 30, "duration": duration,
        "audio": str(audio_path.resolve()), "output": str(output_path.resolve()),
        "tail_seconds": .6, "clone_padding_seconds": 1,
        "images": images, "movies": movies, "subtitles": subtitles,
        "caption": {"font": str(caption_font.resolve()), "size": CAPTION_FONT_SIZE,
                    "synthetic_bold": "bold" not in font.getname()[1].lower(),
                    # libass는 ascender+descender를 FontSize에 맞춘다. BLF는 em 크기다.
                    "em_size": CAPTION_FONT_SIZE ** 2 / (ascent + descent),
                    "left": CAPTION_MARGIN_L, "right": CAPTION_MARGIN_R, "bottom": CAPTION_MARGIN_V},
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    script = Path(__file__).with_name("blender") / "vse_render.py"
    command = [blender_bin, "-b", "--factory-startup", "--python-exit-code", "1",
               "--python", str(script), "--", str(manifest.resolve())]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=False)
    except OSError as exc:
        raise RenderError(f"Blender를 실행할 수 없습니다. BLENDER_BIN을 확인하세요: {blender_bin}") from exc
    if result.returncode or not output_path.is_file():
        raise RenderError(f"Blender 렌더링 실패: {(result.stderr + result.stdout)[-2000:]}")
