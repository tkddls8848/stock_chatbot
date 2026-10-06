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


def test_slightly_long_context_is_still_accepted():
    context = "A change in Israel's leadership could shift how investors price regional risk across oil futures, defense stocks and the Israeli shekel."
    assert 120 < len(context) <= 170
    assert english.validate_scripts({"scripts": [_script(context=context)]}, [_issue()])


def test_threshold_percent_in_a_label_is_allowed():
    issue = {**_issue(), "markets": [{"id": "1", "question": "Will the 10-year Treasury yield hit 5.3% before 2027?",
                                      "yes": "12%", "no": "88%", "yes_probability": .12}]}
    row = _script(market_labels=[{"id": "1", "label": "10-year yield hits 5.3%"}])
    assert english.validate_scripts({"scripts": [row]}, [issue])
    with pytest.raises(HighlightError):
        english.validate_scripts({"scripts": [_script(market_labels=[{"id": "1", "label": "yield at 12% chance"}])]}, [issue])


def test_descriptions_lead_with_the_site_link():
    from polymarket_shorts.pipeline import metadata_for as korean_metadata
    summary = {"generated_at": "2026-09-28T16:00:00+09:00", "generation_id": "g1"}
    scenario = english.build_scenario(summary, [_issue()], [_script()], production_date=date(2026, 9, 28),
                                      visual_queries={})
    for metadata in (english.metadata_for(scenario), korean_metadata(scenario)):
        assert metadata["description"].splitlines()[0].endswith("https://nunchi.live")
        assert metadata["description"].splitlines()[1] == "Business inquiries: tkddls8848@gmail.com"


@pytest.mark.parametrize("render_fails", [False, True])
def test_english_force_reproduction_preserves_old_pointer_until_success(tmp_path, monkeypatch, render_fails):
    from dataclasses import replace
    from polymarket_shorts.config import Settings
    from polymarket_shorts.core.storage import write_json
    from polymarket_shorts.workflow import current_target

    day = date(2026, 9, 28)
    settings = replace(Settings.from_env(), output_dir=tmp_path, visuals_enabled=False)
    root = english.english_root(settings, day.isoformat())
    previous = root / "revisions" / "last-reviewed"
    previous.mkdir(parents=True)
    (previous / "review.md").write_text("previous English script", encoding="utf-8")
    write_json(root / "review.json", {"status": "pending"})
    write_json(root / "workflow.json", {"current": "revisions/last-reviewed"})
    korean = tmp_path / day.isoformat()
    write_json(korean / "scenario.json", {"scenes": [{"kind": "consensus", "event_id": "81557"}]})
    write_json(korean / "source.json", {
        "summary": {"generated_at": "2026-09-28T16:00:00+09:00", "generation_id": "fresh-generation"},
        "issues": [_issue()],
    })
    monkeypatch.setattr(english, "write_scripts", lambda *args: [_script()])
    monkeypatch.setattr(english, "synthesize", lambda *args, **kwargs: ())
    monkeypatch.setattr(english, "probe_duration", lambda *args, **kwargs: 30)
    monkeypatch.setattr(english, "find_font", lambda *args: None)

    def render(*args, **kwargs):
        assert current_target(root) == previous
        if render_fails:
            raise RuntimeError("mock English renderer failed")
        kwargs["output_path"].write_bytes(b"new English video")
        return 30

    monkeypatch.setattr(english, "render_video", render)
    if render_fails:
        with pytest.raises(RuntimeError, match="mock English renderer failed"):
            english.produce_english(settings, day, force=True)
        assert current_target(root) == previous
    else:
        result = english.produce_english(settings, day, force=True)
        assert result.status == "pending_review"
        assert current_target(root) == root
        assert "Israel's next prime minister" in (root / "review.md").read_text(encoding="utf-8")
    assert (previous / "review.md").read_text(encoding="utf-8") == "previous English script"
