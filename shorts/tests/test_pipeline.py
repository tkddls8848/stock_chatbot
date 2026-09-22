from dataclasses import replace
from datetime import date

from polymarket_shorts.config import Settings
from polymarket_shorts.pipeline import produce_daily


def test_daily_state_prevents_a_second_production(tmp_path):
    state_file = tmp_path / "state" / "published.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        '{"days":{"2026-09-01":{"video_path":"already.mp4","review_path":"review.md"}}}',
        encoding="utf-8",
    )
    settings = replace(
        Settings.from_env(),
        output_dir=tmp_path / "output",
        state_file=state_file,
    )

    result = produce_daily(settings, production_date=date(2026, 9, 1))

    assert result.status == "already_produced"
    assert result.video_path == "already.mp4"
    assert result.review_path == "review.md"


def test_editorial_rejects_missing_sectors_before_generating_audio(tmp_path):
    import json
    import pytest
    from polymarket_shorts.pipeline import produce_editorial

    plan = tmp_path / "editorial.json"
    plan.write_text(json.dumps({"schema_version": 1, "scenes": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="5개 분야"):
        produce_editorial(plan, Settings.from_env())


def test_daily_selection_audits_sources_and_bounds_model_calls(tmp_path, monkeypatch, issue_source):
    import json
    from polymarket_shorts import pipeline, highlights

    snapshot, candidate, detail, _, script = issue_source
    class Client:
        requests = {"details": 0, "news": 0}
        def __init__(self, url):
            pass
        def snapshot(self):
            return snapshot
        def detail(self, identity, generation):
            assert identity == "e1" and generation == "g1"
            self.requests["details"] += 1
            return detail
        def news(self, title, **kwargs):
            self.requests["news"] += 1
            return []
        def confirm(self, generation):
            assert generation == "g1"
    calls = []
    def chat(settings, **kwargs):
        calls.append(kwargs)
        return ({"selected": [{"id": "e1", "relevance": 3, "timeliness": 2,
                               "source_title": candidate["title"], "topic": "Fed Decision", "reason": candidate["selection"]["reason"]}]}
                if len(calls) == 1 else {"scripts": [script]})
    monkeypatch.setattr(pipeline, "PolymarketWebClient", Client)
    monkeypatch.setattr(highlights, "chat_json", chat)
    scenario = pipeline.prepare_daily(Settings.from_env(), date(2026, 9, 23), tmp_path)
    audit = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert audit["llm_calls"] == len(calls) == 2
    assert audit["requests"] == {"details": 1, "news": 1}
    assert audit["produced_issues"] == 1
    assert json.loads((tmp_path / "source.json").read_text(encoding="utf-8"))["issues"][0]["markets"][0]["id"] == "m1"
    assert len(scenario.scenes) == 3


def test_no_suitable_issues_does_not_synthesize_or_mark_day_complete(tmp_path, monkeypatch):
    from polymarket_shorts import pipeline
    monkeypatch.setattr(pipeline, "prepare_daily", lambda *a: None)
    def forbidden(*a, **kw):
        raise AssertionError("No issue must not generate audio")
    monkeypatch.setattr(pipeline, "synthesize", forbidden)
    settings = replace(Settings.from_env(), output_dir=tmp_path, state_file=tmp_path / "state.json")
    assert pipeline.produce_daily(settings).status == "no_suitable_issues"
    assert not settings.state_file.exists()


def test_failed_selection_records_error_and_does_not_fall_back_to_summary(tmp_path, monkeypatch, issue_source):
    import json
    import pytest
    from polymarket_shorts import pipeline
    from polymarket_shorts.highlights import HighlightError
    monkeypatch.setattr(pipeline.PolymarketWebClient, "snapshot", lambda self: issue_source[0])
    def reject(*a, **kw):
        raise HighlightError("bad selection")
    monkeypatch.setattr(pipeline, "select_issues", reject)
    with pytest.raises(HighlightError):
        pipeline.prepare_daily(Settings.from_env(), date(2026, 9, 23), tmp_path)
    audit = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert audit["status"] == "failed" and audit["error"] == "bad selection"
    assert audit["llm_calls"] == 1
    assert not (tmp_path / "scenario.json").exists()
