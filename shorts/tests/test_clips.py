import base64
from dataclasses import replace
import hashlib
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest
import requests

from polymarket_shorts import clips, pipeline, review, workflow
from polymarket_shorts.config import Settings
from polymarket_shorts.scenario import Scenario, Scene


@pytest.fixture
def settings():
    return replace(Settings.from_env(), generated_clips=True, video_api_key="secret-fal-key",
                   visuals_enabled=True, max_groups=5)


@pytest.fixture
def source(tmp_path):
    scene = Scene("consensus", "이슈", "", "설명", "멘트", visual_query="topic: harbor at dusk")
    png = tmp_path / (hashlib.sha1(scene.visual_query.encode()).hexdigest()[:12] + ".png")
    png.write_bytes(b"source-png")
    return scene, png


class Response:
    is_redirect = False

    def __init__(self, payload=None, content=b"video"):
        self.payload, self.content = payload, content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload

    def iter_content(self, **kwargs):
        yield self.content


@pytest.fixture
def api(monkeypatch):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.request.side_effect = [
        Response({"status_url": "https://queue.fal.run/jobs/1/status",
                  "response_url": "https://queue.fal.run/jobs/1/result"}),
        Response({"status": "IN_QUEUE"}), Response({"status": "IN_PROGRESS"}),
        Response({"status": "COMPLETED"}),
        Response({"video": {"url": "https://media.fal.media/1.mp4"}}),
    ]
    session.get.return_value = Response()
    monkeypatch.setattr(clips.requests, "Session", lambda: session)
    monkeypatch.setattr(clips, "POLL_SECONDS", 0)
    monkeypatch.setattr(clips, "validate_clip", Mock())
    return session


def test_success_queue_contract_and_validated_cache(source, settings, api):
    scene, png = source
    chosen = clips.clips_for((scene,), (png,), settings)
    assert chosen == (png.with_suffix(".mp4"),)
    assert chosen[0].read_bytes() == b"video"
    assert api.request.call_count == 5
    submit = api.request.call_args_list[0]
    assert submit.args == ("POST", clips.QUEUE_URL + "/" + settings.video_model)
    assert submit.kwargs["headers"] == {"Authorization": "Key secret-fal-key"}
    body = submit.kwargs["json"]
    assert body["image_url"] == "data:image/png;base64," + base64.b64encode(b"source-png").decode()
    assert (body["duration"], body["resolution"], body["aspect_ratio"], body["generate_audio"]) == (
        "8", "720p", "9:16", False)
    assert "harbor at dusk" in body["prompt"] and "no new objects" in body["prompt"]
    assert "headers" not in api.get.call_args.kwargs  # 다운로드 CDN에 API 키를 보내지 않는다.
    assert clips.clips_for((scene,), (png,), settings) == chosen
    assert api.request.call_count == 5
    assert clips.validate_clip.call_count == 2


@pytest.mark.parametrize("change", [{"generated_clips": False}, {"video_api_key": ""},
                                  {"visuals_enabled": False}])
def test_disabled_or_missing_key_never_opens_network(source, settings, monkeypatch, change):
    scene, png = source
    forbidden = Mock(side_effect=AssertionError("network must not open"))
    monkeypatch.setattr(clips.requests, "Session", forbidden)
    png.with_suffix(".mp4").touch()  # 캐시가 있어도 설정을 우회하지 않는다.
    assert clips.clips_for((scene,), (png,), replace(settings, **change)) == (png,)
    forbidden.assert_not_called()


def test_validation_failure_falls_back_without_publishing_cache(source, settings, api, caplog):
    scene, png = source
    clips.validate_clip.side_effect = ValueError("bad clip")
    assert clips.clips_for((scene,), (png,), settings) == (png,)
    assert not list(png.parent.glob("*.mp4"))
    assert "정지 배경" in caplog.text


def test_http_failure_does_not_expose_credentials(source, settings, api, caplog):
    scene, png = source
    api.request.side_effect = requests.HTTPError("secret-fal-key")
    assert clips.clips_for((scene,), (png,), settings) == (png,)
    assert "HTTPError" in caplog.text and "secret-fal-key" not in caplog.text


def test_corrupt_cache_is_not_rebought(source, settings, api):
    scene, png = source
    png.with_suffix(".mp4").write_bytes(b"broken")
    clips.validate_clip.side_effect = ValueError("bad cache")
    assert clips.clips_for((scene,), (png,), settings) == (png,)
    api.request.assert_not_called()


def test_deadline_bounds_whole_batch_even_with_running_worker(source, settings, monkeypatch, caplog):
    scene, png = source
    release, finished = threading.Event(), threading.Event()
    def slow(*args):
        try:
            release.wait(2)
            return None
        finally:
            finished.set()
    monkeypatch.setattr(clips, "_generate_clip", slow)
    monkeypatch.setattr(clips, "TOTAL_SECONDS", .03)
    started = time.monotonic()
    try:
        assert clips.clips_for((scene,), (png,), settings) == (png,)
        assert time.monotonic() - started < .5
        assert "상한 초과" in caplog.text
    finally:
        release.set()
        assert finished.wait(1)


def test_expired_worker_never_downloads_or_commits(source, settings, api):
    _, png = source
    assert clips._generate_clip(png, "harbor", settings, time.monotonic() - 1) is None
    api.request.assert_not_called()
    assert not png.with_suffix(".mp4").exists()


def test_issue_submissions_run_concurrently_and_share_intro(source, settings, monkeypatch):
    scene, png = source
    other = replace(scene, visual_query="topic: mountains")
    other_png = png.with_name(hashlib.sha1(other.visual_query.encode()).hexdigest()[:12] + ".png")
    other_png.touch()
    barrier = threading.Barrier(2)
    calls = []
    def generate(path, *args):
        calls.append(path)
        barrier.wait(2)
        return path.with_suffix(".mp4")
    monkeypatch.setattr(clips, "_generate_clip", generate)
    intro, outro = replace(scene, kind="intro"), replace(scene, kind="outro")
    chosen = clips.clips_for((intro, scene, other, outro), (png, png, other_png, png), settings)
    assert set(calls) == {png, other_png}
    assert chosen == (png.with_suffix(".mp4"), png.with_suffix(".mp4"), other_png.with_suffix(".mp4"), png)
    still = replace(scene, background="still")
    monkeypatch.setattr(clips, "_generate_clip", lambda path, *args: path.with_suffix(".mp4"))
    assert clips.clips_for((intro, still), (png, png), settings) == (png.with_suffix(".mp4"), png)


def test_builtin_background_is_never_submitted(source, settings, monkeypatch):
    scene, png = source
    forbidden = Mock(side_effect=AssertionError())
    monkeypatch.setattr(clips, "_generate_clip", forbidden)
    builtin = png.with_name("financial-city.png")
    assert clips.clips_for((scene,), (builtin,), settings) == (builtin,)
    forbidden.assert_not_called()


@pytest.mark.parametrize("payload", [[], {"status_url": 123}, {"status_url": "https://other.example/status"},
                                     {"status_url": "https://queue.fal.run/status"}])
def test_malformed_queue_responses_fall_back(source, settings, api, payload):
    scene, png = source
    api.request.side_effect = [Response(payload), Response({"status": "FAILED"})]
    assert clips.clips_for((scene,), (png,), settings) == (png,)
    assert not png.with_suffix(".mp4").exists()


def test_oversized_download_falls_back(source, settings, api, monkeypatch):
    scene, png = source
    monkeypatch.setattr(clips, "MAX_DOWNLOAD_BYTES", 2)
    assert clips.clips_for((scene,), (png,), settings) == (png,)
    assert not list(png.parent.glob("*.mp4"))


@pytest.mark.parametrize("seconds", [3, 16, 8.5, True])
def test_duration_bounds(settings, seconds):
    with pytest.raises(ValueError, match="SHORTS_CLIP_SECONDS"):
        replace(settings, clip_seconds=seconds)


def test_defaults_and_secret_repr(monkeypatch):
    for name in ("SHORTS_GENERATED_CLIPS", "SHORTS_VIDEO_MODEL", "SHORTS_VIDEO_API_KEY", "SHORTS_CLIP_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_env()
    assert not settings.generated_clips and settings.clip_seconds == 8 and not settings.video_api_key
    monkeypatch.setenv("SHORTS_VIDEO_API_KEY", "private-key")
    assert "private-key" not in repr(Settings.from_env())


def test_scene_still_edit_round_trips_without_changing_default_payload(source, settings):
    scene, _ = source
    scenario = Scenario("2026-09-26", "g1", "fixed", (
        replace(scene, kind="intro"), scene, replace(scene, kind="outro")))
    assert all("background" not in row for row in scenario.to_dict()["scenes"])
    metadata = {"title": "제목", "description": "설명", "tags": ["Shorts"]}
    patch = {"summary": "배경 수정", "changes": [{"scene": 2, "background": "still"}],
             "metadata": {}, "scene_order": [1, 2, 3], "tts_rate": "+0%"}
    edited, _, _ = workflow.apply_edit(scenario, metadata, patch, settings)
    assert [row.background for row in edited.scenes] == ["", "still", ""]
    assert edited.scenes[1].visual_query == scene.visual_query
    assert workflow._scenario(edited.to_dict()) == edited
    patch["changes"][0]["background"] = "arbitrary.mp4"
    with pytest.raises(review.ReviewError, match="배경 방식"):
        workflow.apply_edit(scenario, metadata, patch, settings)


def test_visuals_review_and_revision_reuse_original_cache(source, settings, monkeypatch, tmp_path):
    scene, png = source
    scenario = Scenario("2026-09-26", "g1", "fixed", (replace(scene, kind="intro"), scene))
    metadata = {"title": "제목", "description": "설명", "tags": ["Shorts"]}
    target = tmp_path / "revisions" / "revision1"
    roots = []
    def backgrounds(scenes, root, config):
        roots.append(root)
        return (png, png)
    monkeypatch.setattr(pipeline, "backgrounds_for", backgrounds)
    monkeypatch.setattr(pipeline, "synthesize", lambda *a, **k: ())
    monkeypatch.setattr(pipeline, "find_font", lambda *a: Path("font"))
    monkeypatch.setattr(clips, "_generate_clip", lambda path, *a: path.with_suffix(".mp4"))
    def render(*a, **kwargs):
        assert kwargs["background_paths"] == (png.with_suffix(".mp4"),) * 2
        kwargs["output_path"].write_bytes(b"video")
        return 5
    monkeypatch.setattr(pipeline, "render_video", render)
    pipeline.produce_revision(scenario, metadata, target, settings)
    assert roots == [tmp_path / "backgrounds"]
    text = (target / "review.md").read_text(encoding="utf-8")
    assert "Seedance 이미지→영상 합성(이슈 1개)" in text
    assert "게시 시 YouTube '변경·합성 콘텐츠' 표시" in text
    payload = workflow._read(target / "scenario.json")
    assert all(row["kind"] == "clip" and row["source"] == "Seedance image-to-video" for row in payload["visuals"])
    assert "Seedance" not in review._script(scenario, metadata, png, 5)
