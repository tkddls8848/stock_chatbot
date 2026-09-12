import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from shared.core.clock import JST
from telegram_bot.features.market_sentiment.anomaly import market_gate_report, score_entries
from telegram_bot.features.market_sentiment.chart import render_anomaly_chart
from telegram_bot.features.market_sentiment.overnight import select_headlines
from telegram_bot.features.market_sentiment.window import session_window
from shared.llm.overnight_tone import OvernightToneAnalyzer, OvernightToneError
from telegram_bot.news.sources import GlobalArticle
from telegram_bot.news.utils import filter_articles_in_window
from telegram_bot.state.overnight_tone import OvernightToneStore

_OVERNIGHT_PROMPT = (
    Path(__file__).resolve().parents[2] / "shared" / "prompts" / "overnight_tone_ko.txt"
)


def _entry(index: int, price_return: float, tone: float, **extra):
    day = date(2026, 5, 1) + timedelta(days=index)
    return {
        "market": "US",
        "price_session": day.isoformat(),
        "sentiment_for_session": (day + timedelta(days=1)).isoformat(),
        "price_return": price_return,
        "tone": tone,
        "forward": tone,
        "article_count": 12,
        "source_count": 5,
        "window_hours": 17.5,
        **extra,
    }


def test_session_windows_follow_exchange_hours_and_weekend():
    kr = session_window("KR", date(2026, 8, 20))
    us = session_window("US", date(2026, 8, 20))
    weekend = session_window("KR", date(2026, 8, 21))

    assert kr is not None and kr.start.hour == 15 and kr.start.minute == 30
    assert kr.end.hour == 9 and kr.sentiment_for_session == date(2026, 8, 21)
    assert us is not None and us.start.hour == 5 and us.end.hour == 22
    assert weekend is not None and weekend.end.date() == date(2026, 8, 24)
    assert weekend.window_hours > 60


def test_non_session_does_not_create_window():
    assert session_window("KR", date(2026, 8, 22)) is None


def test_window_filter_is_half_open_and_sorted():
    start = datetime(2026, 8, 20, 15, 30, tzinfo=JST)
    end = datetime(2026, 8, 21, 9, 0, tzinfo=JST)
    articles = [
        GlobalArticle("end", "end", "", end.isoformat()),
        GlobalArticle("inside", "inside", "", "2026-08-20T20:00:00+09:00"),
        GlobalArticle("start", "start", "", start.isoformat()),
    ]

    selected = filter_articles_in_window(articles, start, end)

    assert [article.article_id for article in selected] == ["start", "inside"]


def test_scoring_excludes_current_point_and_separates_alignment_from_strength():
    history = [_entry(i, (i % 7) - 3, ((i % 7) - 3) * 0.1) for i in range(60)]
    current = _entry(60, -2.0, 0.8)

    point = score_entries([*history, current])[-1]
    changed = score_entries([*history, _entry(60, -2.0, 1.0)])[-1]

    assert point.alignment == "HOPE"
    assert point.expected_tone == pytest.approx(-0.2)
    assert changed.expected_tone == pytest.approx(point.expected_tone)


def test_mad_zero_leaves_strength_unscored():
    rows = [_entry(i, float(i), 0.2) for i in range(61)]

    point = score_entries(rows)[-1]

    assert point.anomaly_score is None
    assert point.strength == "ORDINARY"


def test_gate_requires_reproducibility_and_source_audit():
    rows = [
        _entry(
            i,
            (i % 11) - 5,
            ((i % 11) - 5) * 0.08 + ((i % 3) - 1) * 0.01,
            rescore_tone=((i % 11) - 5) * 0.08,
            source_audit_match=True,
        )
        for i in range(130)
    ]

    report = market_gate_report(rows)

    assert report["g1"] is True
    assert report["g6"] is True
    assert report["g7"] is True
    assert report["evaluation_samples"] == 70
    assert report["g4"]["extreme_samples"] + report["g4"]["normal_samples"] == 69


def test_anomaly_chart_renders_three_panels():
    us = score_entries(
        [_entry(i, (i % 7) - 3, ((i % 7) - 3) * 0.1) for i in range(70)]
    )
    kr = score_entries(
        [
            {**_entry(i, (i % 5) - 2, ((i % 5) - 2) * 0.12), "market": "KR"}
            for i in range(70)
        ]
    )

    image = render_anomaly_chart({"US": us, "KR": kr}, 7, {"US", "KR"})

    assert image.name == "market_anomaly.png"
    assert len(image.getvalue()) > 10_000


def test_store_rolls_back_memory_when_write_fails(tmp_path, monkeypatch):
    store = OvernightToneStore(tmp_path / "tone.json")

    monkeypatch.setattr(
        "telegram_bot.state.overnight_tone.write_json_atomic",
        lambda *_: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        asyncio.run(store.put(_entry(0, 1.0, 0.2)))

    assert asyncio.run(store.entries()) == []


def test_store_scores_entries(tmp_path):
    store = OvernightToneStore(tmp_path / "tone.json")
    for index in range(61):
        asyncio.run(store.put(_entry(index, float(index % 5), float(index % 5) / 10)))

    scored = asyncio.run(store.scored({"US"}))

    assert len(scored["US"]) == 61


def _article(number: int, publisher: str, *, title: str | None = None):
    return GlobalArticle(
        article_id=str(number),
        title=title or f"headline {number}",
        content="",
        published_at="2026-08-20T20:00:00+09:00",
        url=f"https://example.com/{number}?tracking=x",
        extra={"source": publisher},
    )


def test_headline_selection_deduplicates_and_balances_publishers():
    articles = [_article(i, "A") for i in range(8)]
    articles += [_article(20, "B"), _article(21, "C"), _article(22, "D")]
    articles += [_article(99, "B", title="headline 20")]

    selected = select_headlines(articles, limit=8)
    publishers = [article.extra["source"] for article in selected]

    assert len(selected) == 5
    assert publishers[:4] == ["A", "B", "C", "D"]


class _Backend:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_overnight_analyzer_omits_price_return_and_records_version():
    backend = _Backend('{"tone": 0.4, "forward": 0.2, "summary": "낙관 우세"}')
    analyzer = OvernightToneAnalyzer(
        backend,
        _OVERNIGHT_PROMPT,
        model_id="model-v1",
    )

    result = analyzer.analyze(
        "KR",
        "2026-08-20",
        "2026-08-21",
        "start",
        "end",
        [{"title": "headline", "source": "source", "published_at": "time"}],
    )

    assert result["tone"] == 0.4
    assert result["model_id"] == "model-v1"
    assert "price_return" not in backend.calls[0]["user_prompt"]


def test_overnight_analyzer_rejects_broken_envelope():
    analyzer = OvernightToneAnalyzer(
        _Backend('{"tone": 0.4}'),
        _OVERNIGHT_PROMPT,
        model_id="model-v1",
    )

    with pytest.raises(OvernightToneError, match="forward"):
        analyzer.analyze("KR", "D", "D1", "start", "end", [{"title": "x"}])
