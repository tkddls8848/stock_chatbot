import json
from pathlib import Path

import pytest

from services.telegram_bot.llm.market_digest import MarketDigestAnalyzer, MarketDigestError

PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "market_digest_ko.txt"


class BackendStub:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _analyzer(response, **kwargs):
    return MarketDigestAnalyzer(
        backend=BackendStub(response),
        prompt_file=PROMPT,
        **kwargs,
    )


def test_day_value_is_the_mean_of_headline_scores():
    """그날 값은 헤드라인별 점수의 평균이다(2026-09-30). 모델의 종합 판단은 따로 남긴다."""
    analyzer = _analyzer(
        '{"scores": [0.6, -0.2, 0, 0.4], "sentiment": 0.5, "summary": "반도체 강세."}'
    )

    result = analyzer.analyze("KR", "2026-08-05", ["a", "b", "c", "d"])

    assert result["sentiment"] == 0.2
    assert result["overall"] == 0.5
    assert result["scored"] == 4
    assert result["summary"] == "반도체 강세."
    assert (result["positive"], result["negative"], result["neutral"]) == (2, 1, 1)


def test_broken_scores_fall_back_to_the_overall_judgement_and_keep_the_day():
    """점수 목록이 없거나 크게 모자라면 종합 판단으로 살린다.

    그날을 통째로 버리면 캐시에 아무것도 남지 않아 예약 갱신마다 같은 날을
    다시 받아 다시 호출하게 된다.
    """
    for body in ('{"sentiment": 0.1, "summary": "x"}',
                 '{"scores": [0.5, 0.5], "sentiment": 0.1, "summary": "x"}',
                 '{"scores": "high", "sentiment": 0.1, "summary": "x"}'):
        result = _analyzer(body).analyze("KR", "2026-08-05", [f"h{i}" for i in range(10)])

        assert result["sentiment"] == 0.1
        assert result["scored"] == 0
        assert (result["positive"], result["negative"], result["neutral"]) == (None, None, None)


@pytest.mark.parametrize("given,headline_count", [(16, 17), (5, 6), (32, 40), (40, 40)])
def test_a_few_missing_scores_are_tolerated(given, headline_count):
    """긴 목록에서 점수 몇 개가 빠지는 건 정상 범위다(허용 = 헤드라인 수의 20%)."""
    scores = ", ".join(["0.5"] * given)
    analyzer = _analyzer(f'{{"scores": [{scores}], "sentiment": -0.3, "summary": "x"}}')

    result = analyzer.analyze("US", "2026-08-05", [f"h{i}" for i in range(headline_count)])

    assert result["sentiment"] == 0.5
    assert result["scored"] == given


def test_extra_scores_and_non_numbers_are_ignored():
    analyzer = _analyzer('{"scores": [1, "x", 3.0, true, -1, 0.5], "sentiment": 0, "summary": "x"}')

    result = analyzer.analyze("US", "2026-08-05", ["a", "b", "c"])

    # 앞 3개만 보고, 숫자가 아닌 값은 버리고, 범위를 넘는 값은 ±1로 자른다.
    assert result["scored"] == 2
    assert result["sentiment"] == 1.0


def test_tolerance_scales_with_headline_count():
    """긴 목록일수록 넉넉히 봐준다. 하루 상한 40건에서는 ±8이다."""
    analyzer = _analyzer("{}")

    assert analyzer._count_tolerance(1) == 1
    assert analyzer._count_tolerance(8) == 2
    assert analyzer._count_tolerance(20) == 4
    assert analyzer._count_tolerance(35) == 7
    assert analyzer._count_tolerance(40) == 8


def test_sentiment_is_clamped():
    analyzer = _analyzer('{"scores": [9.9], "sentiment": 9.9, "summary": "x"}')

    result = analyzer.analyze("US", "2026-08-05", ["a"])
    assert result["sentiment"] == 1.0 and result["overall"] == 1.0


def test_non_numeric_sentiment_is_rejected():
    analyzer = _analyzer(
        '{"scores": [0.1], "sentiment": "up", "summary": "x"}'
    )

    with pytest.raises(MarketDigestError, match="sentiment must be a number"):
        analyzer.analyze("US", "2026-08-05", ["a"])


def test_truncated_json_reports_length_not_content():
    """기사 원문·응답 본문을 예외 문자열에 넣지 않는다."""
    analyzer = _analyzer('{"positive": 1, "negative": 0, "summary": "잘린 문자열')

    with pytest.raises(MarketDigestError) as excinfo:
        analyzer.analyze("US", "2026-08-05", ["a"])

    assert "raw_chars=" in str(excinfo.value)
    assert "잘린 문자열" not in str(excinfo.value)


def test_empty_response_is_rejected():
    with pytest.raises(MarketDigestError, match="empty digest response"):
        _analyzer("   ").analyze("US", "2026-08-05", ["a"])


def test_empty_headlines_do_not_call_backend():
    backend = BackendStub("{}")
    analyzer = MarketDigestAnalyzer(backend, PROMPT)

    with pytest.raises(MarketDigestError, match="no headlines"):
        analyzer.analyze("US", "2026-08-05", [])
    assert backend.calls == []


def test_payload_carries_market_date_and_headlines():
    backend = BackendStub(
        '{"scores": [0.1], "sentiment": 0.1, "summary": "x"}'
    )
    analyzer = MarketDigestAnalyzer(backend, PROMPT, num_predict=256)

    analyzer.analyze("KR", "2026-08-05", ["헤드라인"])

    payload = json.loads(backend.calls[0]["user_prompt"])
    assert payload == {
        "market": "KR",
        "date": "2026-08-05",
        "headlines": ["헤드라인"],
    }
    assert backend.calls[0]["max_tokens"] == 256


def test_prompt_demands_one_score_per_headline():
    text = PROMPT.read_text(encoding="utf-8")

    assert "scores" in text and "같은 순서, 같은 개수" in text
    assert "JSON만 출력" in text

