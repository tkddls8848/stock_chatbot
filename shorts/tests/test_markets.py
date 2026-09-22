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
    assert [m["yes"] for m in issue["markets"]] == ["55%", "80%"]


def test_inactive_or_invalid_prices_are_not_used(issue_source):
    _, candidate, detail, _, _ = issue_source
    detail["markets"][0]["closed"] = True
    detail["markets"][1]["yes_probability"] = float("inf")
    with pytest.raises(SourceError, match="개별 베팅"):
        prepare_issue(candidate, detail, [])


@pytest.mark.parametrize("value, expected", [(0.5, "50%"), (.175, "17.5%"), (.01, "1%"), (.0045, "0.45%")])
def test_percent_keeps_original_precision(value, expected):
    assert percent(value) == expected
