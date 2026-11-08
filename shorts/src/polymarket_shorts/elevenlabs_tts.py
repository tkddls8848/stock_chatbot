"""Jian 연속 합성: 발음용 원고와 원래 자막 사이의 문자 시각 대응을 보존한다."""
from __future__ import annotations

import base64
import binascii
import math
import re

import requests


class ElevenTTSError(RuntimeError):
    """외부 응답·자격증명 원문을 담지 않는 실패 사유."""


_DIGITS = "영일이삼사오육칠팔구"


def _integer(value: str) -> str:
    number = int(value.replace(",", ""))
    if number == 0:
        return "영"
    if number >= 10**16:
        return " ".join(_DIGITS[int(digit)] for digit in str(number))
    groups = []
    for big_unit in ("", "만", "억", "조"):
        group, number = number % 10000, number // 10000
        part = ""
        for digit, unit in zip(f"{group:04d}", ("천", "백", "십", "")):
            if digit != "0":
                part += ("" if digit == "1" and unit else _DIGITS[int(digit)]) + unit
        if part:
            groups.append(part + big_unit)
        if not number:
            break
    return " ".join(reversed(groups))


def pronunciation(script: str) -> tuple[str, list[int]]:
    """숫자·기호를 풀되 각 출력 문자를 원래 원고 위치에 대응한다.

    이 대응으로 '십이점오오퍼센트' 전체의 실제 시각을 자막의 '12.55%'에 돌려준다.
    화면 원고를 고치거나 글자 수로 시간을 추정하지 않는다.
    """
    text, positions = script, list(range(len(script)))

    def substitute(pattern, replacement):
        nonlocal text, positions
        pieces, mapped, cursor = [], [], 0
        for match in re.finditer(pattern, text):
            pieces.append(text[cursor:match.start()])
            mapped.extend(positions[cursor:match.start()])
            new = replacement(match)
            pieces.append(new)
            mapped.extend([positions[match.start()]] * len(new))
            cursor = match.end()
        pieces.append(text[cursor:])
        mapped.extend(positions[cursor:])
        text, positions = "".join(pieces), mapped

    # 기존 원고의 나열을 완결된 확률 문장으로 읽는다. 숫자·전망 자체는 바꾸지 않는다.
    substitute(r"(?<=[을를]),(?=\s*컨센서스 참여자의)", lambda _: " 기대하고 있습니다.")
    substitute(r"(?:동결|인상|인하)(?:-(?:동결|인상|인하))+",
               lambda m: "한 뒤 ".join(m.group().split("-")))
    substitute(r"(?<![A-Za-z])(?:AI|ETF)(?![A-Za-z])",
               lambda m: {"AI": "에이아이", "ETF": "이티에프"}[m.group()])

    substitute(r"(?<![\d.])([1-9])(?=개|차례)",
               lambda m: ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉")[int(m.group())])

    def number(match):
        raw, month = match.group(1), match.group(2)
        if month:
            return {"6": "유월", "10": "시월"}.get(raw, _integer(raw) + "월")
        whole, dot, fraction = raw.replace(",", "").partition(".")
        return _integer(whole) + ("점" + "".join(_DIGITS[int(d)] for d in fraction) if dot else "")

    substitute(r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(월)?", number)
    substitute(r"%", lambda _: "퍼센트")
    substitute(r"·", lambda _: ", ")
    return text, positions


def generate(script: str, *, api_key: str, voice: str, model: str,
             speed: float, stability: float) -> tuple[bytes, list[tuple[float, float, str]]]:
    text, positions = pronunciation(script)
    try:
        response = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": api_key},
            json={"text": text, "model_id": model,
                  "voice_settings": {"speed": speed, "stability": stability,
                                     "similarity_boost": 0.75, "style": 0.0,
                                     "use_speaker_boost": False}},
            timeout=(10, 150), allow_redirects=False,
        )
        # 오류 본문에 키나 원고가 포함될 수 있으므로 상태 코드만 남긴다.
        if response.status_code != 200:
            raise ElevenTTSError(f"http_{response.status_code}")
        payload = response.json()
    except requests.RequestException:
        raise ElevenTTSError("network_error") from None
    except ValueError:
        raise ElevenTTSError("invalid_json") from None
    try:
        audio = base64.b64decode(payload["audio_base64"], validate=True)
        alignment = payload["alignment"]
        chars = alignment["characters"]
        starts = alignment["character_start_times_seconds"]
        ends = alignment["character_end_times_seconds"]
        if not audio or "".join(chars) != text or not len(chars) == len(starts) == len(ends):
            raise ValueError
        previous = 0.0
        for start, end in zip(starts, ends):
            if (type(start) not in (float, int) or type(end) not in (float, int)
                    or not math.isfinite(start) or not math.isfinite(end)
                    or start < previous or end < start):
                raise ValueError
            previous = end
        words = []
        cursor = 0
        for token in re.finditer(r"\S+", script):
            while cursor < len(positions) and positions[cursor] < token.start():
                cursor += 1
            first = cursor
            while cursor < len(positions) and positions[cursor] < token.end():
                cursor += 1
            if first == cursor or ends[cursor - 1] <= starts[first]:
                raise ValueError
            words.append((starts[first], ends[cursor - 1], token.group()))
        if not words:
            raise ValueError
    except (KeyError, TypeError, ValueError, binascii.Error):
        raise ElevenTTSError("invalid_audio_or_alignment") from None
    return audio, words
