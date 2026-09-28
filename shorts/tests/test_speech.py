"""말로 읽는 규칙. LLM 없이도 원고가 자연스러워야 하므로 여기가 기준이다.

확률은 화면과 같은 퍼센트로 짧게 말한다(운영자 결정 2026-09-27).
"""

import re

import pytest

from polymarket_shorts import speech


def test_scene_links_name_the_next_theme():
    """장면 사이는 다음 이슈의 테마를 알린다(운영자 결정 2026-09-28)."""
    assert speech.transition(0, "지정학") == ""        # 첫 이슈는 도입에 바로 이어진다
    assert speech.transition(1, "주식·시장") == "다음은 주식·시장 테마의 주요 컨센서스 현황을 살펴봅니다."
    assert speech.transition(7, "") == "다음은 주요 컨센서스 현황을 살펴봅니다."


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
    one = speech.opening_line(1)
    many = speech.opening_line(3)

    assert one.startswith("오늘의 시장 컨센서스 이슈를 선정하였습니다.") and "질문 하나를" in one
    assert "질문 3개를" in many
    # 한 글자 관형사(이·그·저)는 TTS가 한 음절로 스쳐 지나가 들리지 않는다.
    for line in (one, many, speech.CLOSING_LINE):
        assert not re.search(r"(?:^|\s)[이그저]\s", line), line


def test_the_closing_is_the_fixed_line_with_the_site():
    assert speech.CLOSING_LINE.count("확인해 보세요") == 1
    assert "투자 조언" in speech.CLOSING_LINE and "눈치 닷 라이브" in speech.CLOSING_LINE
    assert "nunchi.live" in speech.CLOSING_SCREEN


def test_a_yes_no_question_says_both_sides_as_shares_of_everyone():
    spoken = speech.speak_markets("binary", "호르무즈 해협 교통",
                                  [("9월 30일까지 호르무즈 해협 교통 정상화", "0.4%", "99.6%")])
    assert spoken == ("9월 30일까지 호르무즈 해협 교통 정상화에 대해 그렇다고 보는 사람은 전체의 0.4%, "
                      "그렇지 않다고 보는 사람은 전체의 99.6%입니다.")


def test_one_of_several_names_the_topic_then_each_choice():
    spoken = speech.speak_markets("exclusive_multi", "연준 금리 결정",
                                  [("10월 금리 25bp 인상", "64.5%", "35.5%"), ("10월 금리 동결", "33.5%", "66.5%")])
    assert spoken == ("연준 금리 결정에 대해 10월 금리 25bp 인상을 선택한 사람은 전체의 64.5%, "
                      "10월 금리 동결을 선택한 사람은 전체의 33.5%입니다.")


def test_several_can_be_true_lists_each_choice_without_a_topic():
    spoken = speech.speak_markets("independent_multi", "경기 지표",
                                  [("미국 경기 침체", "9.5%", "90.5%"), ("유로존 금리 인하", "40%", "60%")])
    assert spoken == "미국 경기 침체를 선택한 사람은 전체의 9.5%, 유로존 금리 인하를 선택한 사람은 전체의 40%입니다."


def test_no_choices_say_nothing():
    assert speech.speak_markets("binary", "", []) == ""
