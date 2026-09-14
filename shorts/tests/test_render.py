from PIL import Image

from polymarket_shorts import render
from polymarket_shorts.render import (
    CAPTION_MARGIN_V, FOOTER_Y, HEIGHT, PROGRESS_Y, SAFE_BOTTOM, WIDTH, find_font, render_frame,
)
from polymarket_shorts.scenario import Scenario, Scene
import pytest


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


def test_video_preserves_audio_even_over_target_and_adds_tail(tmp_path, monkeypatch):
    scene = Scene("consensus", "제목", "기준", "첫 문장\n둘째 문장", "내레이션")
    scenario = Scenario("2026-09-05", "g1", "now", (scene,))
    audio = tmp_path / "voice.mp3"
    subtitles = tmp_path / "captions.vtt"
    output = tmp_path / "short.mp4"
    audio.touch()
    subtitles.touch()
    captured = {}

    monkeypatch.setattr(render, "probe_duration", lambda *args, **kwargs: 100.0)
    bodies = []
    def fake_frame(scene, path, **kwargs):
        bodies.append(scene.body)
        path.touch()
    monkeypatch.setattr(render, "render_frame", fake_frame)

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
        max_duration=30,
    )

    position = captured["command"].index("-t")
    assert captured["command"][position + 1] == "100.600"
    assert duration == 100.6
    assert "-shortest" not in captured["command"]
    assert "apad=pad_dur=0.6" in captured["command"]
    assert bodies == ["첫 문장\n둘째 문장", "첫 문장\n둘째 문장"]
    video_filter = captured["command"][captured["command"].index("-filter_complex") + 1]
    # Expand still frames before drawing subtitles so cues change within a scene.
    assert "[1:v]fps=30,format=rgba[fg]" in video_filter
    assert "[bg][fg]overlay=shortest=1,subtitles=" in video_filter
    assert "PlayResX=1080,PlayResY=1920" in video_filter


def test_scene_cuts_follow_tts_cues_not_text_length():
    scenes = (Scene("intro", "질문", "", "", "거래가 많으면 확실할까요?"), Scene("consensus", "거시", "", "", "거시. 연준의 결정을 봅니다."))
    scenario = Scenario("2026-09-13", "g1", "", scenes)
    rows = [(0.1, 3.0, scenes[0].narration), (5.5, 6.0, "거시."), (6.0, 10.0, "연준의 결정을 봅니다.")]
    assert render._narration_durations(
        [scene.narration for scene in scenario.scenes], rows, 10.0,
    ) == [5.5, 4.5]
    with pytest.raises(render.RenderError, match="맞출 수"):
        render._narration_durations(
            [scene.narration for scene in scenario.scenes], rows[:1], 10.0,
        )


def test_short_captions_preserve_text_and_cue_interval(tmp_path):
    text = "호르무즈 해협의 교통 정상화 가능성은 20.5%로 낮게 나타나며 참여자의 우려를 반영합니다."
    target = tmp_path / "phrases.srt"
    render._short_captions([(1.0, 9.0, text)], target)
    rows = render._caption_rows(target)
    assert len(rows) > 1
    assert " ".join(row[2] for row in rows) == text
    assert rows[0][0] == 1.0 and rows[-1][1] == 9.0


def test_captions_sit_lowest_and_the_progress_bar_moved_off_the_bottom(tmp_path):
    target = tmp_path / "frame.png"
    render_frame(
        Scene("consensus", "거시·통화", "EVENT 25", "본문", "내레이션", source_note="09.13 15:00"),
        target, font_path=find_font(), index=2, total=5, transparent=True,
    )

    # 자막 아래 끝이 안전 영역 바닥이다. 고지문은 그 위, 진행바는 헤더 옆으로 올라갔다.
    assert HEIGHT - CAPTION_MARGIN_V == SAFE_BOTTOM
    assert FOOTER_Y < SAFE_BOTTOM - 100 and PROGRESS_Y < FOOTER_Y
    style = render._subtitle_filter(tmp_path / "phrases.srt")
    assert f"MarginV={CAPTION_MARGIN_V}" in style

    with Image.open(target) as image:
        pixels = image.convert("RGBA").load()
        def accent(y):
            return sum(pixels[x, y][:3] == (212, 168, 79) for x in range(72, 879))

        assert accent(PROGRESS_Y + 2) > 200   # 진행바가 새 자리에 있다
        assert accent(1582) == 0              # 예전 자리에는 없다
        assert accent(SAFE_BOTTOM - 10) == 0  # 자막이 들어갈 띠는 비어 있다
