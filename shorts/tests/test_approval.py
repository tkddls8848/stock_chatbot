from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from polymarket_shorts import approval, youtube
from polymarket_shorts.core.storage import write_json
from polymarket_shorts.review import ReviewError, operation_lock


@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings = SimpleNamespace(output_dir=tmp_path, review_timeout_minutes=60, auto_publish=True)
    instant = [datetime(2026, 10, 6, 12, tzinfo=ZoneInfo("Asia/Seoul"))]
    monkeypatch.setattr(approval, "now", lambda: instant[0])
    calls = []

    def upload(root, settings, *, workflow_locked=False):
        assert workflow_locked
        with pytest.raises(ReviewError):
            with operation_lock(root, ".workflow.lock"):
                pass
        calls.append(root)
        return {"status": "uploaded", "url": "https://example.test/video", "video_id": "video"}

    monkeypatch.setattr(youtube, "upload", upload)
    return settings, instant, calls


def make(root, suffix="first"):
    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "review.json", {
        "status": "pending", "produced_at": suffix, "video_sha256": suffix,
        "youtube": {"title": suffix},
    })
    (root / "review.md").write_text("검수 원고 " + suffix, encoding="utf-8")
    write_json(root / "scenario.json", {"narration": suffix})
    return root


def test_timer_starts_only_after_delivery_and_survives_restart(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    instant[0] += timedelta(hours=2)
    assert approval.tick(settings) == []
    assert approval.pending(settings)[0]["delivered_at"] is None
    gate = approval.acknowledge(settings, token)
    assert datetime.fromisoformat(gate["deadline"]) == instant[0] + timedelta(hours=1)
    instant[0] += timedelta(minutes=59)
    assert approval.acknowledge(settings, token)["deadline"] == gate["deadline"]
    assert approval.tick(settings) == []
    instant[0] += timedelta(minutes=1)
    assert approval.tick(settings)[0]["status"] == "uploaded"
    assert len(calls) == 1
    assert approval.tick(settings) == []
    assert approval.pending(settings) == []


def test_pause_invalidates_timer_and_new_revision_needs_delivery(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    old = approval.register(root, settings)["token"]
    approval.acknowledge(settings, old)
    approval.pause(settings, old)
    instant[0] += timedelta(hours=3)
    assert approval.tick(settings) == []
    assert approval.register(root, settings)["state"] == "paused"
    target = make(root / "revisions" / "second", "second")
    write_json(root / "workflow.json", {"current": str(target.relative_to(root))})
    new = approval.register(root, settings)["token"]
    assert new != old
    for action in (approval.acknowledge, approval.pause, approval.approve, approval.resolve_root):
        with pytest.raises(ReviewError, match="이전 수정본"):
            action(settings, old)
    assert approval.tick(settings) == []
    approval.acknowledge(settings, new)
    instant[0] += timedelta(hours=1)
    approval.tick(settings)
    assert len(calls) == 1


def test_manual_approval_works_when_automatic_disabled(setup):
    settings, instant, calls = setup
    settings.auto_publish = False
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    approval.acknowledge(settings, token)
    instant[0] += timedelta(hours=2)
    assert approval.tick(settings) == []
    approval.pause(settings, token)
    assert approval.approve(settings, token)["status"] == "uploaded"
    assert approval.approve(settings, token)["status"] == "already_uploaded"
    assert len(calls) == 1


def test_all_registered_dates_but_not_legacy(setup):
    settings, instant, calls = setup
    roots = [make(settings.output_dir / "2026-10-04"),
             make(settings.output_dir / "2026-10-05")]
    make(settings.output_dir / "2026-10-06")
    for root in roots:
        token = approval.register(root, settings)["token"]
        assert approval.resolve_root(settings, token) == root
        approval.acknowledge(settings, token)
    assert {row["date"] for row in approval.pending(settings)} == {"2026-10-04", "2026-10-05"}
    instant[0] += timedelta(hours=1)
    assert len(approval.tick(settings)) == 2
    assert set(calls) == set(roots)


def test_changed_script_cannot_use_old_approval(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    approval.acknowledge(settings, token)
    (root / "review.md").write_text("다른 원고", encoding="utf-8")
    instant[0] += timedelta(hours=1)
    assert approval.tick(settings)[0]["status"] == "error"
    assert calls == []
    with pytest.raises(ReviewError):
        approval.approve(settings, token)


def test_invalid_paths_and_lock_fail_closed(setup):
    settings, _, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    for raw in ("../../2026-10-06", str(root), "2026-10-06-ko-../", "2026-99-99-ko-" + "a" * 32):
        with pytest.raises(ReviewError):
            approval.approve(settings, raw)
    with operation_lock(root, ".workflow.lock"):
        with pytest.raises(ReviewError):
            approval.approve(settings, token)
    assert calls == []


def test_upload_failure_preserves_approval_for_retry(setup, monkeypatch):
    settings, instant, _ = setup
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    monkeypatch.setattr(youtube, "upload", lambda *a, **kw: {"status": "no_credentials", "url": None})
    assert approval.approve(settings, token)["status"] == "no_credentials"
    gate = json.loads((root / approval.GATE_FILE).read_text())
    assert gate["state"] == "approved"
    assert approval.tick(settings)[0]["status"] == "no_credentials"
    approval.pause(settings, token)
    assert approval.tick(settings) == []


def test_force_reproduction_does_not_automatically_publish_same_day_twice(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    write_json(root / "upload.json", {"revisions": {"old": {"video_id": "old-video"}}})
    token = approval.register(root, settings)["token"]
    approval.acknowledge(settings, token)
    instant[0] += timedelta(hours=1)
    result = approval.tick(settings)[0]
    assert result["status"] == "paused"
    assert "이미 게시" in result["reason"]
    assert calls == []
    assert approval.approve(settings, token)["status"] == "uploaded"
    assert len(calls) == 1


def test_symlink_cannot_resolve_outside_output(setup, tmp_path):
    settings, _, _ = setup
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    make(outside)
    (settings.output_dir / "2026-10-06").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReviewError, match="저장소 밖"):
        approval.register(settings.output_dir / "2026-10-06", settings)


def test_resume_after_upload_record_saved_before_gate_commit(setup, monkeypatch):
    settings, instant, _ = setup
    root = make(settings.output_dir / "2026-10-06")
    token = approval.register(root, settings)["token"]
    approval.acknowledge(settings, token)
    instant[0] += timedelta(hours=1)
    record = json.loads((root / "review.json").read_text())
    identity = youtube.revision_id(root, record)
    calls = []

    def upload(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            write_json(root / "upload.json", {"revisions": {identity: {"video_id": "saved"}}})
            raise OSError("interrupted after upload")
        return {"status": "already_uploaded", "url": "https://example.test/saved", "video_id": "saved"}

    monkeypatch.setattr(youtube, "upload", upload)
    assert approval.tick(settings)[0]["status"] == "error"
    assert approval.tick(settings)[0]["status"] == "already_uploaded"
    assert len(calls) == 2
    assert approval.pending(settings) == []


def test_new_revision_recovers_after_process_dies_before_gate_registration(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    old = approval.register(root, settings)["token"]
    approval.acknowledge(settings, old)
    approval.pause(settings, old)
    target = make(root / "revisions" / "second", "second")
    write_json(root / "workflow.json", {"current": str(target.relative_to(root))})
    instant[0] += timedelta(hours=3)
    item = approval.pending(settings)[0]
    assert item["token"] != old
    assert item["state"] == "pending" and item["delivered_at"] is None
    assert item["deadline"] is None and item["script"] == "검수 원고 second"
    with pytest.raises(ReviewError, match="이전 수정본"):
        approval.approve(settings, old)
    assert approval.tick(settings) == [] and not calls


def test_linked_longform_uploads_with_the_short_and_its_failure_keeps_the_short(setup, monkeypatch):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    target = settings.output_dir / "longform" / "2026-10-06" / "US-1500"
    target.mkdir(parents=True)
    write_json(root / "longform.json", {"dir": "2026-10-06/US-1500", "report_id": "r"})
    sent = []
    monkeypatch.setattr(youtube, "upload_longform", lambda path, settings: sent.append(path) or {
        "status": "uploaded", "video_id": "long", "url": "https://www.youtube.com/watch?v=long"})
    token = approval.register(root, settings)["token"]
    approval.acknowledge(settings, token)
    instant[0] += timedelta(hours=1)
    result = approval.tick(settings)[0]
    assert result["status"] == "uploaded" and result["longform"]["url"].endswith("long")
    assert sent == [target.resolve()] and len(calls) == 1

    other = make(settings.output_dir / "2026-10-07")
    write_json(other / "longform.json", {"dir": "2026-10-07/US-1500", "report_id": "r"})
    monkeypatch.setattr(youtube, "upload_longform", lambda path, settings: (_ for _ in ()).throw(
        ReviewError("YouTube 연결 실패")))
    result = approval.approve(settings, approval.register(other, settings)["token"])
    assert result["state"] == "uploaded" and result["longform"] == {"status": "error", "error": "YouTube 연결 실패"}


def test_short_without_a_linked_longform_reports_nothing_extra(setup):
    settings, instant, calls = setup
    root = make(settings.output_dir / "2026-10-06")
    assert "longform" not in approval.approve(settings, approval.register(root, settings)["token"])
