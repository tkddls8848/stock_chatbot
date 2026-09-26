"""로컬 영상 검수 원고와 수정 기록을 저장한다."""

from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from .scenario import Scenario


REVIEW_FILE = "review.json"
SCRIPT_FILE = "review.md"


class ReviewError(RuntimeError):
    pass


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """임시 파일에 쓰고 바꿔 끼운다. 쓰다 죽어도 잘린 JSON이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def operation_lock(target: Path, name: str = ".review.lock"):
    """프로세스 종료 시 OS가 해제하는 비차단 잠금."""
    target.mkdir(parents=True, exist_ok=True)
    with (target / name).open("a+b") as stream:
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ReviewError("이 산출물을 다른 작업이 처리 중입니다") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _digest(path: Path) -> str:
    reader = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            reader.update(block)
    return reader.hexdigest()


def _clock(zone: ZoneInfo) -> str:
    return datetime.now(zone).isoformat(timespec="seconds")


def _load(target: Path) -> dict[str, Any]:
    path = target / REVIEW_FILE
    if not path.is_file():
        raise ReviewError(f"검수 기록이 없습니다: {path}. 먼저 영상을 제작하세요")
    return json.loads(path.read_text(encoding="utf-8"))


def _minutes(seconds: float) -> str:
    return f"{int(seconds) // 60}분 {seconds - int(seconds) // 60 * 60:04.1f}초"


def _script(
    scenario: Scenario,
    metadata: dict[str, Any],
    video: Path,
    duration: float,
    clip_issues: int = 0,
) -> str:
    lines = [
        f"# 검수 원고 · {scenario.date}",
        "",
        f"- 영상: `{video.name}` ({_minutes(duration)})",
        f"- generation: `{scenario.generation_id}` / 원자료 {scenario.source_written_at}",
        "- 상태: 검수 대기",
        "",
        "고칠 곳을 자연어로 알려 주세요. 제작 원고를 고쳐 다시 렌더합니다.",
        "",
        "## 게시 메타데이터",
        "",
        f"**제목** {metadata['title']}",
        "",
        "**설명**",
        "",
        "```text",
        metadata["description"],
        "```",
        "",
        f"**태그** {', '.join(metadata['tags'])}",
    ]
    if clip_issues:
        lines[5:5] = [f"- 배경: Seedance 이미지→영상 합성(이슈 {clip_issues}개)",
                      "- 게시 시 YouTube '변경·합성 콘텐츠' 표시"]
    for index, scene in enumerate(scenario.scenes, start=1):
        title = " / ".join(scene.title.splitlines())
        lines += [
            "",
            f"## {index:02d} · {title} ({scene.kind})",
            "",
            f"**화면 수치** {scene.metric_label or '-'} — {scene.metric or '-'}",
            "",
            "**화면 문구**",
            "",
            "```text",
            scene.body,
            "```",
            "",
            f"**확인점(화면에 넣지 않음)** {scene.takeaway or '-'}",
            "",
            "**멘트**",
            "",
            scene.narration,
        ]
        if scene.selection_note:
            lines += ["", f"**원고 선별** {scene.selection_note}"]
        if scene.evidence:
            lines += ["", "**원자료 근거 문장**", ""]
            lines.extend(f"> {sentence}" for sentence in scene.evidence)
    return "\n".join(lines) + "\n"


def write_review(
    target: Path,
    *,
    scenario: Scenario,
    metadata: dict[str, Any],
    video: Path,
    duration: float,
    timezone: ZoneInfo,
    clip_issues: int = 0,
) -> Path:
    """제작 직후 검수 대기 상태를 남긴다. 새 수정본은 다시 검수한다."""
    script = target / SCRIPT_FILE
    script.write_text(_script(scenario, metadata, video, duration, clip_issues), encoding="utf-8")
    write_json(target / REVIEW_FILE, {
        "status": "pending",
        "date": scenario.date,
        "generation_id": scenario.generation_id,
        "video": video.name,
        "video_sha256": _digest(video),
        "duration_seconds": round(duration, 3),
        "produced_at": _clock(timezone),
        "script": script.name,
        # 기존 산출물의 제목·설명·태그 저장 키를 유지한다. 외부 연결은 없다.
        "youtube": metadata,
    })
    return script


def read_script(target: Path) -> str:
    script = target / SCRIPT_FILE
    if not script.is_file():
        raise ReviewError(f"검수 원고가 없습니다: {script}. 먼저 영상을 제작하세요")
    return script.read_text(encoding="utf-8")


def complete_review(target: Path) -> None:
    """현재 완성본의 검수 완료만 기록한다."""
    with operation_lock(target):
        record = _load(target)
        record["status"] = "reviewed"
        write_json(target / REVIEW_FILE, record)
