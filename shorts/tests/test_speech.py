"""말로 읽는 규칙. LLM 없이도 원고가 자연스러워야 하므로 여기가 기준이다.

확률은 화면과 같은 퍼센트로 짧게 말한다(운영자 결정 2026-09-27).
"""

import re

import pytest

from polymarket_shorts import speech


def test_scene_links_name_the_next_theme():
    """장면 사이는 다음 이슈의 테마를 알린다(운영자 결정 2026-09-28)."""
    assert speech.transition(0, "지정학") == ""        # 첫 이슈는 도입에 바로 이어진다
    # 원고에 장면 여는 말(`lead_in`)이 없을 때의 대체 문장이다. 같은 틀이 매번 반복되지 않게 번갈아 쓴다.
    assert speech.transition(1, "주식·시장") == "이번에는 주식·시장 쪽 질문으로 넘어가 보겠습니다."
    assert speech.transition(2, "거시·통화") == "거시·통화 쪽에서도 눈여겨볼 질문이 있습니다."
    assert speech.transition(7, "") == "이어서 다른 질문을 보겠습니다."


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

    # 고정 시작 화면("오늘의 집단 예측 컨센서스 요약")과 같은 말로 연다.
    assert one.startswith("오늘의 집단 예측 컨센서스 요약입니다.") and "질문 하나를" in one
    assert "질문 3개를" in many
    # 한 글자 관형사(이·그·저)는 TTS가 한 음절로 스쳐 지나가 들리지 않는다.
    for line in (one, many, speech.CLOSING_LINE):
        assert not re.search(r"(?:^|\s)[이그저]\s", line), line


def test_the_closing_is_the_fixed_line_with_the_site():
    assert speech.CLOSING_LINE.count("확인하세요") == 1
    assert "투자 조언" in speech.CLOSING_LINE and "눈치 닷 라이브" in speech.CLOSING_LINE
    assert "nunchi.live" in speech.CLOSING_SCREEN


def test_a_yes_no_question_says_what_participants_expect():
    """"…선택한 사람은 전체의 N%" 대신 주제에 맞는 동사로 전망을 말하고(2026-10-02), "…것을 기대하고 있습니다"로
    끝낸다(운영자 결정 2026-10-08 — "…것으로 봅니다"·"…쪽으로 봅니다"를 쓰지 않는다)."""
    spoken = speech.speak_markets("binary", "호르무즈 해협 통행",
                                  [("9월 30일까지 정상화", "0.4%", "99.6%", "9월 30일까지 호르무즈 해협 통행이 정상화될")])
    assert spoken == ("참여자의 0.4%는 9월 30일까지 호르무즈 해협 통행이 정상화될 것을 기대하고 있고, "
                      "99.6%는 그 반대를 기대하고 있습니다.")


def test_several_choices_each_say_their_own_outlook():
    spoken = speech.speak_markets("independent_multi", "이스라엘·이란 휴전", [
        ("10월 31일까지", "79.5%", "20.5%", "휴전이 10월 31일까지 이어질"),
        ("11월 30일까지", "70.5%", "29.5%", "휴전이 11월 30일까지 이어질"),
    ])
    # 같은 주어("휴전이")는 첫 전망에서만 읽는다.
    assert spoken == ("참여자의 79.5%는 휴전이 10월 31일까지 이어질 것을, "
                      "70.5%는 11월 30일까지 이어질 것을 기대하고 있습니다.")


def test_one_of_several_names_the_topic_first():
    spoken = speech.speak_markets("exclusive_multi", "2026년 연준 금리 인상 횟수", [
        ("2회 인상", "60.5%", "39.5%", "연준이 2026년에 금리를 2회 인상할"),
        ("3회 인상", "18.6%", "81.4%", "연준이 2026년에 금리를 3회 인상할"),
    ])
    assert spoken.startswith("2026년 연준 금리 인상 횟수에 대해 참여자의 60.5%는 연준이 2026년에 금리를 2회 인상할 것을")


@pytest.mark.parametrize(("label", "expected"), [
    ("355달러 이상", "참여자의 85%는 355달러 이상을 기대하고 있습니다."),
    ("10월 인하", "참여자의 85%는 10월 인하를 기대하고 있습니다."),
    ("5.4%", "참여자의 85%는 5.4%를 기대하고 있습니다."),   # 숫자·%는 읽는 소리로 조사를 고른다
])
def test_without_an_outlook_the_label_still_says_an_outlook_not_a_choice(label, expected):
    """모델이 전망 구절을 못 써도 음성은 같은 틀이다. "선택한 사람"으로 되돌아가지 않는다."""
    spoken = speech.speak_markets("independent_multi", "", [(label, "85%", "15%")])
    assert spoken == expected and "선택" not in spoken


def test_a_yes_no_without_an_outlook_keeps_both_sides():
    spoken = speech.speak_markets("binary", "", [("연내 경기 침체", "9.5%", "90.5%")])
    assert spoken == "연내 경기 침체에 대해 참여자의 9.5%는 그렇게 될 것을, 90.5%는 그렇지 않을 것을 기대하고 있습니다."


def test_no_choices_say_nothing():
    assert speech.speak_markets("binary", "", []) == ""


@pytest.mark.parametrize(("event_type", "yes", "mood"), [
    ("binary", ["91.5%"], "참여자 대부분이 한쪽으로 쏠려 있습니다."),
    ("binary", ["68%"], "그쪽이 우세합니다."),
    ("binary", ["50.95%"], "팽팽하게 갈립니다."),
    ("binary", ["18%"], "반대쪽이 더 많습니다."),
    ("binary", ["6.5%"], "그쪽은 소수에 그칩니다."),
    ("independent_multi", ["91.5%", "89%"], "어느 기준에서도 같은 쪽으로 크게 쏠려 있습니다."),
    ("independent_multi", ["68%", "50.95%"], "기준에 따라 엇갈립니다."),
    ("independent_multi", ["18%", "6.5%"], "어느 기준에서도 반대쪽이 더 많습니다."),
    ("independent_multi", ["55%", "48%"], "기준을 바꿔도 크게 달라지지 않습니다."),
    ("independent_multi", ["78%", "70%"], "어느 기준에서도 그쪽이 우세합니다."),
    ("exclusive_multi", ["60.5%", "18.6%"], "한쪽으로 뚜렷하게 모여 있습니다."),
    ("exclusive_multi", ["35%", "30%"], "뚜렷하게 앞서는 답 없이 나뉩니다."),
])
def test_the_scene_closes_with_where_the_numbers_lean(event_type, yes, mood):
    """숫자만 읽고 넘어가면 장면이 뚝 끊겼다(2026-10-07). 숫자는 다시 말하지 않는다."""
    rows = [(f"선택지{n}", value, "") for n, value in enumerate(yes)]
    assert speech.consensus_mood(event_type, rows) == mood
    assert not re.search(r"\d", mood)
