import re
from dataclasses import replace

from PIL import Image, ImageDraw

from polymarket_shorts import render
from polymarket_shorts.render import (
    CAPTION_MARGIN_V, FOOTER_Y, HEIGHT, PROGRESS_Y, SAFE_BOTTOM, WIDTH, render_frame,
)
from polymarket_shorts.scenario import Scenario, Scene
from polymarket_shorts.tts import TTSError, Word
import pytest

from conftest import requires_cjk_font


@requires_cjk_font
def test_wrapping_preserves_words_and_numeric_units(cjk_font):
    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
    font = render._font(cjk_font, 78)
    title = "미국 연준 금리 인하 0회 97.5%"
    lines = render._wrap(draw, title, font, 806)
    assert " ".join(lines) == title
    assert any("0회" in line for line in lines)
    assert any("97.5%" in line for line in lines)
    assert all(draw.textlength(line, font=font) <= 806 for line in lines)


@requires_cjk_font
def test_wrapping_keeps_explicit_breaks_and_all_of_a_long_word(cjk_font):
    draw = ImageDraw.Draw(Image.new("RGB", (1080, 1920)))
    font = render._font(cjk_font, 44)
    text = "첫째 줄\n" + "아주긴원고" * 10
    lines = render._wrap(draw, text, font, 300)
    assert lines[0] == "첫째 줄"
    assert "".join(lines[1:]) == "아주긴원고" * 10
    assert all(draw.textlength(line, font=font) <= 300 for line in lines)


@requires_cjk_font
def test_render_frame_is_vertical_short_resolution(tmp_path, cjk_font):
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
        font_path=cjk_font,
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
    # 화면 문구는 한 줄씩 쌓여 뜬다 — 수치 화면 뒤에 첫 줄, 그다음 두 줄 모두.
    assert bodies == ["첫 문장\n둘째 문장", "첫 문장", "첫 문장\n둘째 문장"]
    video_filter = captured["command"][captured["command"].index("-vf") + 1]
    # Expand still frames, drift the whole card, then draw subtitles on top — the
    # cues stay put while the card floats.
    assert video_filter.startswith("fps=30,crop=")
    assert video_filter.index("crop=") < video_filter.index("subtitles=")
    assert "overlay" not in video_filter
    assert captured["command"].count("-i") == 2  # Composited frames and audio only.
    assert captured["command"][captured["command"].index("-filter_threads") + 1] == "1"
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


@requires_cjk_font
def test_captions_sit_lowest_and_the_progress_bar_moved_off_the_bottom(tmp_path, cjk_font):
    target = tmp_path / "frame.png"
    render_frame(
        Scene("consensus", "거시·통화", "EVENT 25", "본문", "내레이션", source_note="09.13 15:00"),
        target, font_path=cjk_font, index=2, total=5, transparent=True,
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


def test_a_long_sentence_splits_evenly_instead_of_leaving_a_scrap():
    """앞에서부터 한도까지 채우면 꼬리에 "분위기입니다." 한 조각만 남는다."""
    narration = "반면 9월 WTI 100달러 이상 쪽은 다섯 번에 한 번꼴로 봅니다."
    words = tuple(
        Word(start, start + .4, text)
        for start, text in zip(
            (0.0, 0.5, 1.0, 1.5, 2.2, 2.7, 3.2, 3.7, 4.0, 4.5),
            ("반면", "9월", "WTI", "100달러", "이상", "쪽은", "다섯", "번에", "한", "번꼴로"),
        )
    ) + (Word(5.0, 5.6, "봅니다"),)

    (phrases,) = render._phrases([narration], (words,), 7.0)

    assert len(phrases) == 2
    # 두 문구의 길이가 비슷하고, 어느 쪽도 어절을 반토막 내지 않는다.
    assert abs(len(phrases[0].text) - len(phrases[1].text)) <= 6
    assert " ".join(phrase.text for phrase in phrases) == narration


def test_a_sentence_that_fits_stays_in_one_cue():
    narration = "원유 가격은 에너지 비용과 연결됩니다."
    words = tuple(
        Word(start, start + .4, text)
        for start, text in zip((0.0, 0.5, 1.0, 1.5), ("원유", "가격은", "에너지", "비용과"))
    ) + (Word(2.0, 2.6, "연결됩니다"),)

    (phrases,) = render._phrases([narration], (words,), 4.0)

    assert [phrase.text for phrase in phrases] == [narration]


def test_the_metric_counts_up_from_zero_before_it_settles():
    """정지 카드가 20초씩 멈춰 있으면 화면이 죽는다. 수치는 차오르며 들어온다."""
    scene = Scene("consensus", "10월 금리 결정", "01 · 거시·통화", "본문", "멘트",
                  metric="55%", probability=.55)

    beats = render._countup(scene, 3.0)

    assert [beat for beat, _, _ in beats] == ["metric"] * (render.COUNTUP_STEPS + 1)
    assert sum(hold for _, hold, _ in beats) == pytest.approx(3.0)
    assert beats[0][2].metric == "0%" and beats[0][2].probability == 0
    # 올라가는 동안에는 정수만 보여 주고, 확정된 값은 마지막에 한 번 제대로 선다.
    assert all(re.fullmatch(r"\d+%", shown.metric) for _, _, shown in beats[:-1])
    assert beats[-1][2] == scene


def test_a_short_beat_or_a_word_metric_stays_a_single_frame():
    scene = Scene("consensus", "제목", "기준", "본문", "멘트", metric="55%", probability=.55)

    assert render._countup(scene, render.COUNTUP_MIN_BEAT - .01) == [("metric", render.COUNTUP_MIN_BEAT - .01, scene)]
    words = replace(scene, metric="조건", kind="outro")
    assert render._countup(words, 9.0) == [("metric", 9.0, words)]


def test_captions_never_cut_a_word_the_voice_reported_in_pieces():
    """edge-tts는 "25bp"를 "25"와 "bp"로 나눠 돌려준다. 그 사이는 끊지 않는다."""
    narration = "10월 금리 변동 없음과 10월 금리 25bp 인상, 둘 다 정확히 반반입니다."
    pieces = ("10월", "금리", "변동", "없음과", "10월", "금리", "25", "bp",
              "인상", "둘", "다", "정확히", "반반입니다")
    words = tuple(Word(n * .5, n * .5 + .4, text) for n, text in enumerate(pieces))

    (phrases,) = render._phrases([narration], (words,), 8.0)

    assert len(phrases) > 1  # 이 길이는 한 화면에 담기지 않는다
    for phrase in phrases:
        assert not phrase.text.endswith("25") and not phrase.text.startswith("bp")
    assert " ".join(phrase.text for phrase in phrases) == narration


def test_a_caption_prefers_the_comma_the_speaker_already_pauses_at():
    narration = "여기 숫자는 사람들의 전망일 뿐, 정해진 결과도 투자 조언도 아닙니다."
    pieces = ("여기", "숫자는", "사람들의", "전망일", "뿐", "정해진", "결과도", "투자",
              "조언도", "아닙니다")
    words = tuple(Word(n * .5, n * .5 + .4, text) for n, text in enumerate(pieces))

    (phrases,) = render._phrases([narration], (words,), 7.0)

    assert [phrase.text for phrase in phrases] == [
        "여기 숫자는 사람들의 전망일 뿐,", "정해진 결과도 투자 조언도 아닙니다.",
    ]
