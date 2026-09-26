from __future__ import annotations

from array import array
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence
import asyncio
import json
import logging
import subprocess

import edge_tts
from edge_tts.exceptions import EdgeTTSException


logger = logging.getLogger(__name__)


# 호흡. edge-tts는 문장 사이도 장면 사이도 똑같이 0.86초로 읽어서, 화면이 새
# 분야로 넘어가는데 목소리는 쉬지 않았다. 새 장면의 첫 마디가 잘린 것처럼
# 들리던 원인이다. 그래서 합성 뒤에 자리마다 다른 쉼을 넣는다 — 한 값으로
# 고정하면 어디서나 똑같이 끊겨 사람이 읽는 리듬이 아니게 된다.
#
# 도입에서 첫 이슈로는 흐름을 끊지 않을 만큼만 쉬고, 이슈와 이슈 사이는 주제가
# 통째로 바뀌므로 넓히며, 마지막 고지문 앞에서 가장 길게 쉰다. 프레임 하나가
# 24ms이므로 실제 값은 이 근처로 떨어진다. 자막·화면이 언제 넘어가는지는
# render.SCENE_LEAD가 정한다.
#
# 값은 짧게 둔다(2026-09-26 조정). 1.15~1.8초를 넣던 동안 79초 영상의 23%가 완전한
# 무음이었고, 배경음이 없어 쉼마다 "볼륨이 0으로 떨어졌다 돌아오는" 소리로 들렸다.
# 문장 사이의 원래 호흡(0.86초)도 쇼츠에는 길어 줄인다 — 늘리기만 하던 것을
# 늘리고 줄이는 쪽으로 바꿨다.
OPENING_PAUSE_SECONDS = 0.6
TOPIC_PAUSE_SECONDS = 0.75
CLOSING_PAUSE_SECONDS = 0.9
# 장면 안 문장 끝의 쉼.
SENTENCE_PAUSE_SECONDS = 0.45
_SENTENCE_END = (".", "!", "?")
# 쉼을 줄일 때 말소리 양 끝에 남겨 두는 여유. 단어 시각은 수십 ms 어긋날 수 있어
# 이 안쪽만 덜어 내야 말꼬리·첫소리가 잘리지 않는다.
_GUARD_SECONDS = 0.12
# 잘라 붙인 자리의 짧은 페이드. 이음매에서 파형이 튀지 않게 한다.
_FADE_SECONDS = 0.02
_SAMPLE_RATE = 24000


class TTSError(RuntimeError):
    pass


@dataclass(frozen=True)
class Word:
    """edge-tts가 보고한 한 단어의 실제 발화 구간."""

    start: float
    end: float
    text: str


def synthesize(
    narrations: Sequence[str],
    *,
    audio_path: Path,
    words_path: Path,
    voice: str,
    rate: str,
    ffmpeg_bin: str = "ffmpeg",
) -> tuple[tuple[Word, ...], ...]:
    """장면 원고 전체를 한 번에 합성하고 장면별 단어 시각을 돌려준다.

    CLI(`python -m edge_tts`)로는 단어 시각을 받을 수 없다. CLI에는 boundary를
    고르는 옵션이 없어 SentenceBoundary만 쓰는데, 그 이벤트의 duration은 문장
    뒤 쉼(약 0.86초)까지 포함한다. 그래서 문장 큐의 끝이 마지막 단어보다 늘
    0.8초쯤 뒤에 찍혔고, 그 부풀려진 창을 글자 수로 나눠 자막을 쪼개니 분할점이
    통째로 밀려 뒷 문구가 말보다 0.4~1.0초 늦게 떴다. 단어 단위로 받으면 그
    추정 자체가 없어진다.

    합성은 한 번뿐이다 — 장면별로 따로 합성해 이어 붙이면 경계마다 음색과
    호흡이 다시 시작된다. 장면 사이 호흡만 뒤에서 넓힌다.
    """
    script = "\n".join(narrations)
    try:
        asyncio.run(
            edge_tts.Communicate(
                script, voice, rate=rate, boundary="WordBoundary",
            ).save(str(audio_path), str(words_path))
        )
        words = _read_words(words_path)
    except (EdgeTTSException, OSError, ValueError, KeyError) as exc:
        raise TTSError(f"TTS 생성 실패: {exc}") from exc
    if not audio_path.is_file() or not words:
        raise TTSError("TTS가 음성이나 단어 시각을 돌려주지 않았습니다")

    scenes = _split_by_scene(narrations, words)
    scenes = _pace_breaths(audio_path, scenes, narrations, ffmpeg_bin=ffmpeg_bin)
    _write_words(words_path, scenes)
    return scenes


def locate(text: str, words: Sequence[Word]) -> list[int]:
    """원고에서 각 단어가 시작하는 글자 위치.

    앞에서부터 순서대로 훑으므로 같은 말이 여러 번 나와도 어긋나지 않는다.
    """
    positions, cursor = [], 0
    for word in words:
        position = text.find(word.text, cursor)
        if position < 0:
            raise TTSError(f"합성된 단어를 원고에서 찾지 못했습니다: {word.text}")
        positions.append(position)
        cursor = position + len(word.text)
    return positions


def _read_words(path: Path) -> tuple[Word, ...]:
    """단어 시각 파일을 읽는다. offset·duration의 단위는 100나노초다."""
    words: list[Word] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        offset, duration = event["offset"], event["duration"]
        words.append(Word(offset / 1e7, (offset + duration) / 1e7, event["text"]))
    return tuple(words)


def _write_words(path: Path, scenes: Sequence[Sequence[Word]]) -> None:
    """호흡을 넣은 뒤의 시각으로 파일을 다시 쓴다 — 옆에 놓인 음성과 맞아야 한다."""
    lines = [
        json.dumps({"type": "WordBoundary", "offset": round(word.start * 1e7),
                    "duration": round((word.end - word.start) * 1e7), "text": word.text},
                   ensure_ascii=False)
        for scene in scenes for word in scene
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _split_by_scene(
    narrations: Sequence[str], words: Sequence[Word],
) -> tuple[tuple[Word, ...], ...]:
    """이어 읽은 단어들을 원고 위치로 장면에 나눠 담는다."""
    script = "\n".join(narrations)
    positions = locate(script, words)
    scenes: list[tuple[Word, ...]] = []
    start = 0
    for narration in narrations:
        end = start + len(narration)
        members = tuple(word for word, at in zip(words, positions) if start <= at < end)
        if not members:
            raise TTSError(f"장면 원고를 읽은 음성이 없습니다: {narration[:30]}")
        scenes.append(members)
        start = end + 1
    return tuple(scenes)


def scene_pauses(count: int) -> tuple[float, ...]:
    """장면 경계마다의 목표 쉼. 자리에 따라 다르다(위 상수의 설명)."""
    if count < 2:
        return ()
    gaps = [TOPIC_PAUSE_SECONDS] * (count - 1)
    gaps[0] = OPENING_PAUSE_SECONDS
    gaps[-1] = CLOSING_PAUSE_SECONDS
    return tuple(gaps)


def _sentence_breaths(narration: str, words: Sequence[Word]) -> dict[int, float]:
    """장면 안에서 문장이 끝나는 단어 번호와 그 자리의 목표 쉼.

    문장이 끝났는지는 두 단어 사이에 남은 원고 글자로 본다 — edge-tts는 문장
    부호를 단어로 돌려주지 않는다.
    """
    starts = locate(narration, words)
    breaths: dict[int, float] = {}
    for index, (word, start) in enumerate(zip(words[:-1], starts)):
        between = narration[start + len(word.text):starts[index + 1]]
        if any(mark in between for mark in _SENTENCE_END):
            breaths[index] = SENTENCE_PAUSE_SECONDS
    return breaths


def _decode(audio_path: Path, ffmpeg_bin: str) -> array:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-i", str(audio_path), "-f", "s16le", "-ac", "1",
             "-ar", str(_SAMPLE_RATE), "-"],
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise TTSError(f"FFmpeg를 실행하지 못했습니다: {exc}") from exc
    if result.returncode or not result.stdout:
        raise TTSError(f"음성을 PCM으로 풀지 못했습니다: {result.stderr[-300:]!r}")
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
    return samples


def _encode(samples: array, audio_path: Path, ffmpeg_bin: str) -> None:
    # 다시 MP3로 둔다 — 제작·수정·HyperFrames 내보내기가 모두 narration.mp3를 읽는다.
    # 원본(48kbps)보다 높은 비트레이트라 재압축 손실이 들리지 않는다.
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-v", "error", "-y", "-f", "s16le", "-ar", str(_SAMPLE_RATE), "-ac", "1",
             "-i", "-", "-c:a", "libmp3lame", "-b:a", "96k", str(audio_path)],
            input=samples.tobytes(), capture_output=True, check=False,
        )
    except OSError as exc:
        raise TTSError(f"FFmpeg를 실행하지 못했습니다: {exc}") from exc
    if result.returncode:
        raise TTSError(f"쉼을 조정한 음성을 저장하지 못했습니다: {result.stderr[-300:]!r}")


def _fade(samples: array, start: int, length: int, *, rising: bool) -> None:
    """samples[start:start+length]에 선형 페이드를 제자리에서 건다."""
    end = min(len(samples), start + length)
    span = max(1, end - start)
    for offset, index in enumerate(range(max(0, start), end)):
        gain = (offset + 1) / span if rising else 1 - (offset + 1) / span
        samples[index] = int(samples[index] * gain)


def _pace_breaths(
    audio_path: Path, scenes: tuple[tuple[Word, ...], ...], narrations: Sequence[str],
    *, ffmpeg_bin: str = "ffmpeg",
) -> tuple[tuple[Word, ...], ...]:
    """문장·장면 경계의 쉼을 목표치로 늘리거나 줄인다.

    원고를 무엇으로 이어 붙여도(줄바꿈·빈 줄·말줄임표) 이 서비스가 주는 간격은
    0.863초로 고정이라, 쉼은 합성 뒤에 조정할 수밖에 없다.

    PCM에서 한다. 예전에는 MP3 무음 프레임을 복제해 넣었는데, 그 방식은 늘릴 수만
    있고 이음매에 페이드를 걸 수 없었다. 쉼 한가운데에서만 잘라 붙이고 말소리 쪽은
    `_GUARD_SECONDS` 안쪽을 건드리지 않는다. 이음매마다 짧은 페이드를 건다.
    """
    flat = [word for scene in scenes for word in scene]
    targets: dict[int, float] = {}
    boundaries: list[int] = []
    offset = 0
    for narration, scene in zip(narrations, scenes, strict=True):
        targets.update({offset + index: gap
                        for index, gap in _sentence_breaths(narration, scene).items()})
        offset += len(scene)
        boundaries.append(offset - 1)
    # 장면 경계는 문장 경계이기도 하다. 그 자리는 장면의 목표 쉼이 이긴다.
    targets.update(zip(boundaries, scene_pauses(len(scenes))))
    targets.pop(len(flat) - 1, None)
    if not targets:
        return scenes
    source = _decode(audio_path, ffmpeg_bin)
    fade = round(_FADE_SECONDS * _SAMPLE_RATE)
    paced, cursor, shift = array("h"), 0, 0.0
    marks: dict[int, float] = {}
    for index in sorted(targets):
        before, after = flat[index], flat[index + 1]
        gap = after.start - before.end
        delta = targets[index] - gap
        middle = max(cursor, round((before.end + after.start) / 2 * _SAMPLE_RATE))
        if delta > 0:
            paced.extend(source[cursor:middle])
            _fade(paced, len(paced) - fade, fade, rising=False)
            paced.extend(array("h", bytes(2 * round(delta * _SAMPLE_RATE))))
            cursor = middle
            head = len(paced)
            paced.extend(source[cursor:cursor + fade])
            _fade(paced, head, fade, rising=True)
            cursor += fade
            changed = delta
        else:
            removable = max(0.0, gap - 2 * _GUARD_SECONDS)
            cut = round(min(-delta, removable) * _SAMPLE_RATE)
            if cut <= 0:
                marks[index + 1] = shift
                continue
            left = max(cursor, middle - cut // 2)
            paced.extend(source[cursor:left])
            _fade(paced, len(paced) - fade, fade, rising=False)
            cursor = left + cut
            head = len(paced)
            paced.extend(source[cursor:cursor + fade])
            _fade(paced, head, fade, rising=True)
            cursor += fade
            changed = -cut / _SAMPLE_RATE
        shift += changed
        marks[index + 1] = shift
    paced.extend(source[cursor:])
    _encode(paced, audio_path, ffmpeg_bin)
    shifts, current = [], 0.0
    for position in range(len(flat)):
        current = marks.get(position, current)
        shifts.append(current)
    moved = iter(replace(word, start=word.start + by, end=word.end + by)
                 for word, by in zip(flat, shifts))
    return tuple(tuple(next(moved) for _ in scene) for scene in scenes)


