from PIL import Image

from polymarket_shorts import render
from polymarket_shorts.render import (
    CAPTION_MARGIN_V, FOOTER_Y, HEIGHT, PROGRESS_Y, SAFE_BOTTOM, WIDTH, find_font, render_frame,
)
from polymarket_shorts.scenario import Scenario, Scene
from polymarket_shorts.tts import TTSError, Word
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
    output = tmp_path / "short.mp4"
    audio.touch()
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
        scene_words=((Word(0.1, 1.0, "내레이션"),),),
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


def test_scene_cuts_land_on_the_next_scene_first_spoken_word():
    narrations = ["거래가 많으면 확실할까요?", "거시. 연준의 결정을 봅니다."]
    # 문장 사이에는 1.1초의 쉼이 있다. 장면은 그 쉼의 어딘가가 아니라 다음 장면의
    # 첫 단어에 붙어야 한다 — 그러지 않으면 앞 장면의 말이 새 화면 위로 넘어온다.
    spoken = (
        (Word(0.10, 0.60, "거래가"), Word(0.60, 1.10, "많으면"), Word(1.10, 1.90, "확실할까요")),
        (Word(3.00, 3.40, "거시"), Word(3.40, 3.90, "연준의"),
         Word(3.90, 4.40, "결정을"), Word(4.40, 5.00, "봅니다")),
    )

    scenes = render._phrases(narrations, spoken, 6.0)

    # 새 장면은 자기 첫 단어보다 SCENE_LEAD만큼 앞에서 열린다 — 넓혀 둔 쉼의
    # 뒤쪽이 새 화면 위에서 흐르고, 그 뒤에 말이 시작된다.
    assert scenes[1][0].start == pytest.approx(3.0 - render.SCENE_LEAD)
    assert render._scene_durations(scenes, 6.0) == pytest.approx([2.45, 3.55])
    with pytest.raises(TTSError, match="찾지 못했습니다"):
        render._phrases(narrations, (spoken[0], (Word(3.0, 3.6, "없는말"),)), 6.0)


def test_split_phrases_start_when_the_word_is_spoken_not_at_a_character_share():
    narration = "호르무즈 해협의 통행 정상화 가능성은 20.5%로 낮게 나타납니다."
    # 말은 4.6초에 끝나고 6.0초까지는 쉼이다. 예전에는 문장 큐의 끝(쉼 포함)까지를
    # 글자 수로 나눠 뒷 구절이 말보다 늦게 떴다. 이제 분할점은 단어 시작에서 온다.
    words = (
        Word(0.10, 0.70, "호르무즈"), Word(0.70, 1.20, "해협의"), Word(1.20, 1.60, "통행"),
        Word(1.60, 2.10, "정상화"), Word(2.10, 2.70, "가능성은"), Word(2.70, 3.50, "20.5%로"),
        Word(3.50, 3.90, "낮게"), Word(3.90, 4.60, "나타납니다"),
    )

    (phrases,) = render._phrases([narration], (words,), 6.0)

    assert len(phrases) == 2
    assert phrases[1].start == pytest.approx(2.70 - render.CAPTION_LEAD)
    # 문구는 빈틈도 겹침도 없이 이어지고, 원고 글자를 하나도 잃지 않는다.
    assert phrases[0].end == phrases[1].start
    assert (phrases[0].start, phrases[-1].end) == (0.0, 6.0)
    assert " ".join(phrase.text for phrase in phrases) == narration


def test_written_captions_keep_the_phrase_times(tmp_path):
    target = tmp_path / "phrases.srt"

    render._write_captions(((render.Phrase(0.05, 2.65, "앞 구절"), render.Phrase(2.65, 6.0, "뒷 구절.")),), target)

    assert target.read_text(encoding="utf-8") == (
        "1\n00:00:00,050 --> 00:00:02,650\n앞 구절\n\n"
        "2\n00:00:02,650 --> 00:00:06,000\n뒷 구절.\n"
    )


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
