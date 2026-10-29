from dataclasses import replace

import pytest

from polymarket_shorts.client import SourceError
from polymarket_shorts.markets import prepare_issue, shortlist, percent


def test_shortlist_has_sector_caps_and_does_not_require_homepage_paragraphs(issue_source, event_factory):
    snapshot = issue_source[0]
    events = tuple(event_factory(str(i), title=f"Company {chr(65+i)} IPO?", tags=["stocks"]) for i in range(20))
    candidates, audit = shortlist(replace(snapshot, events=events))
    assert len(candidates) == 10
    assert audit["scanned"] == 20 and audit["eligible_by_sector"]["equities"] == 20
    assert all(c["change"] is None for c in candidates)


def test_repeated_threshold_markets_do_not_monopolize_shortlist(issue_source, event_factory):
    events = tuple(event_factory(str(i), title=f"Will oil hit {i} by December 31?", tags=["finance"]) for i in range(20))
    candidates, _ = shortlist(replace(issue_source[0], events=events))
    assert len(candidates) == 2


@pytest.mark.parametrize("change", [
    {"liquidity": 50}, {"volume24hr": float("nan")}, {"volume24hr": True},
    {"end_date": "2026-01-01T00:00:00Z"}, {"end_date": None},
    {"event_type": "unknown_multi"}, {"tags": ["sports"]},
])
def test_bad_low_activity_or_out_of_scope_events_are_excluded(issue_source, event_factory, change):
    candidates, _ = shortlist(replace(issue_source[0], events=(event_factory(**change),)))
    assert candidates == []


def test_changed_leader_does_not_become_probability_movement(issue_source):
    snapshot = replace(issue_source[0], trending={"spotlight": [{"id": "e1", "leader_changed": True, "basis_change": .4}]})
    candidate = shortlist(snapshot)[0][0]
    assert candidate["change"] is None and candidate["leader_changed"]


def test_actual_movement_affects_ranking_and_has_reference(issue_source):
    snapshot = replace(issue_source[0], trending={"basis_at": "earlier", "spotlight": [{"id": "e1", "basis_change": .08}]})
    moving = shortlist(snapshot)[0][0]
    assert moving["score"] > shortlist(issue_source[0])[0][0]["score"]
    assert moving["change"] == .08 and moving["change_basis_at"] == "earlier"


def test_independent_probabilities_are_not_normalized(issue_source):
    _, candidate, detail, _, _ = issue_source
    detail["markets"][1].update(yes_probability=.8, no_probability=.2)
    issue = prepare_issue(candidate, detail, [])
    # 유력한 선택지가 먼저 온다. 값은 정규화되지 않은 원래 확률이다.
    assert [m["yes"] for m in issue["markets"]] == ["80%", "55%"]


def test_inactive_or_invalid_prices_are_not_used(issue_source):
    _, candidate, detail, _, _ = issue_source
    detail["markets"][0]["closed"] = True
    detail["markets"][1]["yes_probability"] = float("inf")
    with pytest.raises(SourceError, match="개별 베팅"):
        prepare_issue(candidate, detail, [])


@pytest.mark.parametrize("value, expected", [(0.5, "50%"), (.175, "17.5%"), (.01, "1%"), (.0045, "0.45%")])
def test_percent_keeps_original_precision(value, expected):
    assert percent(value) == expected


@pytest.mark.parametrize(("changes", "reason"), [
    ({"end_date": "2026-09-24T03:59:00Z"}, "closes_within_two_days"),
    ({"leader_probability": .985}, "near_certain"),
])
def test_questions_already_settled_for_viewers_are_not_candidates(event_factory, changes, reason):
    """당일 마감·98% 이상 질문은 영상이 올라갈 때 이미 끝났거나 아무것도 알려 주지 않는다(독립 검토 2026-10-08)."""
    from polymarket_shorts.client import Snapshot
    summary = {"generation_id": "g1", "generated_at": "2026-09-23T09:00:00+09:00", "freshness": {"state": "normal"}}
    candidates, audit = shortlist(Snapshot(summary, (event_factory(**changes),), {}))
    assert candidates == [] and audit["excluded"] == {reason: 1}


def _rows(*values, labels=None):
    """(확률, 24시간 참여 규모) 줄로 원자료 선택지를 만든다."""
    return [{"id": f"m{i}", "question": f"Q{i}", "outcome_label": (labels or [None] * len(values))[i],
             "yes_probability": p, "no_probability": round(1 - p, 4), "yes": percent(p), "no": percent(round(1 - p, 4)),
             "volume24hr": v, "liquidity": 1} for i, (p, v) in enumerate(values)]


def test_a_yes_no_question_shows_the_answer_most_participants_chose():
    """이지선다는 많은 답 하나만 세운다(운영자 결정 2026-10-29). 호르무즈 통행 정상화 '예' 11.5%가 화면을 차지했다."""
    from polymarket_shorts.markets import shown_markets
    (row,) = shown_markets("binary", _rows((.115, 10)))
    assert row["side"] == "no"
    (row,) = shown_markets("binary", _rows((.765, 10)))
    assert row["side"] == "yes"


def test_one_of_several_shows_the_top_three_unless_the_third_says_nothing():
    from polymarket_shorts.markets import shown_markets
    # 연준 12월 결정(2026-10-29): 25bp 인상 76.5 · 동결 21.5 · 50bp 이상 인상 1.95
    fed = _rows((.0025, 24), (.0045, 16), (.215, 28), (.765, 18), (.0195, 13))
    assert [r["yes"] for r in shown_markets("exclusive_multi", fed)] == ["76.5%", "21.5%", "1.95%"]
    # 2026년 인하 횟수: 셋째가 0.15%라 두 줄
    cuts = _rows((.9715, 64), (.0095, 13), (.0015, 12))
    assert [r["yes"] for r in shown_markets("exclusive_multi", cuts)] == ["97.15%", "0.95%"]
    # 묶음 선택지(Other)는 셋째로 세우지 않는다
    other = _rows((.775, 39), (.205, 51), (.0215, 9), labels=["Hike–Pause–Hike", "Hike–Pause–Pause", "Other"])
    assert [r["yes"] for r in shown_markets("exclusive_multi", other)] == ["77.5%", "20.5%"]


def test_a_date_ladder_shows_its_slope_in_date_order_without_inverted_rungs():
    """이스라엘·이란 휴전(2026-10-29). 확률순 상위 둘은 99.6%·99.1%로 거의 확정된 칸뿐이었다.

    10/30 50.25%는 10/31 95.5%보다 낮아 순서가 뒤집힌 칸이다 — 참여가 얇은 그 칸을 버린다.
    """
    from polymarket_shorts.markets import shown_markets
    labels = ["October 31", "December 31", "November 30", "November 15", "October 27", "December 15",
              "November 3", "October 29", "October 30"]
    rows = _rows((.955, 42.8), (.33, 14.5), (.37, 3.2), (.66, 9.6), (.996, 8.6), (.55, .3), (.926, 4.9),
                 (.991, .4), (.5025, 4.3), labels=labels)
    shown = shown_markets("independent_multi", rows, 2026)
    assert [r["outcome_label"] for r in shown] == ["October 31", "December 15", "December 31"]


def test_a_price_ladder_reads_from_the_highest_price():
    from polymarket_shorts.markets import shown_markets
    labels = ["Below 4.00%", "Below 4.10%", "Below 4.50%", "Below 4.65%", "Below 4.70%"]
    rows = _rows((.026, 1), (.0475, 1), (.56, 3), (.56, 70), (.795, 2), labels=labels)
    shown = shown_markets("independent_multi", rows)
    assert [r["outcome_label"] for r in shown] == ["Below 4.70%", "Below 4.65%", "Below 4.10%"]


def test_several_true_choices_that_are_not_a_ladder_fall_back_to_the_top_three():
    from polymarket_shorts.markets import shown_markets
    rows = _rows((.2, 1), (.6, 1), (.4, 1), (.3, 1), labels=["Apple", "NVIDIA", "Microsoft", "Alphabet"])
    assert [r["outcome_label"] for r in shown_markets("independent_multi", rows)] == ["NVIDIA", "Microsoft", "Alphabet"]
