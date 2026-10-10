from dataclasses import replace
import json

import pytest

from polymarket_shorts import approval, llm, review, workflow
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
        patch(changes=[], scene_order=[1, 3], metadata={"description": "새 설명"}, tts_rate="-10%"), settings)
    assert [scene.kind for scene in edited.scenes] == ["intro", "outro"]
    assert meta["description"] == "새 설명" and meta["title"] == metadata["title"]
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
    def audio(text, *, audio_path, words_path, **kwargs):
        assert text == [scene.narration for scene in scenario.scenes]
        assert audio_path.name == "narration.mp3"
        assert words_path.name == "narration.words.jsonl"
        audio_path.write_bytes(b"continuous-audio")
        words_path.write_text("", encoding="utf-8")
        return ()
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


ASK = "두 번째 장면 동결 확률을 70%로 바꾸고 멘트도 맞춰 줘"
OLD_LINE = "연준이 10월 회의에서 금리를 정합니다. 참여자의 64.5%는 동결될 것으로, 35.5%는 그렇지 않을 것으로 봅니다."
NEW_LINE = "연준이 10월 회의에서 금리를 정합니다. 참여자의 70%는 동결될 것으로, 30%는 그렇지 않을 것으로 봅니다."


@pytest.fixture
def issue():
    """제작 경로(build_scenario)와 같은 모양의 이슈 장면 하나."""
    settings = Settings.from_env()
    consensus = Scene(
        "consensus", "연준 10월 결정", "01 · 거시·통화", "동결 — 예 64.5%", OLD_LINE,
        options=(("동결", "64.5%", 0.645),), metric="64.5%", metric_label="동결", probability=0.645,
        bullets=("24시간 참여 규모 · 1.2M달러", "표시 선택지 · 유효 3개 중 상위 1개"),
        source_note="자료 기준 10/05 18:00 +0900", options_at=0.3,
        evidence=("이벤트 질문: Fed decision in October?", "시장 m1: Fed holds? / 예 64.5% / 아니오 35.5%"),
        event_id="e1", market_ids=("m1",),
    )
    scenario = Scenario("2026-10-06", "g1", "2026-10-05T18:00:00+09:00", (
        Scene("intro", "연준 10월 결정", "오늘의 전망 · 10.06", "질문", "도입 멘트"),
        consensus,
        Scene("outro", "확률은 예측입니다", "마무리", "고지", "투자 조언은 아닙니다"),
    ))
    metadata = {"title": "2026-10-06 시장 컨센서스",
                "description": "안내\n\n오늘 다룬 이슈: 연준 10월 결정\n정보 기준 시각: 2026-10-05 18:00",
                "tags": ["Shorts"]}
    return scenario, metadata, settings


def source_patch(**kwargs):
    return patch(**{
        "changes": [{"scene": 2, "options": [{"label": "동결", "yes": "70%"}], "narration": NEW_LINE}],
        "source_edits": [{"scene": 2, "fields": ["options", "narration"], "request": "동결 확률을 70%로 바꾸고"}],
        **kwargs,
    })


def test_requested_option_change_rewrites_screen_values_and_keeps_provenance(issue):
    scenario, metadata, settings = issue
    edited, _, _ = workflow.apply_edit(scenario, metadata, source_patch(), settings, ASK)
    scene = edited.scenes[1]
    assert scene.options == (("동결", "70%", 0.7),)
    assert (scene.metric, scene.metric_label, scene.probability) == ("70%", "동결", 0.7)
    assert scene.body == "동결 — 예 70%"
    # 화면에 찍히는 출처 표기가 원자료 그대로인 척하지 않는다.
    assert scene.source_note == "자료 기준 10/05 18:00 +0900 · 검수 수정"
    # 원자료 근거는 그대로 두고, 바뀐 값과 요청 구절을 뒤에 붙인다.
    assert scene.evidence[:2] == scenario.scenes[1].evidence
    assert "동결 64.5% → 동결 70%" in scene.evidence[2]
    assert "동결 확률을 70%로 바꾸고" in scene.evidence[3]
    assert (scene.event_id, scene.market_ids) == ("e1", ("m1",))
    assert scene.options_at == NEW_LINE.index("참여자의") / len(NEW_LINE)
    assert workflow._scenario(edited.to_dict()) == edited


@pytest.mark.parametrize("change, message", [
    # 요청 원문에 없는 구절을 근거로 댈 수 없다.
    ({"source_edits": [{"scene": 2, "fields": ["options", "narration"], "request": "사용자가 원함"}]},
     "요청 원문"),
    # 선택지는 선언 없이 바꾸지 못한다.
    ({"source_edits": []}, "source_edits"),
    # 요청은 70%인데 모델이 71%를 쓴다.
    ({"changes": [{"scene": 2, "options": [{"label": "동결", "yes": "71%"}], "narration": NEW_LINE}]},
     "71"),
    # 선택지만 바꾸고 멘트는 이전 확률을 그대로 말한다.
    ({"changes": [{"scene": 2, "options": [{"label": "동결", "yes": "70%"}]}],
      "source_edits": [{"scene": 2, "fields": ["options"], "request": "70%로"}]}, "이전 확률"),
    ({"changes": [{"scene": 1, "options": [{"label": "동결", "yes": "70%"}]}],
      "source_edits": [{"scene": 1, "fields": ["options"], "request": "70%로"}]}, "이슈 장면"),
    ({"changes": [{"scene": 2, "options": [{"label": f"선택{n}", "yes": "70%"} for n in range(3)],
                   "narration": NEW_LINE}]}, "1~2개"),
    ({"changes": [{"scene": 2, "options": [{"label": "동결", "yes": "170%"}], "narration": NEW_LINE}]},
     "0~100%"),
    ({"changes": [{"scene": 2, "options": [{"label": "동결", "yes": "70%", "probability": 0.1}],
                   "narration": NEW_LINE}]}, "label과 yes"),
])
def test_source_edits_must_be_requested_declared_and_consistent(issue, change, message):
    scenario, metadata, settings = issue
    with pytest.raises(review.ReviewError, match=message):
        workflow.apply_edit(scenario, metadata, source_patch(**change), settings, ASK)


@pytest.mark.parametrize("narration, instruction, edits, message", [
    # 원고에도 요청에도 없는 숫자는 문체 수정에 끼어들 수 없다.
    ("참여자의 80%가 동결을 봅니다.", "멘트를 쉽게", [], "80"),
    # 요청에 있는 숫자라도 원자료 수정으로 밝혀야 한다.
    ("참여자의 80%가 동결을 봅니다.", "멘트에 80%를 넣어", [], "source_edits"),
])
def test_text_edits_cannot_slip_in_unrequested_numbers(issue, narration, instruction, edits, message):
    scenario, metadata, settings = issue
    with pytest.raises(review.ReviewError, match=message):
        workflow.apply_edit(scenario, metadata,
                            patch(changes=[{"scene": 2, "narration": narration}], source_edits=edits),
                            settings, instruction)


def test_style_edit_reusing_source_numbers_needs_no_declaration(issue):
    scenario, metadata, settings = issue
    line = "10월 회의를 앞두고 참여자의 64.5%가 동결 쪽입니다."
    edited, _, _ = workflow.apply_edit(scenario, metadata,
                                       patch(changes=[{"scene": 2, "narration": line}]), settings, "멘트를 짧게")
    scene = edited.scenes[1]
    assert scene.narration == line and scene.evidence == scenario.scenes[1].evidence
    assert scene.source_note == scenario.scenes[1].source_note
    assert scene.options_at == 0.0  # 확률을 첫 문장에서 말한다


def test_unchanged_options_echoed_by_the_model_are_not_a_source_edit(issue):
    scenario, metadata, settings = issue
    edited, _, _ = workflow.apply_edit(scenario, metadata, patch(changes=[
        {"scene": 2, "options": [{"label": "동결", "yes": "64.5%"}], "takeaway": "발표문 확인"}]), settings, "확인점 추가")
    assert edited.scenes[1].options == scenario.scenes[1].options
    assert edited.scenes[1].source_note == scenario.scenes[1].source_note


def test_requested_topic_change_updates_description_issue_line(issue):
    scenario, metadata, settings = issue
    instruction = "두 번째 이슈 제목을 '연준 12월 결정'으로 바꿔"
    edited, meta, _ = workflow.apply_edit(scenario, metadata, patch(
        changes=[{"scene": 2, "title": "연준 12월 결정"}],
        source_edits=[{"scene": 2, "fields": ["title"], "request": "제목을 '연준 12월 결정'으로"}]),
        settings, instruction)
    assert edited.scenes[1].title == "연준 12월 결정"
    assert "오늘 다룬 이슈: 연준 12월 결정\n" in meta["description"]
    assert "(title)" in edited.scenes[1].evidence[-1]


def test_metadata_fact_edits_need_declaration(issue):
    scenario, metadata, settings = issue
    description = metadata["description"] + "\n참여 규모 7.7M달러"
    with pytest.raises(review.ReviewError, match="source_edits"):
        workflow.apply_edit(scenario, metadata, patch(changes=[], metadata={"description": description}),
                            settings, "설명에 참여 규모 7.7M달러를 적어")
    _, meta, _ = workflow.apply_edit(scenario, metadata, patch(
        changes=[], metadata={"description": description},
        source_edits=[{"scene": "metadata", "fields": ["description"], "request": "참여 규모 7.7M달러를"}]),
        settings, "설명에 참여 규모 7.7M달러를 적어")
    assert meta["description"] == description


def test_dropping_a_shown_option_rewrites_the_count_bullet(issue):
    scenario, metadata, settings = issue
    two = replace(scenario.scenes[1], options=(("동결", "64.5%", 0.645), ("인하", "30%", 0.3)))
    scenario = replace(scenario, scenes=(scenario.scenes[0], two, scenario.scenes[2]))
    edited, _, _ = workflow.apply_edit(scenario, metadata, patch(
        changes=[{"scene": 2, "options": [{"label": "동결", "yes": "64.5%"}],
                  "narration": "참여자의 64.5%는 동결될 것으로, 35.5%는 그렇지 않을 것으로 봅니다."}],
        source_edits=[{"scene": 2, "fields": ["options"], "request": "인하 선택지는 빼"}]),
        settings, "인하 선택지는 빼 줘")
    assert edited.scenes[1].bullets[1] == "표시 선택지 · 유효 3개 중 상위 1개"
    assert edited.scenes[1].options == (("동결", "64.5%", 0.645),)


def test_revision_records_requested_source_edit(issue, tmp_path, monkeypatch):
    scenario, metadata, settings = issue
    video = tmp_path / "video.mp4"
    video.write_bytes(b"original-video")
    review.write_review(tmp_path, scenario=scenario, metadata=metadata, video=video,
                        duration=40, timezone=settings.timezone)
    review.write_json(tmp_path / "scenario.json", scenario.to_dict())
    monkeypatch.setattr(workflow, "request_edit", lambda *a: source_patch())
    monkeypatch.setattr(workflow, "produce_revision", fake_render)
    target, _ = workflow.revise(tmp_path, ASK, settings)
    saved = json.loads((target / "scenario.json").read_text(encoding="utf-8"))["scenes"][1]
    assert saved["options"] == [["동결", "70%", 0.7]] and saved["metric"] == "70%"
    edit = json.loads((target / "edit.json").read_text(encoding="utf-8"))
    assert edit["instruction"] == ASK and edit["patch"]["source_edits"][0]["fields"] == ["options", "narration"]
    assert json.loads((target / "review.json").read_text(encoding="utf-8"))["status"] == "pending"


def test_natural_language_edits_cannot_change_the_fixed_title(draft):
    """게시 제목은 "yyyy-mm-dd 시장 컨센서스" 고정이다(2026-09-27)."""
    _, scenario, metadata, settings = draft
    with pytest.raises(workflow.ReviewError):
        workflow.apply_edit(scenario, metadata,
                            patch(changes=[], scene_order=[1, 2, 3], metadata={"title": "새 제목"}, tts_rate="+0%"),
                            settings)


def produced(root, scenario, metadata, settings):
    """제작 직후 날짜 폴더. scenario.json은 JSON이라 튜플이 리스트로 저장된다."""
    root.mkdir(parents=True, exist_ok=True)
    video = root / "video.mp4"
    video.write_bytes(b"original-video")
    review.write_review(root, scenario=scenario, metadata=metadata, video=video,
                        duration=40, timezone=settings.timezone)
    review.write_json(root / "scenario.json", scenario.to_dict())
    return root


TITLE_ASK = "이슈 제목을 연준 금리 결정으로"


def test_saved_revision_round_trips_and_takes_a_second_source_edit(issue, tmp_path, monkeypatch):
    scenario, metadata, settings = issue
    root = produced(tmp_path, scenario, metadata, settings)
    assert workflow._scenario(json.loads((root / "scenario.json").read_text(encoding="utf-8"))) == scenario
    monkeypatch.setattr(workflow, "produce_revision", fake_render)
    monkeypatch.setattr(workflow, "request_edit", lambda *a: source_patch())
    workflow.revise(root, ASK, settings)
    monkeypatch.setattr(workflow, "request_edit", lambda *a: patch(
        changes=[{"scene": 2, "title": "연준 금리 결정"}],
        source_edits=[{"scene": 2, "fields": ["title"], "request": TITLE_ASK}]))
    target, _ = workflow.revise(root, TITLE_ASK, settings)
    saved = json.loads((target / "scenario.json").read_text(encoding="utf-8"))["scenes"][1]
    assert saved["title"] == "연준 금리 결정"
    assert saved["evidence"][:2] == list(scenario.scenes[1].evidence)
    assert saved["evidence"][-1].endswith(TITLE_ASK) and len(saved["evidence"]) == 5


def test_source_edit_field_names_must_be_strings(issue):
    scenario, metadata, settings = issue
    bad = source_patch(source_edits=[{"scene": 2, "fields": [{}], "request": "동결 확률을 70%로 바꾸고"}])
    with pytest.raises(workflow.ReviewError, match="필드"):
        workflow.apply_edit(scenario, metadata, bad, settings, ASK)


@pytest.fixture
def gated(issue, tmp_path):
    """시나리오 검토에 등록되고 전달까지 끝난 제작일 폴더."""
    scenario, metadata, settings = issue
    settings = replace(settings, output_dir=tmp_path)
    root = produced(tmp_path / "2026-10-06", scenario, metadata, settings)
    gate = approval.register(root, settings)
    approval.acknowledge(settings, gate["token"])
    return root, settings, gate["token"]


def gate_of(root):
    return json.loads((root / "approval.json").read_text(encoding="utf-8"))


def test_stale_token_edit_is_rejected_before_the_model_runs(gated, monkeypatch):
    root, settings, _ = gated
    calls = []
    monkeypatch.setattr(workflow, "request_edit", lambda *a: calls.append(a) or source_patch())
    with pytest.raises(workflow.ReviewError):
        workflow.revise(root, ASK, settings, expected_token="2026-10-06-ko-" + "0" * 32)
    assert calls == [] and gate_of(root)["state"] == "pending"


def test_edit_of_registered_script_pauses_first_then_queues_the_revision(gated, monkeypatch):
    root, settings, token = gated
    seen = []
    monkeypatch.setattr(workflow, "request_edit", lambda *a: seen.append(gate_of(root)) or source_patch())
    monkeypatch.setattr(workflow, "produce_revision", fake_render)
    # 토큰 없이 들어오는 로컬 대화·브라우저 수정도 같은 게이트를 멈춘다.
    workflow.revise(root, ASK, settings)
    assert seen[0]["state"] == "paused" and seen[0]["deadline"] is None
    gate = gate_of(root)
    assert gate["token"] != token and gate["state"] == "pending" and gate["delivered_at"] is None
    with pytest.raises(workflow.ReviewError):
        approval.resolve_root(settings, token)
    assert approval.resolve_root(settings, gate["token"]) == root.resolve()


def test_unchanged_edit_keeps_the_script_paused(gated, monkeypatch):
    root, settings, token = gated
    monkeypatch.setattr(workflow, "request_edit", lambda *a: patch(changes=[]))
    target, summary = workflow.revise(root, "그대로 둬", settings, expected_token=token)
    assert target == root.resolve() and "보류" in summary
    assert gate_of(root)["state"] == "paused" and gate_of(root)["token"] == token
