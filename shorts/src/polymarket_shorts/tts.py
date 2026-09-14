from __future__ import annotations

from pathlib import Path
import subprocess
import sys


class TTSError(RuntimeError):
    pass


def synthesize(
    text: str,
    *,
    audio_path: Path,
    subtitle_path: Path,
    voice: str,
    rate: str,
) -> None:
    command = [
        sys.executable,
        "-m",
        "edge_tts",
        "--voice",
        voice,
        f"--rate={rate}",
        "--text",
        text,
        "--write-media",
        str(audio_path),
        "--write-subtitles",
        str(subtitle_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode or not audio_path.is_file() or not subtitle_path.is_file():
        detail = (result.stderr or result.stdout or "unknown edge-tts error").strip()
        raise TTSError(f"TTS 생성 실패: {detail[-500:]}")
