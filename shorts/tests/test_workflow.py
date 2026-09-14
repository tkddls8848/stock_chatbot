from dataclasses import replace
import json

import pytest

from polymarket_shorts import llm, review, workflow
from polymarket_shorts.config import Settings
from polymarket_shorts.scenario import Scenario, Scene


@pytest.fixture
def draft(tmp_path):
    settings = Settings.from_env()
    scenario = Scenario("2026-09-13", "g1", "fixed-source", (
        Scene("intro", "첫 질문", "", "도입", "도입 멘트"),
        Scene("consensus", "분야", "", "설명", "원래 멘트", metric="42%"),
        Scene("outro", "마무리", "", "고지", "투자 조언은 아닙니다"),
    ))
    metadata = {"title": "테스트 #Shorts", "description": "근거 설명", "tags": ["Shorts"]}
    video = tmp_path / "video.mp4"
    video.write_bytes(b"original-video")
    review.write_review(tmp_path, scenario=scenario, metadata=metadata, video=video,
                        duration=40, timezone=settings.timezone)
    review.write_json(tmp_path / "scenario.json", scenario.to_dict())
    return tmp_path, scenario, metadata, settings


def patch(**kwargs):
    return {"summary": "두 번째 장면을 쉽게 수정", "changes": [{"scene": 2, "narration": "쉬운 멘트"}],
            "metadata": {}, "scene_order": [1, 2, 3], "tts_rate": "+0%", **kwargs}




def fake_render(scenario, metadata, target, settings):
    video = target / "video.mp4"
    video.write_bytes(b"edited-video")
    review.write_json(target / "scenario.json", scenario.to_dict())
    review.write_json(target / "production.json", {"voice": settings.tts_voice, "rate": settings.tts_rate})
    review.write_review(target, scenario=scenario, metadata=metadata, video=video,
                        duration=35, timezone=settings.timezone)


def test_revision_preserves_source_and_old_video_and_requires_fresh_review(draft, monkeypatch):
    root, scenario, metadata, settings = draft
    review.complete_review(root)
    monkeypatch.setattr(workflow, "request_edit", lambda *a: patch())
    monkeypatch.setattr(workflow, "produce_revision", fake_render)
    target, _ = workflow.revise(root, "두 번째 멘트를 쉽게", settings)
    assert workflow.current_target(root) == target
    assert (root / "video.mp4").read_bytes() == b"original-video"
    payload = json.loads((target / "scenario.json").read_text(encoding="utf-8"))
    assert payload["scenes"][1]["narration"] == "쉬운 멘트"
    assert payload["scenes"][1]["metric"] == "42%"
    assert payload["generation_id"] == scenario.generation_id
    assert json.loads((root / "review.json").read_text(encoding="utf-8"))["status"] == "superseded"
    assert json.loads((target / "review.json").read_text(encoding="utf-8"))["status"] == "pending"


def test_failed_render_keeps_previous_preview_but_reopens_review(draft, monkeypatch):
    root, _, _, settings = draft
    review.complete_review(root)
    monkeypatch.setattr(workflow, "request_edit", lambda *a: patch())
    def fail(*args):
        raise ValueError("render failed")
    monkeypatch.setattr(workflow, "produce_revision", fail)
    with pytest.raises(ValueError, match="render failed"):
        workflow.revise(root, "쉽게", settings)
    assert workflow.current_target(root) == root
    assert json.loads((root / "review.json").read_text(encoding="utf-8"))["status"] == "pending"
    assert (root / "video.mp4").read_bytes() == b"original-video"


@pytest.mark.parametrize("change", [
    {"changes": [{"scene": 2, "metric": "99%"}]},
    {"changes": [{"scene": True, "narration": "test"}]},
    {"scene_order": [1, 2, 2, 3]}, {"scene_order": [2, 3]},
    {"tts_rate": "+99%"}, {"metadata": {"privacy": "public"}},
    {"changes": [{"scene": 2, "narration": ""}]},
])
def test_model_cannot_change_source_or_unknown_fields(draft, change):
    _, scenario, metadata, settings = draft
    with pytest.raises(review.ReviewError):
        workflow.apply_edit(scenario, metadata, patch(**change), settings)


def test_metadata_and_scene_removal(draft):
    _, scenario, metadata, settings = draft
    edited, meta, config = workflow.apply_edit(scenario, metadata,
        patch(changes=[], scene_order=[1, 3], metadata={"title": "새 제목"}, tts_rate="-10%"), settings)
    assert [scene.kind for scene in edited.scenes] == ["intro", "outro"]
    assert meta["title"] == "새 제목"
    assert config.tts_rate == "-10%"






def test_lock_excludes_second_writer_and_releases_on_error(tmp_path):
    with pytest.raises(ValueError):
        with review.operation_lock(tmp_path):
            with pytest.raises(review.ReviewError, match="처리 중"):
                with review.operation_lock(tmp_path):
                    pytest.fail("lock must exclude another writer")
            raise ValueError("failed operation")
    with review.operation_lock(tmp_path):
        pass








def test_truncated_llm_response_is_not_retried(draft, monkeypatch):
    _, scenario, metadata, settings = draft
    settings = replace(settings, editor_account_id="account", editor_api_token="secret")
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"choices": [{"finish_reason": "length", "message": {"content": "{"}}]}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **kw: calls.append(kw) or Response())
    with pytest.raises(review.ReviewError, match="완결되지"):
        workflow.request_edit(scenario, metadata, "쉽게", settings)
    assert len(calls) == 1


def test_edit_prompt_names_the_current_scene_order(draft, monkeypatch):
    _, scenario, metadata, settings = draft
    settings = replace(settings, editor_account_id="account", editor_api_token="secret")
    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(patch())}}]}

    def post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr(llm.requests, "post", post)
    workflow.request_edit(scenario, metadata, "두 번째 장면을 쉽게", settings)

    system_prompt = captured["messages"][0]["content"]
    assert "장면 번호는 [1, 2, 3]" in system_prompt
    assert "scene_order는 반드시 [1, 2, 3]" in system_prompt






def test_revision_renderer_uses_edited_script_and_persists_preview(draft, monkeypatch):
    from polymarket_shorts import pipeline
    root, scenario, metadata, settings = draft
    settings = replace(settings, visuals_enabled=False)
    def audio(text, *, audio_path, subtitle_path, **kwargs):
        assert text == scenario.narration
        assert audio_path.name == "narration.mp3"
        assert subtitle_path.name == "captions.vtt"
        audio_path.write_bytes(b"continuous-audio")
        subtitle_path.write_text("captions", encoding="utf-8")
    def render(actual, **kwargs):
        assert actual == scenario
        assert "audio_scene_durations" not in kwargs
        kwargs["output_path"].write_bytes(b"rendered")
        return 30.0
    monkeypatch.setattr(pipeline, "synthesize", audio)
    monkeypatch.setattr(pipeline, "find_font", lambda *a: root / "font.ttf")
    monkeypatch.setattr(pipeline, "render_video", render)
    target = root / "rendered"
    result = pipeline.produce_revision(scenario, metadata, target, settings)
    assert result.status == "pending_review"
    assert metadata["title"] in review.read_script(target)
    production = json.loads((target / "production.json").read_text(encoding="utf-8"))
    assert production["rate"] == settings.tts_rate
    assert production["synthesis"] == "continuous"


def test_conversation_edits_and_completes_local_review(draft, monkeypatch, capsys):
    root, _, _, settings = draft
    monkeypatch.setattr(workflow, "request_edit", lambda *a: patch())
    monkeypatch.setattr(workflow, "produce_revision", fake_render)
    answers = iter(["두 번째 멘트를 쉽게", "보기", "완료"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    workflow.interact(root, settings)
    current = workflow.current_target(root)
    record = json.loads((current / "review.json").read_text(encoding="utf-8"))
    assert record["status"] == "reviewed"
    assert (current / record["video"]).is_file()
    assert "검수를 완료했습니다" in capsys.readouterr().out


def test_exit_preserves_pending_review_and_can_resume(draft, monkeypatch):
    root, _, _, settings = draft
    monkeypatch.setattr("builtins.input", lambda *a: "종료")
    workflow.interact(root, settings)
    assert json.loads((root / "review.json").read_text(encoding="utf-8"))["status"] == "pending"
    monkeypatch.setattr("builtins.input", lambda *a: "완료")
    workflow.interact(root, settings)
    assert json.loads((root / "review.json").read_text(encoding="utf-8"))["status"] == "reviewed"


@pytest.mark.parametrize("flag", ["--approve", "--publish"])
def test_cli_rejects_removed_publishing_commands(flag, monkeypatch):
    import sys
    from polymarket_shorts import cli
    monkeypatch.setattr(sys, "argv", ["shorts", flag, "some-directory"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
