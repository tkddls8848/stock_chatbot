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


RATE = tts._SAMPLE_RATE


def _pcm(monkeypatch, tmp_path, pattern):
    """(초, 소리 여부) 목록으로 PCM을 만들고 디코드·인코드를 메모리로 바꿔 끼운다.

    말소리는 1000, 무음은 0이다. 저장된 결과는 `store["out"]`에 남는다.
    """
    from array import array

    samples = array("h")
    for seconds, loud in pattern:
        samples.extend([1000 if loud else 0] * round(seconds * RATE))
    store = {"in": samples}
    monkeypatch.setattr(tts, "_decode", lambda path, ffmpeg_bin: array("h", store["in"]))
    monkeypatch.setattr(tts, "_encode", lambda out, path, ffmpeg_bin: store.update(out=out))
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"mp3")
    return audio, store


def test_long_scene_break_is_shortened_inside_the_silence(tmp_path, monkeypatch):
    # 말 1초 → 무음 2초 → 말 1초. 장면 경계(마무리) 목표는 CLOSING_PAUSE_SECONDS다.
    audio, store = _pcm(monkeypatch, tmp_path, [(1, True), (2, False), (1, True)])
    scenes = ((Word(0.0, 1.0, "앞"),), (Word(3.0, 4.0, "뒤"),))

    paced = tts._pace_breaths(audio, scenes, ["앞", "뒤"])

    removed = 2.0 - tts.CLOSING_PAUSE_SECONDS
    assert paced[0] == scenes[0]
    assert paced[1][0].start == pytest.approx(3.0 - removed, abs=0.001)
    out = store["out"]
    assert len(out) == pytest.approx(4 * RATE - removed * RATE, abs=2)
    # 말소리는 한 샘플도 깎이지 않는다 — 잘라 낸 것은 쉼 가운데뿐이다.
    assert sum(1 for v in out if v == 1000) == 2 * RATE


def test_short_scene_break_is_widened_with_silence(tmp_path, monkeypatch):
    audio, store = _pcm(monkeypatch, tmp_path, [(1, True), (0.3, False), (1, True)])
    scenes = ((Word(0.0, 1.0, "앞"),), (Word(1.3, 2.3, "뒤"),))

    paced = tts._pace_breaths(audio, scenes, ["앞", "뒤"])

    added = tts.CLOSING_PAUSE_SECONDS - 0.3
    assert paced[1][0].start == pytest.approx(1.3 + added, abs=0.001)
    assert sum(1 for v in store["out"] if v == 1000) == 2 * RATE


def test_speech_edges_are_guarded_when_the_gap_is_tight(tmp_path, monkeypatch):
    # 쉼이 목표보다 길지만 여유(양쪽 _GUARD_SECONDS)를 빼면 덜어 낼 것이 거의 없다.
    gap = 2 * tts._GUARD_SECONDS + 0.01
    audio, store = _pcm(monkeypatch, tmp_path, [(1, True), (gap, False), (1, True)])
    words = (Word(0.0, 1.0, "짧다"), Word(1.0 + gap, 2.0 + gap, "다음"))
    monkeypatch.setattr(tts, "SENTENCE_PAUSE_SECONDS", 0.05)

    (paced,) = tts._pace_breaths(audio, (words,), ["짧다. 다음"])

    assert paced[1].start == pytest.approx(words[1].start - 0.01, abs=0.001)
    assert sum(1 for v in store["out"] if v == 1000) == 2 * RATE


def test_every_sentence_end_inside_a_scene_is_paced(tmp_path, monkeypatch):
    """edge-tts의 0.86초 호흡은 쇼츠에 길다. 장면 안 문장 끝도 목표로 맞춘다."""
    audio, _ = _pcm(monkeypatch, tmp_path, [(0.6, True), (0.86, False), (0.6, True)])
    words = (Word(0.0, 0.6, "짧다"), Word(1.46, 2.06, "다음"))

    (paced,) = tts._pace_breaths(audio, (words,), ["짧다. 다음"])

    assert paced[1].start == pytest.approx(1.46 - (0.86 - tts.SENTENCE_PAUSE_SECONDS), abs=0.001)


def test_splices_are_faded_so_the_wave_does_not_jump(tmp_path, monkeypatch):
    # 쉼 한가운데에도 옅은 소리(500)가 깔려 있으면 이음매 양쪽이 페이드로 이어진다.
    from array import array

    samples = array("h", [500] * (3 * RATE))
    monkeypatch.setattr(tts, "_decode", lambda path, ffmpeg_bin: array("h", samples))
    store = {}
    monkeypatch.setattr(tts, "_encode", lambda out, path, ffmpeg_bin: store.update(out=out))
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"mp3")
    scenes = ((Word(0.0, 0.5, "앞"),), (Word(2.5, 3.0, "뒤"),))

    tts._pace_breaths(audio, scenes, ["앞", "뒤"])

    out = store["out"]
    jumps = max(abs(out[i + 1] - out[i]) for i in range(len(out) - 1))
    assert jumps < 100   # 페이드 없이 붙이면 500 → 0 → 500처럼 튄다


def test_decode_failure_is_a_tts_error(tmp_path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"invalid mp3")
    with pytest.raises(tts.TTSError):
        tts._pace_breaths(audio, ((Word(0, 1, "앞"),), (Word(1.86, 2.5, "뒤"),)), ["앞", "뒤"],
                          ffmpeg_bin="definitely-not-ffmpeg")


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
