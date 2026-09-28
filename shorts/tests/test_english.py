from datetime import date

import pytest

from polymarket_shorts import english
from polymarket_shorts.highlights import HighlightError


def _issue():
    return {
        "id": "81557", "title": "Prime Minister of Israel after the next election?", "sector": "geopolitics",
        "event_type": "exclusive_multi", "volume24hr": 210211.2, "end_date": "2026-10-27T23:59:00Z",
        "description": "Legislative elections are scheduled for October 27, 2026.", "valid_market_count": 16,
        "markets": [
            {"id": "1", "question": "Will Gadi Eizenkot be the next Prime Minister of Israel?",
             "yes": "51.4%", "no": "48.6%", "yes_probability": .514},
            {"id": "2", "question": "Will Naftali Bennett be the next Prime Minister of Israel?",
             "yes": "20.1%", "no": "79.9%", "yes_probability": .201},
        ],
        "news": [],
    }


def _script(**changes):
    row = {"id": "81557", "headline": "Israel's next prime minister", "question": "Who will lead Israel next?",
           "market_labels": [{"id": "1", "label": "Gadi Eizenkot"}, {"id": "2", "label": "Naftali Bennett"}],
           "context": "A new government can shift regional risk premiums in oil.",
           "watch_point": "Watch the October 27 election results."}
    return {**row, **changes}


def test_valid_script_passes():
    assert english.validate_scripts({"scripts": [_script()]}, [_issue()])[0]["headline"] == "Israel's next prime minister"


@pytest.mark.parametrize("field, text", [
    ("context", "Betting on this race is heavy among traders today."),
    ("watch_point", "Watch the result due on November 3."),
])
def test_forbidden_words_and_invented_numbers_are_refused(field, text):
    with pytest.raises(HighlightError):
        english.validate_scripts({"scripts": [_script(**{field: text})]}, [_issue()])


def test_scenario_is_english_with_the_fixed_opening_and_closing():
    summary = {"generated_at": "2026-09-28T16:00:00+09:00", "generation_id": "g1"}
    scenario = english.build_scenario(summary, [_issue()], [_script()], production_date=date(2026, 9, 28),
                                      visual_queries={"81557": "geopolitics; topic: a globe"})
    intro, issue, outro = scenario.scenes
    assert scenario.language == "en"
    assert intro.narration.startswith("Here are the market consensus issues selected for September 28, 2026.")
    assert "one question" in intro.narration
    assert "the consensus puts Gadi Eizenkot at 51.4%, Naftali Bennett at 20.1%." in issue.narration
    assert issue.visual_query == "geopolitics; topic: a globe"
    assert outro.narration == english.CLOSING_LINE
    assert english.metadata_for(scenario)["title"] == "2026-09-28 Market Consensus"


def test_binary_question_reads_yes_and_no():
    line = english.speak_markets("binary", "Fed cut", [("Fed cut in October", "64.5%", "35.5%")])
    assert line == "On Fed cut in October, 64.5% of participants say yes and 35.5% say no."


def test_description_states_the_info_time_in_minutes():
    from polymarket_shorts.pipeline import info_time
    assert info_time("2026-09-28T16:00:00.748469+09:00") == "2026-09-28 16:00"
    assert info_time("2026-09-28T07:00:00+00:00") == "2026-09-28 16:00"
