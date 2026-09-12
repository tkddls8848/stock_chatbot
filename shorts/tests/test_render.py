from PIL import Image

from polymarket_shorts import render
from polymarket_shorts.render import HEIGHT, WIDTH, find_font, render_frame
from polymarket_shorts.scenario import Scenario, Scene


def test_render_frame_is_vertical_short_resolution(tmp_path):
    target = tmp_path / "frame.png"
    render_frame(
        Scene(
            kind="consensus",
            title="거시·통화",
            kicker="EVENT 25 · 24H 2.1M달러",
            body="참여자들은 금리 경로를 두고 여전히 판단이 갈리고 있습니다.",
            narration="거시 통화입니다.",
        ),
        target,
        font_path=find_font(),
        index=2,
        total=5,
    )

    with Image.open(target) as image:
        assert image.size == (WIDTH, HEIGHT)
        assert image.mode == "RGB"


def test_video_is_capped_to_the_audio_duration(tmp_path, monkeypatch):
    scene = Scene("intro", "제목", "기준", "본문", "내레이션")
    scenario = Scenario("2026-09-05", "g1", "now", (scene,))
    audio = tmp_path / "voice.mp3"
    subtitles = tmp_path / "captions.vtt"
    output = tmp_path / "short.mp4"
    audio.touch()
    subtitles.touch()
    captured = {}

    monkeypatch.setattr(render, "probe_duration", lambda *args, **kwargs: 100.0)
    monkeypatch.setattr(render, "render_frame", lambda scene, path, **kwargs: path.touch())

    def fake_run(command, **kwargs):
        captured["command"] = command
        output.touch()

        class Result:
            returncode = 0
            stderr = ""
            stdout = ""

        return Result()

    monkeypatch.setattr(render.subprocess, "run", fake_run)

    duration = render.render_video(
        scenario,
        audio_path=audio,
        subtitle_path=subtitles,
        output_path=output,
        work_dir=tmp_path,
        font_path=tmp_path / "font.ttf",
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
        max_duration=179,
    )

    position = captured["command"].index("-t")
    assert captured["command"][position + 1] == "100.000"
    assert duration == 100.0
