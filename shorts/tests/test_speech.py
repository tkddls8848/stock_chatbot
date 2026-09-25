"""확률을 말로 푸는 규칙. LLM 없이도 원고가 자연스러워야 하므로 여기가 기준이다.

Cloudflare 무료 한도가 떨어진 날에도 이 규칙만으로 원고가 나온다. 모델이 쓰는
것은 질문·해설 문장뿐이고 확률은 전부 `speech.py`가 말로 옮긴다.
"""

import re

import pytest

from polymarket_shorts import speech


@pytest.mark.parametrize(
    ("probability", "spoken"),
    [
        (.9995, "사실상 굳어진 분위기입니다"),   # 99.95% — 소수점을 읽지 않는다
        (.9, "열에 아홉은 그렇게 봅니다"),
        (.75, "넷 중 셋은 그렇게 봅니다"),
        (.5, "정확히 반반입니다"),
        (.495, "정확히 반반입니다"),            # 49.5 대 50.5는 기울지 않았다
        (.485, "거의 반반입니다"),
        (.47, "반반에 조금 못 미칩니다"),
        (.333, "셋에 하나꼴로 봅니다"),
        (.22, "다섯 번에 한 번꼴로 봅니다"),
        (.1, "열 번에 한 번꼴로 봅니다"),
        (.0005, "사실상 없다고 봅니다"),
    ],
)
def test_probabilities_are_spoken_as_everyday_odds(probability, spoken):
    assert speech.odds_phrase(probability) == spoken


def test_the_odds_table_is_ordered_and_says_no_numbers():
    """눈금이 뒤섞이면 낮은 확률에 높은 표현이 붙는다 — 사실이 뒤집힌다."""
    floors = [floor for floor, _ in speech._ODDS]

    assert floors == sorted(floors, reverse=True)
    assert floors[-1] == 0  # 어떤 확률도 빠지지 않는다
    assert len({phrase for _, phrase in speech._ODDS}) == len(speech._ODDS)
    for _, phrase in speech._ODDS:
        assert not re.search(r"\d", phrase), phrase


def test_two_choices_with_the_same_odds_are_said_once():
    """49.5 대 49.5를 따로 읽으면 같은 문장을 두 번 듣게 된다."""
    spoken = speech.speak_markets([("10월 금리 동결", .495), ("10월 금리 25bp 인상", .495)])

    assert spoken == "10월 금리 동결과 10월 금리 25bp 인상, 둘 다 정확히 반반입니다."
    assert spoken.count("정확히 반반") == 1


def test_far_apart_choices_are_linked_by_a_contrast():
    spoken = speech.speak_markets([("9월 WTI 90달러 이하", .9995), ("9월 WTI 100달러 이상", .22)])

    assert spoken == ("9월 WTI 90달러 이하 쪽은 사실상 굳어진 분위기입니다. "
                      "반면 9월 WTI 100달러 이상 쪽은 다섯 번에 한 번꼴로 봅니다.")


def test_close_choices_are_linked_without_a_contrast():
    spoken = speech.speak_markets([("10월 금리 동결", .55), ("10월 금리 25bp 인상", .4)])

    assert "반면" not in spoken and "그리고 10월 금리 25bp 인상" in spoken


@pytest.mark.parametrize(
    ("joined", "expected"),
    [("동결", "동결과"), ("인상", "인상과"), ("금리", "금리와"), ("WTI", "WTI와")],
)
def test_the_connecting_particle_follows_the_final_consonant(joined, expected):
    spoken = speech.speak_markets([(joined, .5), ("다른 선택지", .5)])

    assert spoken.startswith(expected)


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
