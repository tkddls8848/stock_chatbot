"""텔레그램 `/shorts`가 하위 프로세스로 읽는 상태 JSON과 비대화형 명령."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from polymarket_shorts.config import PROJECT_DIR, Settings
from polymarket_shorts.status import current_status


def _settings(tmp_path):
    return replace(Settings.from_env(), output_dir=tmp_path / "shorts", state_file=tmp_path / "state.json")


def _day(root: Path, day: str, *, review=True, current=None):
    folder = root / day
    folder.mkdir(parents=True)
    (folder / "selection.json").write_text(json.dumps({"status": "script_ready"}), encoding="utf-8")
    if review:
        target = folder / current if current else folder
        target.mkdir(parents=True, exist_ok=True)
        (target / "clip.mp4").write_bytes(b"0" * 10)
        (target / "review.json").write_text(json.dumps({
            "status": "pending", "video": "clip.mp4", "produced_at": "2026-09-24T21:03:00+09:00",
            "duration_seconds": 61.2, "youtube": {"title": "제목", "description": "설명", "tags": ["집단 예측"]},
        }), encoding="utf-8")
        if current:
            (folder / "workflow.json").write_text(json.dumps({"current": current}), encoding="utf-8")
    return folder


def test_shorts_output_lives_in_the_shared_storage(monkeypatch, tmp_path):
    monkeypatch.delenv("STORAGE_DIR", raising=False)
    settings = Settings.from_env()
    assert settings.output_dir == PROJECT_DIR.parent / "storage" / "shorts"
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path))
    assert Settings.from_env().output_dir == tmp_path / "shorts"


def test_status_is_empty_before_the_first_run(tmp_path):
    assert current_status(_settings(tmp_path))["state"] == "empty"


def test_status_follows_the_latest_day_and_its_current_revision(tmp_path):
    settings = _settings(tmp_path)
    _day(settings.output_dir, "2026-09-23")
    _day(settings.output_dir, "2026-09-24", current="revisions/abc")
    status = current_status(settings)
    assert status["date"] == "2026-09-24" and status["revision"] is True
    assert status["review_status"] == "pending" and status["video_bytes"] == 10
    assert Path(status["video_path"]).parts[-3:] == ("revisions", "abc", "clip.mp4")
    assert status["metadata"]["title"] == "제목"


def test_status_reports_a_day_without_video(tmp_path):
    settings = _settings(tmp_path)
    folder = _day(settings.output_dir, "2026-09-24", review=False)
    (folder / "selection.json").write_text(json.dumps({"status": "no_suitable_issues"}), encoding="utf-8")
    status = current_status(settings)
    assert status["selection_status"] == "no_suitable_issues" and status["review_status"] is None


def _cli(tmp_path, *args):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    env.update(PYTHONPATH=str(PROJECT_DIR / "src"), STORAGE_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "polymarket_shorts.cli", *args],
        capture_output=True, text=True, encoding="utf-8", cwd=PROJECT_DIR, env=env,
    )


def test_cli_status_and_complete_print_one_json_line(tmp_path):
    _day(tmp_path / "shorts", "2026-09-24")
    status = _cli(tmp_path, "--status")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["review_status"] == "pending"
    done = _cli(tmp_path, "--complete")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["review_status"] == "reviewed"


def test_cli_panel_commands_fail_cleanly_without_a_video(tmp_path):
    result = _cli(tmp_path, "--complete")
    assert result.returncode != 0 and "검수할 영상이 없습니다" in result.stderr
