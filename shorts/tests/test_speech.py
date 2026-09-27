"""말로 읽는 규칙. LLM 없이도 원고가 자연스러워야 하므로 여기가 기준이다.

확률은 화면과 같은 퍼센트로 짧게 말한다(운영자 결정 2026-09-27).
"""

import re

import pytest

from polymarket_shorts import speech


def test_scene_links_never_count_the_scenes_off():
    """"첫째·다음은·마지막으로"로 이으면 목록을 읽는 소리가 난다."""
    links = [speech.transition(index) for index in range(5)]

    assert links[0] == ""                      # 첫 이슈는 도입에 바로 이어진다
    assert len(set(links)) == len(links)
    for link in links:
        for counter in ("첫째", "둘째", "셋째", "다음은", "마지막으로", "이어서"):
            assert counter not in link
    assert speech.transition(9) == ""          # 상한(5개)을 넘어도 조용히 비운다


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("9월에 WTI 원유 가격은 얼마에 도달할 것인가?", "9월에 WTI 원유 가격은 얼마에 도달할까요?"),
        ("10월에 금리 결정은 어떻게 될 것인가?", "10월에 금리 결정은 어떻게 될까요?"),
        ("다음 대통령은 누구인가?", "다음 대통령은 누구일까요?"),
        ("연준은 금리를 내릴까요?", "연준은 금리를 내릴까요?"),   # 이미 말로 묻는다
    ],
)
def test_written_questions_are_turned_into_spoken_ones(written, spoken):
    assert speech.to_spoken_question(written) == spoken


def test_the_opening_names_the_issue_and_the_size_of_the_day():
    one = speech.opening_line("10월 금리 결정", 1)
    many = speech.opening_line("10월 금리 결정", 3)

    assert one.startswith("10월 금리 결정.") and "질문 하나를" in one
    assert "이런 질문 3개를" in many
    # 한 글자 관형사(이·그·저)는 TTS가 한 음절로 스쳐 지나가 들리지 않는다.
    for line in (one, many, speech.CLOSING_LINE):
        assert not re.search(r"(?:^|\s)[이그저]\s", line), line


def test_the_closing_says_the_disclaimer_once_and_briefly():
    assert speech.CLOSING_LINE.count("확인하세요") == 1
    assert "투자 조언" in speech.CLOSING_LINE
    assert len(speech.CLOSING_LINE) < 90


def test_probabilities_are_said_as_the_same_percent_as_the_screen():
    spoken = speech.speak_markets([("10월 금리 25bp 인상", "64.5%"), ("10월 금리 변동 없음", "33.5%")])
    assert spoken == "10월 금리 25bp 인상 쪽은 64.5%, 10월 금리 변동 없음 쪽은 33.5%입니다."


def test_a_single_choice_is_one_short_sentence_without_yes_no_pairs():
    spoken = speech.speak_markets([("9월 30일까지 해협 교통 정상화", "0.4%")])
    assert spoken == "9월 30일까지 해협 교통 정상화 쪽은 0.4%입니다."
    assert "아니오" not in spoken and "꼴" not in spoken


def test_no_choices_say_nothing():
    assert speech.speak_markets([]) == ""
