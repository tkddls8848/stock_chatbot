from datetime import datetime
from pathlib import Path

import pytest

from polymarket_shorts.longform import (
    LongformError, build_longform, report_window, sentences,
)
from polymarket_shorts.longform_render import HEIGHT, WIDTH, beats, render_longform_frame
from polymarket_shorts.render import find_font
from polymarket_shorts.tts import Word


REPORT = {
    "id": "report:US:2026-10-10T08:00:00+09:00", "kind": "report", "market": "US",
    "title": "미국 시장상황 보고서 · 20:01~08:00 UTC +9",
    "text": ("미국 주식시장은 인공지능(AI) 기대감으로 혼조세를 보이고 있다. 10년물 금리는 5.3%를 넘었다.\n\n"
             "직전 보고서의 관찰 포인트는 확인되고 있다.\n\n"
             "오늘 장에서는 실적 발표를 주목해야 한다."),
    "published_at": "2026-10-10T08:00:00+09:00", "source": "눈치 시장상황 보고서",
}
NEWS = [{"id": f"news:US:{n}", "kind": "news", "market": "US", "title": f"기사 제목 {n}", "text": f"Title {n}",
         "source": "Reuters", "published_at": f"2026-10-10T0{n}:00:00+09:00", "url": ""} for n in range(1, 4)]
DAILY = [{"date": f"2026-10-{day:02d}", "avg_sentiment": value, "count": 40, "summary": "기술주가 강세를 보였다."}
         for day, value in ((5, .1), (6, .34), (7, -.22), (8, -.16), (9, .23))]


def test_window_crosses_midnight_and_long_spans():
    assert report_window(REPORT) == (datetime.fromisoformat("2026-10-09T20:01:00+09:00"),
                                     datetime.fromisoformat("2026-10-10T08:00:00+09:00"))
    long = {**REPORT, "title": "미국 시장상황 보고서 · 10-08 15:00~10-10 08:00 UTC +9"}
    assert report_window(long)[0] == datetime.fromisoformat("2026-10-08T15:00:00+09:00")
    with pytest.raises(LongformError):
        report_window({**REPORT, "title": "미국 시장상황 보고서"})


def test_sentences_keep_decimals_together():
    assert sentences("금리는 5.3%를 넘었다. 다음 문장이다.") == ("금리는 5.3%를 넘었다.", "다음 문장이다.")


def test_script_reads_report_politely_in_order_and_only_uses_source_numbers():
    longform = build_longform(REPORT, NEWS, DAILY)
    kinds = [segment.kind for segment in longform.segments]
    assert kinds == ["intro", "sentiment", "report", "report", "report", "headlines", "outro"]
    first = longform.segments[2]
    assert first.sentences[0] == "이제 보고서 본문입니다." and first.lead == 1
    assert "혼조세를 보이고 있습니다." in first.narration and "5.3%를 넘었습니다." in first.narration
    assert longform.segments[4].sentences == ("오늘 장에서는 실적 발표를 주목해야 합니다.",)
    sentiment = longform.segments[1].narration
    # 음수는 소리로 "마이너스"라 쓰고, 숫자 바로 뒤에 받침에 따라 갈리는 조사를 붙이지 않는다.
    assert "마이너스 0.22" in sentiment and "0.34이고" in sentiment and "10월 9일 요약" in sentiment
    headlines = longform.segments[5]
    assert headlines.sentences[1:] == ("첫째, 기사 제목 1.", "둘째, 기사 제목 2.", "셋째, 기사 제목 3.")
    assert len(headlines.rows) == 3 and headlines.lead == 1
    intro = longform.segments[0].narration
    assert "10월 10일 오전 8시에 나온 미국 시장상황 보고서" in intro and "오후 8시 1분부터" in intro
    assert longform.to_dict()["evidence"]["report"]["id"] == REPORT["id"]


def test_missing_news_and_short_sentiment_drop_their_segments():
    kinds = [segment.kind for segment in build_longform(REPORT, [], DAILY[:2]).segments]
    assert kinds == ["intro", "report", "report", "report", "outro"]


def _words(narration: str) -> tuple[Word, ...]:
    """원고 낱말마다 0.3초씩 읽은 것처럼 만든다."""
    from polymarket_shorts.tts import spoken_text
    return tuple(Word(n * .3, n * .3 + .25, text) for n, text in enumerate(spoken_text(narration).split()))


def test_beats_follow_sentences_and_intro_outro_are_one_screen():
    longform = build_longform(REPORT, NEWS, DAILY)
    clock, segment_words = 0.0, []
    for segment in longform.segments:
        words = _words(segment.narration)
        segment_words.append(tuple(Word(w.start + clock, w.end + clock, w.text) for w in words))
        clock = segment_words[-1][-1].end + 1.2
    timeline = beats(longform, segment_words, clock + .6)
    assert timeline[0].start == 0 and timeline[-1].end == pytest.approx(clock + .6)
    assert all(a.end == b.start for a, b in zip(timeline, timeline[1:]))
    assert {beat.sentence for beat in timeline if beat.segment in (0, len(longform.segments) - 1)} == {None}
    report = [beat.sentence for beat in timeline if beat.segment == 2]
    assert report == list(range(len(longform.segments[2].sentences)))


@pytest.mark.parametrize("segment, active", [(0, None), (1, 2), (2, 1), (5, 2), (6, None)])
def test_frames_render_landscape(tmp_path: Path, segment, active):
    longform = build_longform(REPORT, NEWS, DAILY)
    target = tmp_path / "frame.png"
    render_longform_frame(longform, segment, active, target, font_path=find_font())
    from PIL import Image
    assert Image.open(target).size == (WIDTH, HEIGHT) == (1920, 1080)
