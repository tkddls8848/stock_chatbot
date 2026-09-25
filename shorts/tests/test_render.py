import re
from dataclasses import replace

from PIL import Image, ImageDraw

from polymarket_shorts import render
from polymarket_shorts.media import ASSET_DIR
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


@requires_cjk_font
def test_video_preserves_audio_even_over_target_and_adds_tail(tmp_path, monkeypatch, cjk_font):
    scene = Scene("consensus", "제목", "기준", "첫 줄\n둘째 줄", "내레이션",
                  options=(("첫 줄", "40%", .4), ("둘째 줄", "60%", .6)))
    scenario = Scenario("2026-09-05", "g1", "now", (scene,))
    audio = tmp_path / "voice.mp3"
    output = tmp_path / "short.mp4"
    audio.touch()
    captured = {}

    monkeypatch.setattr(render, "probe_duration", lambda *args, **kwargs: 100.0)
    revealed = []
    def fake_frame(scene, path, **kwargs):
        revealed.append((kwargs["shown"], tuple(row[1] for row in scene.options)))
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
        font_path=cjk_font,
        ffmpeg_bin="ffmpeg",
        ffprobe_bin="ffprobe",
        max_duration=30,
    )

    position = captured["command"].index("-t")
    assert captured["command"][position + 1] == "100.600"
    assert duration == 100.6
    assert "-shortest" not in captured["command"]
    assert "apad=pad_dur=0.6" in captured["command"]
    # 선택지는 한 줄씩 쌓여 뜬다: 질문만 선 화면 → 첫 줄(차오름) → 두 줄(차오름).
    assert [shown for shown, _ in revealed] == (
        [0] + [1] * (render.COUNTUP_STEPS + 1) + [2] * (render.COUNTUP_STEPS + 1)
    )
    # 새로 뜬 줄의 숫자만 차오르고, 이미 선 줄의 값은 흔들리지 않는다.
    assert revealed[1][1][0] == "0%" and revealed[render.COUNTUP_STEPS + 1][1] == ("40%", "60%")
    assert all(row[1][0] == "40%" for row in revealed[render.COUNTUP_STEPS + 1:])
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


@requires_cjk_font
def test_written_captions_keep_the_phrase_times(tmp_path, cjk_font):
    target = tmp_path / "phrases.srt"

    render._write_captions(((render.Phrase(0.05, 2.65, "앞 구절"), render.Phrase(2.65, 6.0, "뒷 구절.")),),
                           target, font_path=cjk_font)

    assert target.read_text(encoding="utf-8") == (
        "1\n00:00:00,050 --> 00:00:02,650\n앞 구절\n\n"
        "2\n00:00:02,650 --> 00:00:06,000\n뒷 구절.\n"
    )


@requires_cjk_font
def test_written_captions_break_korean_lines_between_words(tmp_path, cjk_font):
    """libass는 한글을 아무 글자에서나 끊는다 — "…없" / "음과 …"가 실제로 나왔다."""
    target = tmp_path / "phrases.srt"
    text = "10월 금리 변동 없음과 10월 금리 25bp 인상,"

    render._write_captions(((render.Phrase(0.0, 3.0, text),),), target, font_path=cjk_font)

    lines = target.read_text(encoding="utf-8").splitlines()[2:]
    assert len(lines) == 2 and " ".join(lines) == text
    assert render._subtitle_filter(target).count("WrapStyle=2") == 1


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
                  options=(("10월 금리 변동 없음", "55%", .55),))

    beats = render._countup(scene, 3.0, shown=1)

    assert [beat for beat, _, _, _ in beats] == ["option-1"] * (render.COUNTUP_STEPS + 1)
    assert sum(hold for _, hold, _, _ in beats) == pytest.approx(3.0)
    assert beats[0][2].options[0][1:] == ("0%", 0)
    # 올라가는 동안에는 정수만 보여 주고, 확정된 값은 마지막에 한 번 제대로 선다.
    assert all(re.fullmatch(r"\d+%", shown.options[0][1]) for _, _, shown, _ in beats[:-1])
    assert beats[-1][2] == scene


def test_a_short_beat_or_a_word_metric_stays_a_single_frame():
    scene = Scene("consensus", "제목", "기준", "본문", "멘트", options=(("동결", "55%", .55),))
    brief = render.COUNTUP_MIN_BEAT - .01

    assert render._countup(scene, brief, shown=1) == [("option-1", brief, scene, 1)]
    words = replace(scene, options=(("동결", "조건", .55),))
    assert render._countup(words, 9.0, shown=1) == [("option-1", 9.0, words, 1)]


def test_a_scene_without_choices_still_sets_its_title_before_the_words_land():
    """도입·마무리도 한 장으로 멈춰 있지 않는다 — 제목이 먼저 서고 문구가 얹힌다."""
    scene = Scene("outro", "확률은 예측입니다", "마무리", "질문마다 조건이 다릅니다.", "멘트")

    beats = render._beats(scene, 8.0)

    assert [(beat, shown) for beat, _, _, shown in beats] == [("open", None), ("card", None)]
    assert beats[0][2].body == "" and beats[1][2] == scene
    assert sum(hold for _, hold, _, _ in beats) == pytest.approx(8.0)
    # 짧은 장면은 나누지 않는다.
    assert render._beats(scene, 1.0) == [("card", 1.0, scene, None)]


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


@requires_cjk_font
def test_a_choice_is_drawn_as_a_name_a_big_probability_and_a_gauge(tmp_path, cjk_font):
    """원자료 형식 그대로였던 "…: 예 99.95%, 아니오 0.05%" 두 줄을 대신한다."""
    target = tmp_path / "frame.png"
    scene = Scene("consensus", "9월 WTI 원유 가격", "01 · 기타 경제·금융", "본문", "멘트",
                  options=(("9월 WTI 90달러 이하", "99.95%", .9995),
                           ("9월 WTI 100달러 이상", "22%", .22)))

    render_frame(scene, target, font_path=cjk_font, index=2, total=4, transparent=True)

    with Image.open(target) as image:
        pixels = image.convert("RGBA").load()
        def filled(y):
            return [x for x in range(render.SAFE_LEFT, render.BODY_RIGHT + 1)
                    if pixels[x, y][:3] == (212, 168, 79)]
        height = (render.BODY_BOTTOM - render.BODY_TOP) / 2
        # 거의 확실한 쪽은 막대가 끝까지 차고, 다섯에 하나쯤인 쪽은 앞자락만 찬다.
        first = filled(round(render.BODY_TOP + height * render.OPTION_BAR_TOP) + 8)
        second = filled(round(render.BODY_TOP + height * (1 + render.OPTION_BAR_TOP)) + 8)
        assert max(first) >= render.BODY_RIGHT - 4
        assert .18 < (max(second) - render.SAFE_LEFT) / (render.BODY_RIGHT - render.SAFE_LEFT) < .26


@requires_cjk_font
def test_pending_choices_keep_an_empty_gauge_so_nothing_jumps(tmp_path, cjk_font):
    scene = Scene("consensus", "제목", "기준", "본문", "멘트",
                  options=(("첫 선택지", "40%", .4), ("둘째 선택지", "60%", .6)))
    one, two = tmp_path / "one.png", tmp_path / "two.png"

    render_frame(scene, one, font_path=cjk_font, index=1, total=2, shown=1, transparent=True)
    render_frame(scene, two, font_path=cjk_font, index=1, total=2, shown=2, transparent=True)

    def rows(path, colours):
        with Image.open(path) as image:
            pixels = image.convert("RGBA").load()
            return {y for y in range(render.BODY_TOP, render.BODY_BOTTOM)
                    if any(pixels[x, y][:3] in colours
                           for x in range(render.SAFE_LEFT, render.BODY_RIGHT, 3))}

    ink, track = {(245, 241, 232)}, {(43, 58, 65)}
    middle = render.BODY_TOP + (render.BODY_BOTTOM - render.BODY_TOP) / 2
    # 뜨기 전에도 빈 게이지 홈이 아래 절반의 자리를 잡고 있다.
    assert max(rows(one, track)) > middle
    # 글자는 첫 줄에만 있고, 둘째 줄이 떠도 첫 줄은 같은 자리에 그대로다.
    assert max(rows(one, ink)) < middle
    assert rows(one, ink) <= rows(two, ink) and max(rows(two, ink)) > middle


@requires_cjk_font
def test_the_background_photograph_covers_the_whole_frame(tmp_path, cjk_font):
    """2026-09-23 산출물은 아래 1070px이 #101B20 한 색이었다."""
    target = tmp_path / "frame.png"
    scene = Scene("outro", "확률은 예측입니다", "마무리", "판정 규칙은 직접 확인하세요.", "멘트")

    render_frame(scene, target, font_path=cjk_font, index=4, total=4,
                 background_path=ASSET_DIR / "global-trade.png")

    with Image.open(target) as image:
        pixels = image.convert("RGB").load()
        for y in (900, 1300, 1700, 1900):
            band = {pixels[x, y] for x in range(0, WIDTH, 40)}
            assert len(band) > 6, f"y={y}에서 배경이 단색이다: {band}"


@requires_cjk_font
def test_each_scene_sees_the_same_asset_differently(tmp_path, cjk_font):
    """배경 소스가 둘뿐이라 장면마다 크롭·방향·색조로 변주한다."""
    asset = ASSET_DIR / "financial-city.png"
    frames = [render._background(asset, index=n, total=4, accent=accent)
              for n, accent in enumerate(("gold", "blue", "red", "gold"), start=1)]

    samples = [tuple(frame.getpixel((x, y)) for x in (120, 540, 960) for y in (400, 1200, 1800))
               for frame in frames]
    assert len(set(samples)) == len(samples)
    # 배경이 없으면 단색으로 떨어지되 크기는 같다.
    assert render._background(None, index=1, total=4, accent="gold").size == (WIDTH, HEIGHT)


def test_captions_are_large_bold_and_sit_in_the_lower_third(tmp_path):
    style = render._subtitle_filter(tmp_path / "phrases.srt")

    assert f"FontSize={render.CAPTION_FONT_SIZE}" in style and "Bold=1" in style
    assert render.CAPTION_FONT_SIZE >= 54          # 예전 38은 화면 중간의 주석처럼 보였다
    assert f"MarginV={CAPTION_MARGIN_V}" in style  # 아래 끝이 안전 영역 바닥이다
    # 두 줄이면 자막 띠는 하단 1/3(1280 아래) 안에 들어온다.
    assert HEIGHT - CAPTION_MARGIN_V - 2 * round(render.CAPTION_FONT_SIZE * 1.25) > HEIGHT * 2 / 3


def test_a_spoken_phrase_fits_two_caption_lines():
    """자막이 커진 만큼 한 덩어리를 짧게 끊는다 — 세 줄이 되면 화면을 덮는다."""
    narration = "9월에 WTI 원유 가격은 얼마까지 갈까요? 원유 가격은 에너지 비용과 연결됩니다."
    pieces = ("9월에", "WTI", "원유", "가격은", "얼마까지", "갈까요", "원유", "가격은",
              "에너지", "비용과", "연결됩니다")
    words = tuple(Word(n * .5, n * .5 + .4, text) for n, text in enumerate(pieces))

    (phrases,) = render._phrases([narration], (words,), 8.0)

    assert all(len(phrase.text) <= render._PHRASE_CHARS + 6 for phrase in phrases)
    assert " ".join(phrase.text for phrase in phrases) == narration


def test_a_caption_does_not_split_a_word_from_the_particle_that_binds_it():
    """"…숫자와" / "함께 짚어"처럼 끊으면 한 덩어리가 갈라진다."""
    narration = "오늘은 이런 질문 2개를 숫자와 함께 짚어 보겠습니다."
    pieces = ("오늘은", "이런", "질문", "2개를", "숫자와", "함께", "짚어", "보겠습니다")
    words = tuple(Word(n * .5, n * .5 + .4, text) for n, text in enumerate(pieces))

    (phrases,) = render._phrases([narration], (words,), 6.0)

    assert not any(phrase.text.endswith(("와", "과", "의")) for phrase in phrases)
    assert " ".join(phrase.text for phrase in phrases) == narration


def test_a_caption_prefers_the_clause_ending_the_speaker_pauses_at():
    narration = "질문마다 조건이 다르니 판정 규칙은 직접 확인하세요."
    pieces = ("질문마다", "조건이", "다르니", "판정", "규칙은", "직접", "확인하세요")
    words = tuple(Word(n * .5, n * .5 + .4, text) for n, text in enumerate(pieces))

    (phrases,) = render._phrases([narration], (words,), 6.0)

    assert [phrase.text for phrase in phrases] == [
        "질문마다 조건이 다르니", "판정 규칙은 직접 확인하세요.",
    ]
