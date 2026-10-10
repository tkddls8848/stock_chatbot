from datetime import datetime
from pathlib import Path

import pytest

from polymarket_shorts.longform import (
    LongformError, build_longform, chapters, check_script, report_window, sentences, youtube_metadata,
)
from polymarket_shorts.longform_render import HEIGHT, WIDTH, beats, caption_frame, render_longform_frame
from polymarket_shorts.render import find_font
from polymarket_shorts.tts import Word, spoken_text


REPORT = {
    "id": "report:US:2026-10-10T08:00:00+09:00", "kind": "report", "market": "US",
    "title": "미국 시장상황 보고서 · 20:01~08:00 UTC +9",
    "text": ("미국 주식시장은 인공지능(AI) 기대감으로 혼조세를 보이고 있다. 10년물 금리는 5.3%를 넘었다. "
             "시장의 변동성은 제한적인 수준을 유지하고 있다.\n\n"
             "이전 보고서에서 지적된 기술주 중심의 상승세는 지속되고 있었지만 최근 기사들에 따르면 기술주가 약세를 보이고 있다. "
             "시장의 변동성은 제한적인 수준을 유지하고 있다.\n\n"
             "오늘 장에서는 기업들의 실적 발표가 오늘 장에 어떤 영향을 미칠지 주목해야 한다."),
    "published_at": "2026-10-10T08:00:00+09:00", "source": "눈치 시장상황 보고서",
}
NEWS = [{"id": f"news:US:{n}", "kind": "news", "market": "US", "title": f"기사 제목 {n}",
         "text": f"Stocks rise {n}% as bank earnings near", "source": "Reuters",
         "published_at": f"2026-10-10T0{n}:00:00+09:00", "url": ""} for n in range(1, 4)]
DAILY = [{"date": f"2026-10-{day:02d}", "avg_sentiment": value, "count": 40, "summary": "기술주가 강세를 보였다."}
         for day, value in ((5, .1), (6, .34), (7, -.22), (8, -.16), (9, .23))]
ANALYSIS = [
    {"topic": "AI 기대와 금리 부담", "text": "미국 증시는 AI 기대 속에 혼조세입니다. 10년물 금리는 5.3%를 넘었습니다."},
    {"topic": "기술주 상승 탄력 둔화", "text": "직전에 본 기술주 상승세는 꺾였습니다. 변동성은 아직 크지 않습니다."},
]
PAYLOAD = {"analysis": ANALYSIS, "headlines": [
    {"id": "news:US:1", "summary": "은행 실적 발표를 앞두고 주가가 1% 올랐습니다."},
    {"id": "news:US:2", "summary": ""},
    {"id": "news:US:3", "summary": "기사 제목 3입니다."},
]}


def test_window_crosses_midnight_and_long_spans():
    assert report_window(REPORT) == (datetime.fromisoformat("2026-10-09T20:01:00+09:00"),
                                     datetime.fromisoformat("2026-10-10T08:00:00+09:00"))
    long = {**REPORT, "title": "미국 시장상황 보고서 · 10-08 15:00~10-10 08:00 UTC +9"}
    assert report_window(long)[0] == datetime.fromisoformat("2026-10-08T15:00:00+09:00")
    with pytest.raises(LongformError):
        report_window({**REPORT, "title": "미국 시장상황 보고서"})


def test_sentences_keep_decimals_together():
    assert sentences("금리는 5.3%를 넘었다. 다음 문장이다.") == ("금리는 5.3%를 넘었다.", "다음 문장이다.")


def test_script_check_keeps_short_analysis_and_drops_weak_summaries():
    script = check_script(PAYLOAD, REPORT["text"], NEWS)
    assert [row["topic"] for row in script["analysis"]] == ["AI 기대와 금리 부담", "기술주 상승 탄력 둔화"]
    # 원제에 있는 숫자는 쓸 수 있고, 제목을 되풀이한 요약은 빼서 매체만 말하게 한다.
    assert script["summaries"] == {"news:US:1": "은행 실적 발표를 앞두고 주가가 1% 올랐습니다.",
                                   "news:US:2": "", "news:US:3": ""}


@pytest.mark.parametrize("analysis, reason", [
    # 보고서에 없는 숫자
    ([{"topic": "금리 부담", "text": "10년물 금리는 6.1%를 넘었습니다. 시장은 혼조세입니다."}], "숫자"),
    # 보고서 문장을 거의 그대로 옮겨 적음(줄여 쓰지 않음)
    ([{"topic": "기술주 약세", "text": "이전 보고서에서 지적된 기술주 중심의 상승세는 지속되고 있었지만 최근 기사들에 따르면 "
                                     "기술주가 약세를 보이고 있습니다. 시장은 혼조세입니다."}], "옮겼"),
    # 같은 구절을 두 번(보고서의 되풀이를 그대로 따라 함)
    ([{"topic": "오늘 장 관전 포인트", "text": "실적 발표가 오늘 장에 어떤 영향을 미칠지 봐야 합니다. "
                                         "금리 흐름이 오늘 장에 어떤 영향을 미칠지도 봐야 합니다."}], "두 번"),
    ([{"topic": "오늘 장 관전 포인트", "text": "기업 실적 발표와 연준 결정이 오늘 장에 어떤 영향을 미칠지 주목됩니다. "
                                         "유가가 오늘 장에 어떤 영향을 미칠지도 관심입니다."}], "두 번"),
    ([], "문단"),
])
def test_script_check_rejects_invented_numbers_copies_and_repeats(analysis, reason):
    with pytest.raises(LongformError, match=reason):
        check_script({**PAYLOAD, "analysis": analysis}, REPORT["text"], NEWS)


def test_order_follows_operator_script_and_report_is_one_scene():
    longform = build_longform(REPORT, NEWS, DAILY, check_script(PAYLOAD, REPORT["text"], NEWS))
    assert [segment.kind for segment in longform.segments] == [
        "intro", "agenda", "sentiment", "headlines", "report", "outro"]
    intro, agenda, sentiment, headlines, report, outro = longform.segments
    assert intro.sentences == ("10월 9일 오후 8시 1분부터 10월 10일 오전 8시까지 모인 뉴스를 바탕으로 작성한 "
                               "10월 10일 미국 시장상황을 살펴보겠습니다.",)
    assert agenda.sentences == ("본 영상에서는 최근 뉴스 감성 흐름을 먼저 보고, 미국 시장상황의 주요 헤드라인 뉴스 3개를 "
                                "짚은 다음, 시장 주요 상황에 대한 분석 내용을 소개하겠습니다.",)
    # 음수는 "마이너스"로, 숫자 뒤에는 "점"을 붙여 조사가 받침에 따라 갈리지 않게 한다. 최근 요약은 읽지 않는다.
    assert "마이너스 0.22점을 기록했습니다." in sentiment.narration and "요약" not in sentiment.narration
    assert headlines.sentences[1:] == ("기사 제목 1.", "Reuters 보도로, 은행 실적 발표를 앞두고 주가가 1% 올랐습니다.",
                                       "기사 제목 2.", "Reuters 보도입니다.", "기사 제목 3.", "Reuters 보도입니다.")
    assert headlines.marks == (None, 0, 0, 1, 1, 2, 2)
    assert report.chapter == "시장 분석" and [row["topic"] for row in report.rows] == [a["topic"] for a in ANALYSIS]
    assert report.marks == (None, 0, 0, 1, 1)
    assert outro.narration.endswith("자세한 내용은 nunchi.live에서 확인하세요.")
    assert spoken_text(outro.narration).endswith("눈치 닷 라이브에서 확인하세요.")


def test_missing_news_and_short_sentiment_drop_their_segments_and_agenda_items():
    longform = build_longform(REPORT, [], DAILY[:2], check_script({"analysis": ANALYSIS}, REPORT["text"], []))
    assert [segment.kind for segment in longform.segments] == ["intro", "agenda", "report", "outro"]
    assert longform.segments[1].sentences == ("본 영상에서는 시장 주요 상황에 대한 분석 내용을 소개하겠습니다.",)


def _segment_words(longform):
    clock, result = 0.0, []
    for segment in longform.segments:
        words = [Word(clock + n * .3, clock + n * .3 + .25, text)
                 for n, text in enumerate(spoken_text(segment.narration).split())]
        result.append(tuple(words))
        clock = words[-1].end + 1.2
    return result, clock


def test_beats_follow_the_spoken_row_and_intro_outro_are_one_screen():
    longform = build_longform(REPORT, NEWS, DAILY, check_script(PAYLOAD, REPORT["text"], NEWS))
    words, clock = _segment_words(longform)
    timeline = beats(longform, words, clock + .6)
    assert timeline[0].start == 0 and timeline[-1].end == pytest.approx(clock + .6)
    assert all(a.end == b.start for a, b in zip(timeline, timeline[1:]))
    assert [beat.segment for beat in timeline].count(0) == 1
    # 기사 장면: 여는 말(None) → 기사마다 제목·요약 두 문장이 한 줄을 짚는다.
    assert [beat.focus for beat in timeline if beat.segment == 3] == [None, 0, 1, 2]
    assert [beat.focus for beat in timeline if beat.segment == 4] == [None, 0, 1]


@pytest.mark.parametrize("segment, focus", [(0, None), (1, None), (2, None), (3, 1), (4, 0), (5, None)])
def test_frames_render_landscape(tmp_path: Path, segment, focus):
    longform = build_longform(REPORT, NEWS, DAILY, check_script(PAYLOAD, REPORT["text"], NEWS))
    target = tmp_path / "frame.png"
    render_longform_frame(longform, segment, focus, target, font_path=find_font())
    from PIL import Image
    assert Image.open(target).size == (WIDTH, HEIGHT) == (1920, 1080)


def test_caption_sits_in_the_bottom_band(tmp_path: Path):
    target = tmp_path / "caption.png"
    caption_frame("10년물 미국 국채 금리는 5.3%를 넘었습니다.", target, font_path=find_font())
    from PIL import Image
    box = Image.open(target).getchannel("A").getbbox()
    assert box and box[1] > 830 and box[3] <= 990


def test_chapters_fold_short_scenes_and_need_three():
    screens = [{"start": 0, "segment": "시작"}, {"start": 6, "segment": "목차"},
               {"start": 14, "segment": "최근 뉴스 감성"}, {"start": 40, "segment": "주요 기사"},
               {"start": 44, "segment": "주요 기사"}, {"start": 100, "segment": "시장 분석"},
               {"start": 170, "segment": "마무리"}]
    # 6초짜리 시작 뒤 목차는 접히고, 끝까지 10초가 안 남는 마무리도 빠진다.
    assert chapters(screens, 176) == [(0.0, "시작"), (14.0, "최근 뉴스 감성"), (40.0, "주요 기사"),
                                      (100.0, "시장 분석")]
    assert chapters(screens[:3], 30) == []


def test_youtube_metadata_lists_chapters_and_passes_the_upload_check():
    from dataclasses import replace

    from polymarket_shorts.config import Settings
    from polymarket_shorts.youtube import _metadata

    longform = build_longform(REPORT, NEWS, DAILY, check_script(PAYLOAD, REPORT["text"], NEWS))
    screens = [{"start": 0, "segment": "시작"}, {"start": 12, "segment": "최근 뉴스 감성"},
               {"start": 65, "segment": "주요 기사"}, {"start": 125, "segment": "시장 분석"}]
    metadata = youtube_metadata(longform, screens, 192.4)
    assert metadata["title"] == "10월 10일 미국 시장상황 | 최근 뉴스 감성 흐름 · 주요 헤드라인 뉴스 3건 · 시장 주요 상황 분석"
    assert "\n0:00 시작\n0:12 최근 뉴스 감성\n1:05 주요 기사\n2:05 시장 분석\n" in metadata["description"]
    assert metadata["description"].endswith("https://nunchi.live")
    _metadata({"youtube": metadata}, replace(Settings.from_env(), youtube_privacy="private"))
