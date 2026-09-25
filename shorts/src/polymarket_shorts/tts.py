from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence
import asyncio
import json
import logging

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
OPENING_PAUSE_SECONDS = 1.15
TOPIC_PAUSE_SECONDS = 1.6
CLOSING_PAUSE_SECONDS = 1.8
# 장면 안에서도 긴 문장을 말하고 나면 한 박자 더 쉰다. 짧은 문장 뒤는 원래
# 호흡(0.86초)이 자연스러워 건드리지 않는다.
SENTENCE_PAUSE_SECONDS = 1.05
LONG_SENTENCE_CHARS = 30
_SENTENCE_END = (".", "!", "?")
# 24kHz·48kbps·모노 MPEG-2 Layer III. 프레임 하나가 144바이트·576샘플이다.
_FRAME_BYTES = 144
_FRAME_SECONDS = 576 / 24000


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
    scenes = _pace_breaths(audio_path, scenes, narrations)
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


def _frames(data: bytes) -> list[bytes]:
    """CBR MP3를 프레임 목록으로 자른다. 다른 형식이면 손대지 않고 멈춘다."""
    if not data or len(data) % _FRAME_BYTES:
        raise TTSError("예상과 다른 MP3 길이라 장면 호흡을 넣을 수 없습니다")
    frames = [data[i:i + _FRAME_BYTES] for i in range(0, len(data), _FRAME_BYTES)]
    if any(frame[0] != 0xFF or frame[1] & 0xE0 != 0xE0 for frame in frames):
        raise TTSError("MP3 프레임 경계가 144바이트가 아닙니다")
    return frames


def scene_pauses(count: int) -> tuple[float, ...]:
    """장면 경계마다의 목표 쉼. 자리에 따라 다르다(위 상수의 설명)."""
    if count < 2:
        return ()
    gaps = [TOPIC_PAUSE_SECONDS] * (count - 1)
    gaps[0] = OPENING_PAUSE_SECONDS
    gaps[-1] = CLOSING_PAUSE_SECONDS
    return tuple(gaps)


def _sentence_breaths(narration: str, words: Sequence[Word]) -> dict[int, float]:
    """장면 안에서 긴 문장이 끝나는 단어 번호와 그 자리의 목표 쉼.

    문장이 끝났는지는 두 단어 사이에 남은 원고 글자로 본다 — edge-tts는 문장
    부호를 단어로 돌려주지 않는다.
    """
    starts = locate(narration, words)
    breaths: dict[int, float] = {}
    opened = 0
    for index, (word, start) in enumerate(zip(words[:-1], starts)):
        between = narration[start + len(word.text):starts[index + 1]]
        if not any(mark in between for mark in _SENTENCE_END):
            continue
        if starts[index + 1] - opened >= LONG_SENTENCE_CHARS:
            breaths[index] = SENTENCE_PAUSE_SECONDS
        opened = starts[index + 1]
    return breaths


def _pace_breaths(
    audio_path: Path, scenes: tuple[tuple[Word, ...], ...], narrations: Sequence[str],
) -> tuple[tuple[Word, ...], ...]:
    """장면 경계와 긴 문장 뒤의 쉼을 목표치까지 넓힌다.

    원고를 무엇으로 이어 붙여도(줄바꿈·빈 줄·말줄임표) 이 서비스가 주는 간격은
    0.863초로 고정이라, 쉼은 합성 뒤에 넣을 수밖에 없다.

    쉼 한가운데의 무음 프레임을 그만큼 복제해 끼운다. 말소리 프레임은 한 바이트도
    건드리지 않는다. 복제 가능한 프레임이 없으면 해당 경계의 원래 호흡을 유지한다.
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
    if not targets:
        return scenes
    frames = _frames(audio_path.read_bytes())
    paced: list[bytes] = []
    cursor, shift = 0, 0.0
    marks: dict[int, float] = {}
    for index in sorted(targets):
        before, after = flat[index], flat[index + 1]
        extra = max(0, round((targets[index] - (after.start - before.end)) / _FRAME_SECONDS))
        # 이미 내보낸 프레임보다 앞으로 돌아가지 않는다 — 돌아가면 그만큼이 두 번 실린다.
        middle = max(cursor, int((before.end + after.start) / 2 / _FRAME_SECONDS))
        if extra:
            quiet = _quiet_frame(frames, middle)
            if quiet is None:
                logger.warning("%.2f초 경계에 복제할 무음이 없어 원래 호흡을 유지합니다", after.start)
                extra = 0
            else:
                paced.extend(frames[cursor:middle])
                paced.extend([quiet] * extra)
                cursor = middle
        shift += extra * _FRAME_SECONDS
        marks[index + 1] = shift
    paced.extend(frames[cursor:])
    audio_path.write_bytes(b"".join(paced))
    shifts, current = [], 0.0
    for position in range(len(flat)):
        current = marks.get(position, current)
        shifts.append(current)
    moved = iter(replace(word, start=word.start + by, end=word.end + by)
                 for word, by in zip(flat, shifts))
    return tuple(tuple(next(moved) for _ in scene) for scene in scenes)


def _quiet_frame(frames: Sequence[bytes], middle: int) -> bytes | None:
    """쉼 한가운데에서 되풀이되는 프레임. 말소리를 복제하지 않도록 반복을 요구한다."""
    window = frames[max(0, middle - 8):middle + 8]
    if not window:
        return None
    frame, count = Counter(window).most_common(1)[0]
    return frame if count >= 2 else None
