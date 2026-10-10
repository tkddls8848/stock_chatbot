"""전달된 원고의 수정본에만 승인·시간 제한을 묶는 게시 게이트."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re

from .config import Settings
from .core.clock import now
from .core.storage import write_json
from .review import REVIEW_FILE, ReviewError, complete_review, operation_lock, read_script

GATE_FILE = "approval.json"
# 가운데 "ko"는 영어판을 두던 때의 언어 칸이다. 이미 전달한 원고 버튼·승인 기록이 이 형식이라 그대로 둔다.
_TOKEN = re.compile(r"(\d{4}-\d{2}-\d{2})-ko-([a-f0-9]{32})")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReviewError("검수 승인 기록을 읽을 수 없습니다") from None
    if not isinstance(value, dict):
        raise ReviewError("검수 승인 기록 형식이 잘못됐습니다")
    return value


def _identity(root: Path, settings: Settings) -> str:
    try:
        relative = root.resolve().relative_to(settings.output_dir.resolve())
    except ValueError:
        raise ReviewError("검수 제작일 폴더가 저장소 밖에 있습니다") from None
    parts = relative.parts
    if len(parts) != 1:
        raise ReviewError("검수 제작일 폴더가 올바르지 않습니다")
    day = parts[0]
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise ReviewError("검수 제작일이 올바르지 않습니다") from None
    return day


def _fingerprint(root: Path) -> tuple[Path, str]:
    from .workflow import current_target
    from .youtube import revision_id
    target = current_target(root)
    record = _read(target / REVIEW_FILE)
    if record.get("status") not in {"pending", "reviewed"}:
        raise ReviewError("현재 완성본만 검수할 수 있습니다")
    digest = hashlib.sha256(revision_id(target, record).encode())
    digest.update(str(target.relative_to(root.resolve())).encode())
    # 원고·수치 또는 게시 문구가 바뀌면 과거 버튼과 타이머를 무효화한다.
    for name in ("scenario.json", "review.md", "production.json"):
        path = target / name
        digest.update(name.encode())
        try:
            digest.update(path.read_bytes() if path.exists() else b"")
        except OSError:
            raise ReviewError("검수 제작 원고를 읽을 수 없습니다") from None
    return target, digest.hexdigest()[:32]


def _checked(root: Path, token: str) -> tuple[dict, Path]:
    gate = _read(root / GATE_FILE)
    target, fingerprint = _fingerprint(root)
    if gate.get("token") != token or gate.get("fingerprint") != fingerprint:
        raise ReviewError("이전 수정본의 검수 요청입니다. 최신 원고를 확인하세요")
    if gate.get("state") not in {"pending", "paused", "approved", "uploaded"}:
        raise ReviewError("검수 승인 상태가 올바르지 않습니다")
    if type(gate.get("timeout_minutes")) is not int or gate["timeout_minutes"] <= 0:
        raise ReviewError("검수 대기 시간이 올바르지 않습니다")
    return gate, target


def _token_root(settings: Settings, token: str) -> Path:
    match = _TOKEN.fullmatch(token)
    if not match:
        raise ReviewError("검수 요청 번호가 올바르지 않습니다")
    root = settings.output_dir / match[1]
    _identity(root, settings)
    if not (root / GATE_FILE).is_file():
        raise ReviewError("등록된 검수 요청이 없습니다")
    return root.resolve()


def resolve_root(settings: Settings, token: str, *, workflow_locked: bool = False) -> Path:
    """토큰은 경로가 아니라 등록된 날짜·수정본의 식별자다."""
    root = _token_root(settings, token)
    with nullcontext() if workflow_locked else operation_lock(root, ".workflow.lock"):
        _checked(root, token)
    return root


def register(root: Path, settings: Settings, *, workflow_locked: bool = False) -> dict:
    """새 제작·수정 성공 뒤 호출한다. 전달 확인 전에는 시계를 시작하지 않는다."""
    root = root.resolve()
    day = _identity(root, settings)
    with nullcontext() if workflow_locked else operation_lock(root, ".workflow.lock"):
        _, fingerprint = _fingerprint(root)
        path = root / GATE_FILE
        previous = _read(path) if path.exists() else {}
        if previous.get("fingerprint") == fingerprint:
            return previous
        gate = {
            "token": f"{day}-ko-{fingerprint}", "fingerprint": fingerprint,
            "date": day, "state": "pending",
            "created_at": now().isoformat(), "delivered_at": None, "deadline": None,
            "timeout_minutes": settings.review_timeout_minutes,
        }
        write_json(path, gate)
        return gate


def _roots(settings: Settings):
    if settings.output_dir.is_dir():
        for root in sorted(settings.output_dir.iterdir()):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", root.name) and (root / GATE_FILE).is_file():
                yield root.resolve()


def pending(settings: Settings) -> list[dict]:
    """과거 날짜도 포함하되 등록되지 않은 기존 제작물은 자동 편입하지 않는다."""
    rows = []
    for root in _roots(settings):
        try:
            with operation_lock(root, ".workflow.lock"):
                gate = _read(root / GATE_FILE)
                target, fingerprint = _fingerprint(root)
                if (gate.get("fingerprint") != fingerprint
                        and _read(target / REVIEW_FILE).get("status") == "pending"):
                    # 새 완성본 포인터 저장 직후 종료된 경우 새 원고를 다시 전달한다.
                    # 과거 승인·마감은 재사용하지 않는다.
                    gate = register(root, settings, workflow_locked=True)
                gate, target = _checked(root, gate.get("token", ""))
                if gate["state"] != "uploaded":
                    rows.append({**gate, "script": read_script(target),
                                 "auto_publish": settings.auto_publish})
        except ReviewError as exc:
            rows.append({"date": root.name, "state": "error", "error": str(exc)})
    return rows


def acknowledge(settings: Settings, token: str) -> dict:
    root = _token_root(settings, token)
    with operation_lock(root, ".workflow.lock"):
        gate, _ = _checked(root, token)
        if gate["state"] == "pending" and not gate.get("delivered_at"):
            delivered = now()
            gate.update(delivered_at=delivered.isoformat(),
                        deadline=(delivered + timedelta(minutes=gate["timeout_minutes"])).isoformat())
            write_json(root / GATE_FILE, gate)
        return gate


def pause(settings: Settings, token: str, *, workflow_locked: bool = False) -> dict:
    root = _token_root(settings, token)
    with nullcontext() if workflow_locked else operation_lock(root, ".workflow.lock"):
        gate, target = _checked(root, token)
        if gate["state"] == "uploaded":
            raise ReviewError("이미 게시된 영상은 수정 대기로 바꿀 수 없습니다")
        from .youtube import uploaded_record
        record = _read(target / REVIEW_FILE)
        if uploaded_record(target, record).get("video_id"):
            raise ReviewError("이미 게시된 영상은 수정 대기로 바꿀 수 없습니다")
        gate.update(state="paused", deadline=None)
        write_json(root / GATE_FILE, gate)
        record["status"] = "pending"
        write_json(target / REVIEW_FILE, record)
        return gate


def _publish(root: Path, gate: dict, target: Path, settings: Settings, reason: str) -> dict:
    from .youtube import upload
    if gate["state"] != "approved":
        gate.update(state="approved", approved_at=now().isoformat(), approval_reason=reason)
        write_json(root / GATE_FILE, gate)
    complete_review(target)
    result = upload(root, settings, workflow_locked=True)
    if result.get("status") in {"uploaded", "already_uploaded"}:
        gate.update(state="uploaded", url=result.get("url"))
        write_json(root / GATE_FILE, gate)
        longform = _publish_longform(root, settings)
        if longform:
            return {**gate, "upload": result, **result, "longform": longform}
    return {**gate, "upload": result, **result}


def _publish_longform(root: Path, settings: Settings) -> dict | None:
    """그날 쇼츠에 이어 둔 롱폼을 쇼츠와 같은 때 올린다. 실패해도 쇼츠 게시 결과는 그대로다."""
    from .longform import LongformError, linked
    from .youtube import upload_longform
    try:
        target = linked(root, settings)
        return upload_longform(target, settings) if target else None
    except (ReviewError, LongformError, OSError, ValueError, KeyError) as exc:
        return {"status": "error", "error": str(exc)}


def approve(settings: Settings, token: str) -> dict:
    root = _token_root(settings, token)
    with operation_lock(root, ".workflow.lock"):
        gate, target = _checked(root, token)
        if gate["state"] == "uploaded":
            return {**gate, "status": "already_uploaded"}
        return _publish(root, gate, target, settings, "explicit")


def _other_uploaded(root: Path, target: Path) -> bool:
    from .youtube import revision_id
    current_id = revision_id(target, _read(target / REVIEW_FILE))
    for path in root.rglob("upload.json"):
        entries = _read(path).get("revisions")
        if not isinstance(entries, dict):
            raise ReviewError("게시 이력을 확인할 수 없습니다")
        for identifier, entry in entries.items():
            if not isinstance(entry, dict):
                raise ReviewError("게시 이력을 확인할 수 없습니다")
            if entry.get("video_id") and (path.parent != target or identifier != current_id):
                return True
    return False


def tick(settings: Settings) -> list[dict]:
    results = []
    for root in _roots(settings):
        try:
            with operation_lock(root, ".workflow.lock"):
                gate = _read(root / GATE_FILE)
                gate, target = _checked(root, gate.get("token", ""))
                due = False
                if (settings.auto_publish and gate["state"] == "pending"
                        and gate.get("delivered_at") and gate.get("deadline")):
                    deadline = datetime.fromisoformat(gate["deadline"])
                    if deadline.tzinfo is None:
                        raise ReviewError("검수 만료 시각에 시간대가 없습니다")
                    due = now() >= deadline
                if due or gate["state"] == "approved":
                    timed = due or gate.get("approval_reason") == "timeout"
                    if timed and _other_uploaded(root, target):
                        gate.update(state="paused", deadline=None,
                                    pause_reason="같은 제작일의 다른 영상이 이미 게시됐습니다. 추가 게시는 직접 승인하세요")
                        write_json(root / GATE_FILE, gate)
                        results.append({**gate, "status": "paused", "reason": gate["pause_reason"]})
                        continue
                    results.append(_publish(root, gate, target, settings, "timeout"))
        except (ReviewError, OSError, ValueError) as exc:
            results.append({"date": root.name, "status": "error", "error": str(exc)})
    return results
