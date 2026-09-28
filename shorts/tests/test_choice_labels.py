"""선택지 이름은 선택지끼리 다른 부분만 쓴다(운영자 지적 2026-09-28).

모든 선택지에 공통인 날짜·연도를 라벨마다 요구하자 "니콜라스 마두로 지도자 2026년",
"…186 달러 이하 2026년 9월 28일"처럼 군더더기가 붙었다.
"""

import pytest

from polymarket_shorts import english
from polymarket_shorts.highlights import HighlightError, _shared_numbers, _translation

EWY = ["Will South Korea ETF (EWY) hit (LOW) $186 Week of September 28 2026?",
       "Will South Korea ETF (EWY) hit (LOW) $187 Week of September 28 2026?"]
LEADER = ["Will Nicolás Maduro be the leader of Venezuela end of 2026?",
          "Will Delcy Rodríguez be the leader of Venezuela end of 2026?"]


def test_shared_dates_are_optional_but_thresholds_are_not():
    shared = _shared_numbers(EWY)
    _translation("186달러까지 하락", EWY[0], "label", shared)
    _translation("니콜라스 마두로", LEADER[0], "label", _shared_numbers(LEADER))
    with pytest.raises(HighlightError):
        _translation("주중 하락", EWY[0], "label", shared)          # 선택지를 가르는 186이 빠졌다
    with pytest.raises(HighlightError):
        _translation("186달러", EWY[0], "label", shared)            # 하락 방향이 빠졌다


def test_a_single_market_keeps_its_date():
    question = ["Fed rate cut in October 2026?"]
    with pytest.raises(HighlightError):
        _translation("금리 인하", question[0], "label", _shared_numbers(question))


def test_english_labels_follow_the_same_rule():
    english._check("Nicolás Maduro", LEADER[0], "label", _shared_numbers(LEADER))
    english._check("Falls to $186", EWY[0], "label", _shared_numbers(EWY))
    with pytest.raises(HighlightError):
        english._check("Falls to $186", EWY[0], "label")            # 공통 수치를 모르면 예전처럼 요구한다
