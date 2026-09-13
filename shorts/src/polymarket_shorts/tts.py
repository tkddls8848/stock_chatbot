from __future__ import annotations

from array import array
from pathlib import Path
import subprocess
import sys
import wave

from .render import _caption_rows, _short_captions


SAMPLE_RATE = 24000
# 분야가 바뀌는 자리의 호흡. 잘라내기와 짝이라 실제 간격은
# _TAIL_KEEP + 이 값 + _LEAD_KEEP = 1.01초가 된다. edge-tts가 문장 사이에
# 스스로 두는 무음이 약 1.0초라, 문단 전환을 같은 박자로 맞춘 값이다.
# 잘라내기 전에는 여기에 앞뒤 무음이 더해져 1.73초로 벌어졌고, 그 혼자 긴
# 구멍이 말이 잘린 것도 아닌데 소리를 껐다 켠 것처럼 들리게 했다.
SCENE_PAUSE_SECONDS = 0.85
# s16 기준 대략 -44dBFS. edge-tts의 앞뒤 여백은 정확히 0이라 이 문턱에 걸리지 않는다.
_SILENCE_FLOOR = 200
_LEAD_KEEP_SECONDS = 0.04
_TAIL_KEEP_SECONDS = 0.12
_FADE_SECONDS = 0.012


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


def _trim(pcm: bytes) -> tuple[bytes, float]:
    """문단 앞뒤의 디지털 무음을 걷어내고 잘린 자리를 짧게 페이드한다.

    edge-tts는 문단마다 앞 약 0.2초·뒤 약 0.9초를 진폭 0으로 채워 돌려준다.
    그대로 이어 붙이면 문단 사이가 1.7초 완전 무음이 되어, 말이 잘린 것이
    아닌데도 소리를 껐다 켠 것처럼 들린다. 잘라낸 뒤 남는 간격은
    SCENE_PAUSE_SECONDS 하나뿐이다.
    """
    samples = array("h")
    samples.frombytes(pcm)
    first = next((i for i, value in enumerate(samples) if abs(value) > _SILENCE_FLOOR), None)
    if first is None:
        raise TTSError("장면 음성이 전부 무음입니다")
    last = len(samples) - 1 - next(
        i for i, value in enumerate(reversed(samples)) if abs(value) > _SILENCE_FLOOR
    )
    start = max(0, first - round(_LEAD_KEEP_SECONDS * SAMPLE_RATE))
    stop = min(len(samples), last + 1 + round(_TAIL_KEEP_SECONDS * SAMPLE_RATE))
    kept = samples[start:stop]
    span = min(round(_FADE_SECONDS * SAMPLE_RATE), len(kept) // 2)
    for offset in range(span):
        gain = offset / span
        kept[offset] = round(kept[offset] * gain)
        kept[-1 - offset] = round(kept[-1 - offset] * gain)
    return kept.tobytes(), start / SAMPLE_RATE


def synthesize_sections(
    texts: list[str], *, work_dir: Path, voice: str, rate: str, ffmpeg_bin: str,
) -> tuple[Path, Path, tuple[float, ...]]:
    """장면 음성을 PCM으로 이어 붙인다. 앞뒤 무음만 걷고 말은 한 샘플도 지우지 않는다."""
    work_dir.mkdir(parents=True, exist_ok=True)
    audio = work_dir / "narration.wav"
    subtitles = work_dir / "captions.srt"
    pause = b"\0" * (round(SCENE_PAUSE_SECONDS * SAMPLE_RATE) * 2)
    durations, cues = [], []
    cursor = 0.0
    with wave.open(str(audio), "wb") as merged:
        merged.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        for index, text in enumerate(texts, 1):
            mp3, vtt = work_dir / f"voice-{index:02d}.mp3", work_dir / f"voice-{index:02d}.vtt"
            synthesize(text, audio_path=mp3, subtitle_path=vtt, voice=voice, rate=rate)
            result = subprocess.run(
                [ffmpeg_bin, "-v", "error", "-i", str(mp3), "-f", "s16le",
                 "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"], capture_output=True, check=False,
            )
            if result.returncode or not result.stdout:
                raise TTSError("장면 음성을 PCM으로 변환하지 못했습니다")
            raw_duration = len(result.stdout) / (SAMPLE_RATE * 2)
            pcm, lead = _trim(result.stdout)
            voice_duration = len(pcm) / (SAMPLE_RATE * 2)
            rows = _caption_rows(vtt)
            if not rows or max(end for _, end, _ in rows) > raw_duration + 0.15:
                raise TTSError("장면 자막이 없거나 음성 끝을 벗어납니다")
            # 자막 시각은 잘라내기 전 mp3 기준이다. 앞을 걷어낸 만큼 당기고,
            # 말이 끝난 뒤까지 걸쳐 있던 자막은 줄어든 끝에 맞춘다.
            shift = lambda value: min(max(0.0, value - lead), voice_duration) + cursor
            cues.extend((shift(start), shift(end), words) for start, end, words in rows)
            merged.writeframes(pcm)
            merged.writeframes(pause)
            duration = voice_duration + SCENE_PAUSE_SECONDS
            durations.append(duration)
            cursor += duration
    _short_captions(cues, subtitles)
    return audio, subtitles, tuple(durations)
