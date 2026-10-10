"""Pillow가 만든 비트와 발화 시각을 Blender VSE에 전달한다."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

from .render import HEIGHT, WIDTH, RenderError


# 내레이션 음량 배율(운영자 요청 2026-09-28: 30% 키운다 = +2.3dB). 실측 최대치가
# -3.6dB(한국어)·-3.1dB(영어)라 키워도 0dBFS 아래에 남는다.
NARRATION_GAIN = 1.3


def compose(*, images: list[dict], subtitles: list[dict],
            audio_path: Path, output_path: Path, work_dir: Path,
            duration: float, blender_bin: str, size: tuple[int, int] = (WIDTH, HEIGHT)) -> None:
    """화면 카드(`images`)·자막 그림(`subtitles`, 화면 크기 투명 PNG)과 음성을 합친다.

    `size`는 (가로, 세로)다. 쇼츠는 세로 1080×1920, 롱폼(`longform_render`)은 가로 1920×1080이다.
    """
    manifest = work_dir / "blender-manifest.json"
    manifest.write_text(json.dumps({
        "width": size[0], "height": size[1], "fps": 30, "duration": duration,
        "audio": str(audio_path.resolve()), "output": str(output_path.resolve()),
        "narration_gain": NARRATION_GAIN,
        "tail_seconds": .6, "clone_padding_seconds": 1,
        "images": images, "subtitles": subtitles,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    script = Path(__file__).with_name("blender") / "vse_render.py"
    command = [blender_bin, "-b", "--factory-startup", "--python-exit-code", "1",
               "--python", str(script), "--", str(manifest.resolve())]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=False)
    except OSError as exc:
        raise RenderError(f"Blender를 실행할 수 없습니다. blender를 설치하거나 PATH에 두세요: {blender_bin}") from exc
    if result.returncode or not output_path.is_file():
        raise RenderError(f"Blender 렌더링 실패: {(result.stderr + result.stdout)[-2000:]}")
