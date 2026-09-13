"""영상 내레이션의 어조는 합쇼체 하나로 통일한다.

웹 단락은 **해라체**다(`web/prompts/polymarket_brief_ko.txt`가 그렇게 지시한다).
읽는 화면에는 맞지만 영상은 말로 읽으므로 합쇼체여야 한다. 그대로 끼워 넣으면
"…5%로 나타났다. 확인하십시오"처럼 한 문장 안에서 어조가 뒤섞인다.

매체마다 요구가 다르므로 웹 프롬프트를 고치지 않고 여기서 옮긴다.
"""

import pytest

from polymarket_shorts.scenario import to_polite, to_polite_text


@pytest.mark.parametrize(
    ("plain", "polite"),
    [
        ("묶기 어렵다.", "묶기 어렵습니다."),          # 형용사 받침
        ("집중되어 있다.", "집중되어 있습니다."),        # 있다
        ("18.5%로 나타났다.", "18.5%로 나타났습니다."),  # 과거
        ("2.35%에 그쳤다.", "2.35%에 그쳤습니다."),
        ("상승이 예상된다.", "상승이 예상됩니다."),      # ㄴ다 -> ㅂ니다
        ("그렇게 보인다.", "그렇게 보입니다."),
        ("전망이 부정적이다.", "전망이 부정적입니다."),   # -이다 -> -입니다
        ("그런 것이다.", "그런 것입니다."),
    ],
)
def test_plain_endings_become_polite(plain, polite):
    assert to_polite(plain) == polite


def test_already_polite_sentences_are_left_alone():
    """한 번 더 돌리면 '입니다'가 '입닙니다'가 된다."""
    assert to_polite("이미 합쇼체입니다.") == "이미 합쇼체입니다."
    assert to_polite_text("있습니다. 둡니다.") == "있습니다. 둡니다."


def test_non_declarative_endings_are_left_alone():
    """명령형·명사 종결을 억지로 바꾸지 않는다."""
    assert to_polite("확인하십시오") == "확인하십시오"
    assert to_polite("표본 8개") == "표본 8개"


def test_multiple_sentences_keep_their_spacing():
    got = to_polite_text("집중되어 있다. 경합이 지속되고 있다.")
    assert got == "집중되어 있습니다. 경합이 지속되고 있습니다."


def test_narration_never_mixes_plain_and_polite():
    """회귀 방지: 근거 문장이 해라체로 새어 들어오면 여기서 걸린다."""
    text = to_polite_text("가능성은 18.5%로 나타났다. 위험은 제한적이다.")
    assert "나타났다." not in text
    assert "제한적이다." not in text
    assert text.count("습니다") + text.count("입니다") == 2


def test_evidence_and_checkpoint_are_separated_by_a_sentence_end():
    """마침표가 없으면 TTS가 한 문장으로 읽어 '그쳤습니다 공급망과'로 들린다."""
    from polymarket_shorts.scenario import _end_sentence

    assert _end_sentence("35%에 그쳤습니다") == "35%에 그쳤습니다."
    assert _end_sentence("이미 닫혔습니다.") == "이미 닫혔습니다."
    assert _end_sentence("잘린 문장…") == "잘린 문장…"
