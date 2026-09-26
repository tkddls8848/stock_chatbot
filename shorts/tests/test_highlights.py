import json

import pytest

from polymarket_shorts import highlights
from polymarket_shorts.config import Settings
from polymarket_shorts.highlights import HighlightError, validate_scripts, validate_selection


def selection(identity="e1", **changes):
    return {"id": identity, "source_title": "Fed Decision in October?", "relevance": 3, "timeliness": 2, "topic": "Fed Decision",
            "reason": "금리 결정은 자금조달 비용에 영향을 주므로 발표 조건을 확인할 필요가 있습니다.", **changes}


def test_selection_can_leave_sectors_empty(issue_source):
    candidate = issue_source[1]
    assert validate_selection({"selected": []}, [candidate], 5) == []
    assert validate_selection({"selected": [selection()]}, [candidate], 5)[0]["id"] == "e1"


@pytest.mark.parametrize("changes", [{"id": "invented"}, {"relevance": 1}, {"timeliness": True}, {"reason": "짧음"}])
def test_invalid_or_weak_selection_stops(issue_source, changes):
    with pytest.raises(HighlightError):
        validate_selection({"selected": [selection(**changes)]}, [issue_source[1]], 5)


def test_duplicate_sector_and_cross_sector_topic_are_rejected(issue_source):
    candidate = issue_source[1]
    for extra in ({"id": "e2", "topic_key": "other"}, {"id": "e2", "sector": "general"}):
        rejected = []
        result = validate_selection({"selected": [selection(), selection("e2", topic="October")]},
                                    [candidate, {**candidate, **extra}], 5, rejected=rejected)
        assert len(result) == 1 and rejected[0]["id"] == "e2"


def test_script_translation_accepts_calendar_month_and_exact_market_numbers(issue_source):
    issue, script = issue_source[3:]
    assert validate_scripts({"scripts": [script]}, [issue]) == [script]


@pytest.mark.parametrize("change", [
    {"context": "금리 인상 확률은 80%입니다."},
    {"watch_point": "9월의 공식 결정을 확인하세요."},
    {"market_labels": [{"id": "m2", "label": "금리 동결"}, {"id": "m1", "label": "금리 인상"}]},
    {"market_labels": [{"id": "m1", "label": "25bp 금리 동결"}, {"id": "m2", "label": "금리 인상"}]},
])
def test_unbacked_numbers_and_mismatched_market_references_stop(issue_source, change):
    issue, script = issue_source[3:]
    with pytest.raises(HighlightError):
        validate_scripts({"scripts": [{**script, **change}]}, [issue])


def test_model_calls_only_receive_shortlist_then_selected_market_questions(issue_source, monkeypatch):
    _, candidate, _, issue, script = issue_source
    calls = []
    def chat(settings, **kwargs):
        calls.append(json.loads(kwargs["user"]))
        return {"selected": [selection()]} if len(calls) == 1 else {"scripts": [script]}
    monkeypatch.setattr(highlights, "chat_json", chat)
    highlights.select_issues([candidate], Settings.from_env())
    highlights.write_issues([issue], Settings.from_env())
    assert len(calls) == 2
    assert calls[0]["candidates"][0]["id"] == candidate["id"]
    assert calls[1][0]["markets"][0] == {"id": "m1", "question": issue["markets"][0]["question"]}


def test_question_percentages_are_preserved_but_betting_percentages_cannot_be_invented():
    highlights._translation("물가 3.4% 초과", "Inflation above 3.4%?", "label")
    with pytest.raises(HighlightError):
        highlights._translation("물가 3.4% 초과", "Inflation above 3.4?", "label")


def test_wrong_subject_attached_to_valid_event_id_is_rejected(issue_source):
    rejected = []
    assert validate_selection({"selected": [selection(topic="Crude Oil")]}, [issue_source[1]], 5, rejected=rejected) == []
    assert rejected[0]["id"] == "e1"


@pytest.mark.parametrize("text", ["9월 원유 하락", "9월 원유 90달러 도달", "9월 원유 90달러 이상"])
def test_market_label_cannot_omit_threshold_or_reverse_direction(text):
    with pytest.raises(HighlightError):
        highlights._translation(text, "Will WTI hit (LOW) $90 in September?", "label")


def test_invented_news_references_are_dropped_not_fatal(issue_source):
    """뉴스 번호는 검수 기록의 보조 근거라 지어낸 번호만 버리고 원고는 살린다."""
    issue, script = issue_source[3:]
    real = issue["news"][0]["id"] if issue["news"] else None
    ids = ["news:invented"] + ([real, real] if real else [])
    (clean,) = validate_scripts({"scripts": [{**script, "news_ids": ids}]}, [issue])
    assert clean["news_ids"] == ([real] if real else [])


def test_a_duplicated_label_id_is_collapsed(issue_source):
    issue, script = issue_source[3:]
    labels = script["market_labels"]
    (clean,) = validate_scripts({"scripts": [{**script, "market_labels": [labels[0], *labels]}]}, [issue])
    assert [label["id"] for label in clean["market_labels"]] == [m["id"] for m in issue["markets"]]
