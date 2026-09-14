from polymarket_shorts import panel, review, workflow
from polymarket_shorts.config import Settings
from polymarket_shorts.scenario import Scenario, Scene


def _draft(target):
    target.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    scenario = Scenario("2026-09-14", "g1", "fixed", (
        Scene("intro", "첫 질문", "", "도입", "도입 멘트"),
        Scene("consensus", "거시", "", "설명", "쉬운 설명", metric="42%"),
        Scene("outro", "마무리", "", "고지", "투자 조언은 아닙니다"),
    ))
    video = target / "video.mp4"
    video.write_bytes(b"0123456789")
    review.write_review(
        target, scenario=scenario,
        metadata={"title": "제목", "description": "설명", "tags": ["Shorts"]},
        video=video, duration=40, timezone=settings.timezone,
    )
    review.write_json(target / "scenario.json", scenario.to_dict())
    review.write_json(target / "production.json", {"voice": settings.tts_voice, "rate": "-10%"})
    return settings


def test_panel_state_follows_latest_revision(tmp_path):
    _draft(tmp_path)
    revision = tmp_path / "revisions" / "one"
    _draft(revision)
    workflow.current_target(tmp_path)
    review.write_json(tmp_path / "workflow.json", {"current": "revisions/one"})

    state = panel.panel_state(tmp_path)

    assert state["status"] == "pending"
    assert state["tts_rate"] == "-10%"
    assert state["scenes"][1]["metric"] == "42%"
    assert "쉬운 설명" in state["script"]


def test_video_ranges_support_browser_seeking():
    assert panel._range(None, 10) is None
    assert panel._range("bytes=2-5", 10) == (2, 5)
    assert panel._range("bytes=7-", 10) == (7, 9)
    assert panel._range("bytes=-3", 10) == (7, 9)


def test_browser_panel_uses_text_content_for_review_data():
    assert "innerHTML" not in panel.PANEL_HTML
    assert "textContent" in panel.PANEL_HTML
    assert "replaceAll('\\n',' / ')" in panel.PANEL_HTML


def test_cancelled_video_request_does_not_try_to_write_an_error(tmp_path):
    settings = _draft(tmp_path)
    handler_type = panel._handler(tmp_path, settings)
    handler = handler_type.__new__(handler_type)
    handler.path = "/video"
    handler._video = lambda: (_ for _ in ()).throw(ConnectionResetError())
    handler._error = lambda exc: (_ for _ in ()).throw(AssertionError("error response attempted"))

    handler.do_GET()
