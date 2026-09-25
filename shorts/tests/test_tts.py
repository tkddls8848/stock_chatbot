import json
from pathlib import Path

import pytest

from polymarket_shorts import tts
from polymarket_shorts.tts import Word


def _fake_communicate(captured, words):
    class Fake:
        def __init__(self, text, voice, *, rate, boundary):
            captured.update(text=text, voice=voice, rate=rate, boundary=boundary)

        async def save(self, audio_fname, metadata_fname):
            Path(audio_fname).write_bytes(b"mp3")
            Path(metadata_fname).write_text(
                "".join(json.dumps(word, ensure_ascii=False) + "\n" for word in words),
                encoding="utf-8",
            )

    return Fake


def test_synthesis_asks_for_word_timing_and_returns_it_per_scene(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(tts.edge_tts, "Communicate", _fake_communicate(captured, [
        {"type": "WordBoundary", "offset": 1000000, "duration": 5000000, "text": "연준의"},
        {"type": "WordBoundary", "offset": 6500000, "duration": 3000000, "text": "결정"},
    ]))

    scenes = tts.synthesize(
        ["연준의 결정"],
        audio_path=tmp_path / "voice.mp3",
        words_path=tmp_path / "voice.words.jsonl",
        voice="ko-KR-SunHiNeural",
        rate="-4%",
    )

    # CLI(`python -m edge_tts`)에는 boundary 옵션이 없어 SentenceBoundary만 받는다.
    # 그 이벤트의 duration은 문장 뒤 쉼까지 포함해 자막 시각의 근거가 되지 못한다.
    assert captured["boundary"] == "WordBoundary"
    assert captured["rate"] == "-4%"
    assert scenes == ((Word(0.1, 0.6, "연준의"), Word(0.65, 0.95, "결정")),)


def test_synthesis_without_words_fails_instead_of_returning_silent_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(tts.edge_tts, "Communicate", _fake_communicate({}, []))

    with pytest.raises(tts.TTSError, match="단어 시각"):
        tts.synthesize(
            ["연준의 결정"],
            audio_path=tmp_path / "voice.mp3",
            words_path=tmp_path / "voice.words.jsonl",
            voice="ko-KR-SunHiNeural",
            rate="+0%",
        )


def test_a_failed_synthesis_is_reported_as_tts_error(tmp_path, monkeypatch):
    class Fake:
        def __init__(self, *args, **kwargs):
            pass

        async def save(self, audio_fname, metadata_fname):
            raise tts.EdgeTTSException("no audio received")

    monkeypatch.setattr(tts.edge_tts, "Communicate", Fake)

    with pytest.raises(tts.TTSError, match="TTS 생성 실패"):
        tts.synthesize(
            ["연준의 결정"],
            audio_path=tmp_path / "voice.mp3",
            words_path=tmp_path / "voice.words.jsonl",
            voice="ko-KR-SunHiNeural",
            rate="+0%",
        )


def _cbr_mp3(frames):
    """24kHz·48kbps·모노 MP3 흉내. 프레임 하나가 144바이트·24ms다."""
    return b"".join(b"\xff\xf3\x64\xc4" + payload for payload in frames)


def test_scene_breaks_are_widened_by_duplicating_silent_frames(tmp_path):
    # 40프레임 말소리 → 30프레임 무음 → 130프레임 말소리
    speech = [bytes([n % 251 or 7]) * 140 for n in range(1, 41)]
    quiet = [b"\x00" * 140] * 30
    tail = [bytes([(n * 7) % 251 or 9]) * 140 for n in range(1, 131)]
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(_cbr_mp3(speech + quiet + tail))
    scenes = (
        (Word(0.0, 1.0, "앞"),),
        (Word(1.86, 2.5, "뒤"),),
    )

    paced = tts._pace_breaths(audio, scenes, ["앞", "뒤"])

    # 0.86초였던 쉼을 그 자리의 목표까지 넓힌다. 프레임 하나가 24ms다.
    added = round((tts.CLOSING_PAUSE_SECONDS - 0.86) / tts._FRAME_SECONDS)
    assert len(audio.read_bytes()) == (200 + added) * tts._FRAME_BYTES
    # 말소리 프레임은 그대로다 — 복제한 것은 쉼 한가운데의 무음뿐이다.
    assert audio.read_bytes().count(b"\x00" * 140) == 30 + added
    assert paced[0] == scenes[0]
    assert paced[1][0].start == pytest.approx(1.86 + added * tts._FRAME_SECONDS)


def test_a_scene_break_without_silence_preserves_audio_and_timing(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(_cbr_mp3([bytes([n % 251 or 7]) * 140 for n in range(1, 201)]))

    original = audio.read_bytes()
    scenes = ((Word(0.0, 1.0, "앞"),), (Word(1.86, 2.5, "뒤"),))
    assert tts._pace_breaths(audio, scenes, ["앞", "뒤"]) == scenes
    assert audio.read_bytes() == original


def test_a_long_pause_does_not_search_for_silence(tmp_path, monkeypatch):
    audio = tmp_path / "voice.mp3"
    original = _cbr_mp3([bytes([n % 251 or 7]) * 140 for n in range(1, 201)])
    audio.write_bytes(original)
    monkeypatch.setattr(tts, "_quiet_frame", lambda *args: pytest.fail("이미 충분히 긴 쉼입니다"))
    scenes = ((Word(0, 1, "앞"),), (Word(3, 3.5, "뒤"),))
    assert tts._pace_breaths(audio, scenes, ["앞", "뒤"]) == scenes
    assert audio.read_bytes() == original


def test_mixed_pauses_shift_only_by_inserted_silence(tmp_path):
    frames = [bytes([n % 251 or 7]) * 140 for n in range(1, 301)]
    frames[145:175] = [b"\x00" * 140] * 30
    audio = tmp_path / "voice.mp3"
    original = _cbr_mp3(frames)
    audio.write_bytes(original)
    scenes = ((Word(0, 1, "첫째"),), (Word(1.86, 3.4, "둘째"),), (Word(4.26, 5, "셋째"),))
    paced = tts._pace_breaths(audio, scenes, ["첫째", "둘째", "셋째"])
    added = round((tts.CLOSING_PAUSE_SECONDS - .86) / tts._FRAME_SECONDS)
    assert paced[:2] == scenes[:2]
    assert paced[2][0].start == pytest.approx(4.26 + added * tts._FRAME_SECONDS)
    middle = int((3.4 + 4.26) / 2 / tts._FRAME_SECONDS) * tts._FRAME_BYTES
    assert audio.read_bytes()[:middle] == original[:middle]
    assert audio.read_bytes()[middle + added * tts._FRAME_BYTES:] == original[middle:]


def test_invalid_mp3_still_fails_before_pacing(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"invalid mp3")
    with pytest.raises(tts.TTSError, match="MP3"):
        tts._pace_breaths(audio, ((Word(0, 1, "앞"),), (Word(1.86, 2.5, "뒤"),)), ["앞", "뒤"])
    assert audio.read_bytes() == b"invalid mp3"


def test_words_are_matched_to_the_script_in_order():
    words = (Word(0.0, 0.5, "같은"), Word(0.5, 1.0, "말"), Word(1.0, 1.5, "같은"))

    # 두 번째 "같은"은 앞엣것을 다시 집지 않고 뒤에서 찾는다.
    assert tts.locate("같은 말 같은 자리", words) == [0, 3, 5]
    with pytest.raises(tts.TTSError, match="찾지 못했습니다"):
        tts.locate("다른 원고", words)


def test_scene_pauses_differ_by_where_the_break_falls():
    """한 값으로 고정하면 어디서나 똑같이 끊겨 사람이 읽는 리듬이 아니다."""
    gaps = tts.scene_pauses(4)

    assert gaps == (tts.OPENING_PAUSE_SECONDS, tts.TOPIC_PAUSE_SECONDS, tts.CLOSING_PAUSE_SECONDS)
    # 도입에서 첫 이슈로는 가장 짧게, 마무리 고지문 앞에서 가장 길게 쉰다.
    assert gaps[0] < gaps[1] < gaps[-1]
    assert tts.scene_pauses(1) == ()


def test_a_long_sentence_inside_a_scene_gets_an_extra_breath(tmp_path):
    """장면 경계만 넓히면 긴 문장이 끝나도 곧바로 다음 문장이 밀고 들어온다."""
    narration = "9월 WTI 90달러 이하 쪽은 사실상 굳어진 분위기입니다. 원유 가격은 에너지 비용과 연결됩니다."
    words = (Word(0.0, 0.5, "9월"), Word(0.5, 1.0, "WTI"), Word(1.0, 1.6, "90달러"),
             Word(1.6, 2.0, "이하"), Word(2.0, 2.4, "쪽은"), Word(2.4, 2.9, "사실상"),
             Word(2.9, 3.2, "굳어진"), Word(3.2, 3.6, "분위기입니다"),
             Word(4.46, 5.0, "원유"), Word(5.0, 5.4, "가격은"), Word(5.4, 6.0, "에너지"),
             Word(6.0, 6.5, "비용과"), Word(6.5, 7.2, "연결됩니다"))
    frames = [bytes([n % 251 or 7]) * 140 for n in range(1, 401)]
    frames[160:190] = [b"\x00" * 140] * 30
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(_cbr_mp3(frames))

    (paced,) = tts._pace_breaths(audio, (words,), [narration])

    added = round((tts.SENTENCE_PAUSE_SECONDS - .86) / tts._FRAME_SECONDS)
    assert paced[:8] == words[:8]                       # 앞 문장은 제자리다
    assert paced[8].start == pytest.approx(4.46 + added * tts._FRAME_SECONDS)


def test_a_short_sentence_keeps_the_voice_own_breath(tmp_path):
    """짧은 문장 뒤의 0.86초는 그대로가 자연스럽다 — 넓히면 뚝뚝 끊긴다."""
    audio = tmp_path / "voice.mp3"
    original = _cbr_mp3([bytes([n % 251 or 7]) * 140 for n in range(1, 201)])
    audio.write_bytes(original)
    words = (Word(0.0, 0.6, "짧다"), Word(1.46, 2.0, "다음"))

    assert tts._pace_breaths(audio, (words,), ["짧다. 다음"]) == (words,)
    assert audio.read_bytes() == original
