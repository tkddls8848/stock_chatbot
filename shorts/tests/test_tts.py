from polymarket_shorts import render, tts


def test_negative_rate_is_passed_as_one_cli_argument(tmp_path, monkeypatch):
    captured = {}
    audio = tmp_path / "voice.mp3"
    subtitles = tmp_path / "captions.vtt"

    def fake_run(command, **kwargs):
        captured["command"] = command
        audio.touch()
        subtitles.touch()

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(tts.subprocess, "run", fake_run)

    tts.synthesize(
        "테스트",
        audio_path=audio,
        subtitle_path=subtitles,
        voice="ko-KR-SunHiNeural",
        rate="-4%",
    )

    assert "--rate=-4%" in captured["command"]
    assert "--rate" not in captured["command"]


def test_sections_trim_edge_silence_and_leave_one_pause(tmp_path, monkeypatch):
    import struct
    import wave
    from types import SimpleNamespace
    import pytest

    def fake_synthesize(text, *, audio_path, subtitle_path, **kwargs):
        audio_path.touch()
        subtitle_path.write_text(
            f"1\n00:00:00,200 --> 00:00:00,300\n{text}\n", encoding="utf-8",
        )

    # edge-tts가 실제로 돌려주는 모양: 앞 0.2초·뒤 0.9초가 진폭 0이다.
    lead, speech, tail = 4800, 2400, 21600
    pcm = struct.pack("<%dh" % (lead + speech + tail), *([0] * lead + [9000] * speech + [0] * tail))
    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=pcm))
    audio, captions, timings = tts.synthesize_sections(
        ["첫 문장", "둘째 문장"], work_dir=tmp_path, voice="test", rate="+0%", ffmpeg_bin="ffmpeg",
    )

    # 남는 것은 말 0.1초와 앞뒤 여유뿐이고, 문단 사이 간격은 SCENE_PAUSE_SECONDS 하나다.
    kept = 0.1 + tts._LEAD_KEEP_SECONDS + tts._TAIL_KEEP_SECONDS
    assert timings == pytest.approx((kept + tts.SCENE_PAUSE_SECONDS,) * 2)
    with wave.open(str(audio)) as recorded:
        frames = recorded.getnframes()
        assert frames == round((kept + tts.SCENE_PAUSE_SECONDS) * tts.SAMPLE_RATE) * 2
        samples = struct.unpack("<%dh" % frames, recorded.readframes(frames))
    # 말은 그대로 남고, 문단이 바뀌는 자리는 한 덩어리 무음이다.
    assert max(samples) == 9000
    gap = samples[round(kept * tts.SAMPLE_RATE):round((kept + tts.SCENE_PAUSE_SECONDS) * tts.SAMPLE_RATE)]
    assert set(gap) == {0}
    # 자막 시각도 걷어낸 앞 무음만큼 당겨져 말이 시작하는 0.04초를 가리킨다.
    rows = render._caption_rows(captions)
    assert rows[0][:2] == pytest.approx((tts._LEAD_KEEP_SECONDS, tts._LEAD_KEEP_SECONDS + 0.1))
    assert rows[1][0] == pytest.approx(timings[0] + tts._LEAD_KEEP_SECONDS)


def test_trim_fades_a_cut_that_lands_on_speech(tmp_path):
    import struct

    # 여유 없이 말로 시작·끝나는 음성. 그대로 자르면 계단이 생겨 딸깍거린다.
    loud = struct.pack("<%dh" % 4800, *([9000] * 4800))
    pcm, lead = tts._trim(loud)
    samples = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    assert lead == 0 and len(samples) == 4800
    assert samples[0] == 0 and samples[-1] == 0
    assert 0 < samples[1] < 9000 and samples[len(samples) // 2] == 9000


def test_sections_reject_a_scene_that_is_all_silence(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest

    def fake_synthesize(text, *, audio_path, subtitle_path, **kwargs):
        audio_path.touch()
        subtitle_path.write_text("1\n00:00:00,000 --> 00:00:00,100\n무음\n", encoding="utf-8")

    monkeypatch.setattr(tts, "synthesize", fake_synthesize)
    monkeypatch.setattr(
        tts.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=bytes(9600)),
    )
    with pytest.raises(tts.TTSError, match="전부 무음"):
        tts.synthesize_sections(
            ["소리 없음"], work_dir=tmp_path, voice="test", rate="+0%", ffmpeg_bin="ffmpeg",
        )
