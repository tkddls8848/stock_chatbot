from polymarket_shorts import tts


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
