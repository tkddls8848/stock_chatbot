import json
from pathlib import Path

import pytest

from telegram_bot.llm.market_digest import MarketDigestAnalyzer, MarketDigestError

PROMPT = Path(__file__).resolve().parents[2] / "telegram_bot" / "prompts" / "market_digest_ko.txt"


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


def test_parses_digest_and_counts():
    analyzer = _analyzer(
        '{"positive": 2, "negative": 1, "neutral": 1, '
        '"sentiment": 0.25, "summary": "반도체 강세."}'
    )

    result = analyzer.analyze("KR", "2026-08-05", ["a", "b", "c", "d"])

    assert result["sentiment"] == 0.25
    assert result["summary"] == "반도체 강세."
    assert (result["positive"], result["negative"], result["neutral"]) == (2, 1, 1)


def test_large_count_drift_drops_counts_but_keeps_the_day():
    """크게 어긋나면 건수만 버린다.

    차트가 읽는 값은 sentiment와 summary뿐이고 건수는 읽는 곳이 없다. 그날을
    통째로 버리면 캐시에 아무것도 남지 않아 `/market`을 부를 때마다 같은 날을
    다시 받아 다시 호출하게 된다.
    """
    analyzer = _analyzer(
        '{"positive": 2, "negative": 1, "neutral": 1, "sentiment": 0.1, "summary": "x"}'
    )

    result = analyzer.analyze("KR", "2026-08-05", [f"h{i}" for i in range(10)])

    assert result["sentiment"] == 0.1
    assert result["summary"] == "x"
    assert (result["positive"], result["negative"], result["neutral"]) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "counts,headline_count",
    [
        ((7, 6, 5), 17),  # 실측 사례: 18 vs 17
        ((3, 2, 2), 6),   # 실측 사례: 7 vs 6
        ((7, 6, 6), 20),  # 19 vs 20 (과소 카운트)
        # 2026-08-12 실측. MARKET_DIGEST_ARTICLES_PER_DAY를 20에서 40으로 올린
        # 뒤 첫 백필에서 목록이 길어지자 오차가 5까지 벌어졌다(US 08-10 35→30,
        # US 08-08 40→35, CN 07-24 35→30). 같은 실행에서 35건짜리가 오차 1·2로
        # 통과하기도 했으므로 편향이 아니라 분산이다.
        ((12, 10, 8), 35),
        ((13, 12, 10), 40),
        ((4, 3, 3), 8),  # 작은 표본의 과대 카운트(KR 08-08 8→10)
    ],
)
def test_off_by_one_is_tolerated(counts, headline_count):
    """세기 부정확은 정상 범위다. /no_think로 추론을 껐으니 더 그렇다.

    2026-08-08 실측에서 7회 중 2회가 +1로 어긋났다. 이걸 버리면 호출 비용의
    약 29%가 그대로 낭비된다. 게다가 실패한 날은 캐시에 남지 않으므로
    `/market`을 부를 때마다 같은 날을 다시 받아 다시 호출한다.
    """
    positive, negative, neutral = counts
    analyzer = _analyzer(
        f'{{"positive": {positive}, "negative": {negative}, "neutral": {neutral}, '
        '"sentiment": 0.1, "summary": "x"}'
    )

    result = analyzer.analyze(
        "US", "2026-08-05", [f"h{i}" for i in range(headline_count)]
    )

    assert result["sentiment"] == 0.1


def test_tolerance_scales_with_headline_count():
    """긴 목록일수록 넉넉히 봐준다. 하루 상한 40건에서는 ±8이다."""
    analyzer = _analyzer("{}")

    assert analyzer._count_tolerance(1) == 1
    assert analyzer._count_tolerance(8) == 2
    assert analyzer._count_tolerance(20) == 4
    assert analyzer._count_tolerance(35) == 7
    assert analyzer._count_tolerance(40) == 8


def test_missing_counts_are_rejected_when_strict():
    analyzer = _analyzer('{"sentiment": 0.1, "summary": "x"}')

    with pytest.raises(MarketDigestError, match="counts are missing"):
        analyzer.analyze("KR", "2026-08-05", ["a"])


def test_sentiment_is_clamped():
    analyzer = _analyzer(
        '{"positive": 1, "negative": 0, "neutral": 0, "sentiment": 9.9, "summary": "x"}'
    )

    assert analyzer.analyze("US", "2026-08-05", ["a"])["sentiment"] == 1.0


def test_non_numeric_sentiment_is_rejected():
    analyzer = _analyzer(
        '{"positive": 1, "negative": 0, "neutral": 0, "sentiment": "up", "summary": "x"}'
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
        '{"positive": 1, "negative": 0, "neutral": 0, "sentiment": 0.1, "summary": "x"}'
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


def test_prompt_forbids_thinking_leak_and_demands_counts():
    text = PROMPT.read_text(encoding="utf-8")

    assert "positive" in text and "negative" in text and "neutral" in text
    assert "JSON만 출력" in text

