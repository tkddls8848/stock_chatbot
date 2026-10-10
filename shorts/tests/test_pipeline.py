from dataclasses import replace
from datetime import date

import pytest

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
        def market_news(self):
            return [{"kind": "news", "title": "연준 위원, 10월 금리 동결 지지 발언", "text": "Fed official backs October hold",
                     "published_at": "2026-09-22T21:00:00+09:00", "market": "US", "source": "Wire", "url": ""},
                    {"kind": "news", "title": "코스피 상승 마감", "text": "", "published_at": "2026-09-23T08:00:00+09:00"}]
        def confirm(self, generation):
            assert generation == "g1"
    calls = []
    hook = "어제 연준 위원이 10월 금리 동결을 지지했다는 보도가 나왔습니다."
    def chat(settings, **kwargs):
        calls.append(kwargs)
        return ({"selected": [{"id": "e1", "relevance": 3, "timeliness": 2, "keywords": ["연준", "금리"],
                               "source_title": candidate["title"], "topic": "Fed Decision", "reason": candidate["selection"]["reason"]}]}
                if len(calls) == 1 else {"scripts": [{**script, "news_hook": hook, "hook_news_id": "market:1"}]})
    monkeypatch.setattr(pipeline, "PolymarketWebClient", Client)
    monkeypatch.setattr(highlights, "chat_json", chat)
    scenario = pipeline.prepare_daily(Settings.from_env(), date(2026, 9, 23), tmp_path)
    audit = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    assert audit["llm_calls"] == len(calls) == 2
    assert audit["requests"] == {"details": 1, "news": 1}
    assert audit["produced_issues"] == 1
    assert json.loads((tmp_path / "source.json").read_text(encoding="utf-8"))["issues"][0]["markets"][0]["id"] == "m1"
    assert len(scenario.scenes) == 3
    # 일반어 "금리"는 주체어에서 빠지고, 연준 기사만 원고 모델에 후보로 간다.
    sent = json.loads(calls[1]["user"])[0]["market_news"]
    assert [row["title"] for row in sent] == ["연준 위원, 10월 금리 동결 지지 발언"] and sent[0]["when"] == "어제"
    assert audit["market_news"] == {"pool": 2, "matched": {"e1": 1}}
    narration = scenario.scenes[1].narration
    assert narration.index(hook) < narration.index(script["context"]) < narration.index("참여자의")
    assert any(line.startswith("시의 뉴스(어제") for line in scenario.scenes[1].evidence)
    # 화면 칩 셋째 칸은 주제어다(일반어 "금리"는 선정 때 빠졌다).
    assert scenario.scenes[1].bullets[2] == "주제어 · 연준"


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


def test_prune_old_days_keeps_two_weeks_of_folders(tmp_path):
    from polymarket_shorts.pipeline import RETENTION_DAYS, prune_old_days

    output = tmp_path / "output"
    settings = replace(Settings.from_env(), output_dir=output, repeat_days=7)
    old, kept = output / "2026-09-15", output / "2026-09-16"
    for folder in (old, kept, output / "state"):
        (folder / "media").mkdir(parents=True)
        (folder / "media" / "video.mp4").write_bytes(b"x")

    removed = prune_old_days(settings, date(2026, 9, 29))

    assert RETENTION_DAYS == 14
    assert removed == [old]
    assert not old.exists()
    assert kept.is_dir() and (output / "state").is_dir()


def test_prune_old_days_also_clears_old_longform_days(tmp_path):
    from polymarket_shorts.pipeline import prune_old_days

    output = tmp_path / "output"
    settings = replace(Settings.from_env(), output_dir=output, repeat_days=7)
    old, kept = output / "longform" / "2026-09-15", output / "longform" / "2026-09-16"
    for folder in (old / "US-0800", kept / "US-0800"):
        folder.mkdir(parents=True)

    assert prune_old_days(settings, date(2026, 9, 29)) == [old]
    assert kept.is_dir() and (output / "longform").is_dir()


def test_prune_old_days_never_drops_folders_repeat_avoidance_reads(tmp_path):
    from polymarket_shorts.pipeline import prune_old_days

    output = tmp_path / "output"
    settings = replace(Settings.from_env(), output_dir=output, repeat_days=20)
    (output / "2026-09-10").mkdir(parents=True)
    (output / "2026-09-08").mkdir(parents=True)

    removed = prune_old_days(settings, date(2026, 9, 29))

    assert removed == [output / "2026-09-08"]
    assert (output / "2026-09-10").is_dir()


@pytest.mark.parametrize("render_fails", [False, True])
def test_force_reproduction_selects_new_root_only_after_success(tmp_path, monkeypatch, render_fails):
    from polymarket_shorts import pipeline
    from polymarket_shorts.core.storage import write_json
    from polymarket_shorts.scenario import Scenario, Scene
    from polymarket_shorts.workflow import current_target

    day = date(2026, 10, 6)
    settings = replace(Settings.from_env(), output_dir=tmp_path, state_file=tmp_path / "state.json",
                       visuals_enabled=False)
    root = tmp_path / day.isoformat()
    previous = root / "revisions" / "last-reviewed"
    previous.mkdir(parents=True)
    (previous / "review.md").write_text("previous reviewed scenario", encoding="utf-8")
    write_json(root / "workflow.json", {"current": "revisions/last-reviewed"})
    write_json(settings.state_file, {"days": {day.isoformat(): {"video_path": "old.mp4"}}})
    scenario = Scenario(day.isoformat(), "fresh-generation", "2026-10-06T09:00:00+09:00", (
        Scene(kind="intro", title="새 원고", kicker="도입", body="새 화면", narration="새로운 도입"),
        Scene(kind="outro", title="마무리", kicker="마무리", body="안내", narration="마무리 안내"),
    ))
    monkeypatch.setattr(pipeline, "prepare_daily", lambda *args: scenario)
    monkeypatch.setattr(pipeline, "synthesize", lambda *args, **kwargs: ())
    monkeypatch.setattr(pipeline, "probe_duration", lambda *args, **kwargs: 30)
    monkeypatch.setattr(pipeline, "find_font", lambda *args: None)

    def render(*args, **kwargs):
        assert current_target(root) == previous
        if render_fails:
            raise RuntimeError("mock renderer failed")
        kwargs["output_path"].write_bytes(b"new video")
        return 30

    monkeypatch.setattr(pipeline, "render_video", render)
    if render_fails:
        with pytest.raises(RuntimeError, match="mock renderer failed"):
            pipeline.produce_daily(settings, production_date=day, force=True)
        assert current_target(root) == previous
    else:
        result = pipeline.produce_daily(settings, production_date=day, force=True)
        assert result.status == "pending_review"
        assert current_target(root) == root
        assert "새로운 도입" in (root / "review.md").read_text(encoding="utf-8")
    assert (previous / "review.md").read_text(encoding="utf-8") == "previous reviewed scenario"
