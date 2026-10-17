"""예약 제작(매일 타이머)의 하루 시도 횟수와, 끝내 못 만든 날의 텔레그램 알림 계약.

**하루 두 번까지만 시도한다**(운영자 지시 2026-10-13). 예전에는 systemd가 15분마다 3시간 동안
여덟 번까지 다시 띄웠고, 매번 원고·배경 그림을 새로 만들며 비용을 쓰고도 아무 알림이 없었다
(10/12·10/13 음성 서버 403). 이제 첫 시도가 실패하면 비정상 종료로 systemd의 15분 뒤 재시작을
한 번 받고, 두 번째도 실패하면 정상 종료로 재시작을 끊고 포기를 기록한다. 봇은 `--review-pending`의
`failures`로 포기한 날을 받아 알리고 `--failure-ack DATE`로 확인을 남긴다 — 전달이 실패하면 다음
주기에 다시 보낸다.

시도는 시작할 때 센다. 제한 시간에 걸려 죽은 시도는 결과를 남기지 못하지만 횟수에는 들어간다.
운영자가 텔레그램(`/shorts run`)이나 CLI로 직접 돌린 제작은 세지 않는다 — 결과를 그 자리에서 본다.
"""

from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .core.clock import now
from .core.storage import write_json
from .review import ReviewError, operation_lock

MAX_ATTEMPTS = 2
ATTEMPTS_FILE = "attempts.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def run(settings: Settings, produce: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    day = datetime.now(settings.timezone).date().isoformat()
    root = settings.output_dir / day
    path = root / ATTEMPTS_FILE
    with operation_lock(root, ".attempts.lock"):
        record = {"date": day, **_read(path)}
        if record.get("gave_up"):
            return {"status": "gave_up", "date": day}
        tried = record.setdefault("attempts", [])
        if not record.get("succeeded_at"):
            if len(tried) >= MAX_ATTEMPTS:
                # 앞 시도가 결과를 남기지 못하고 끝났다(제한 시간·강제 종료).
                tried[-1].setdefault("error", "결과를 남기지 못하고 끝났습니다(제한 시간 초과 또는 강제 종료)")
                record["gave_up"] = True
                write_json(path, record)
                return {"status": "gave_up", "date": day}
            tried.append({"started_at": now().isoformat()})
            write_json(path, record)

    try:
        payload = produce()
    except Exception as exc:
        # 어떤 실패든 한 번으로 센다. 원래 예외를 그대로 올려 journal에 추적이 남게 한다.
        if not _failed(path, "쇼츠", f"{type(exc).__name__}: {exc}"):
            raise
        return {"status": "gave_up", "date": day, "stage": "쇼츠", "error": str(exc)}
    record = _read(path)
    if record and not record.get("succeeded_at"):
        record["succeeded_at"] = now().isoformat()
        write_json(path, record)
    return payload


def _failed(path: Path, stage: str, error: str) -> bool:
    """실패를 기록하고, 시도를 다 썼으면 포기로 표시해 True를 돌려준다."""
    with operation_lock(path.parent, ".attempts.lock"):
        record = _read(path)
        tried = record.get("attempts") or [{}]
        tried[-1].update(stage=stage, error=error[:500], failed_at=now().isoformat())
        record["attempts"] = tried
        record["gave_up"] = len(tried) >= MAX_ATTEMPTS
        write_json(path, record)
        return record["gave_up"]


def failures(settings: Settings) -> list[dict[str, Any]]:
    """포기했고 아직 알리지 못한 날. 봇이 매분 `--review-pending`과 함께 읽는다."""
    rows = []
    if settings.output_dir.is_dir():
        for path in sorted(settings.output_dir.glob("*/" + ATTEMPTS_FILE)):
            record = _read(path)
            if record.get("gave_up") and not record.get("notified_at"):
                last = (record.get("attempts") or [{}])[-1]
                rows.append({"date": record.get("date") or path.parent.name,
                             "attempts": len(record.get("attempts") or []),
                             "stage": last.get("stage") or "쇼츠", "error": last.get("error") or ""})
    return rows


def acknowledge(settings: Settings, day: str) -> dict[str, Any]:
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise ReviewError("제작일이 올바르지 않습니다") from None
    root = settings.output_dir / day
    path = root / ATTEMPTS_FILE
    with operation_lock(root, ".attempts.lock"):
        record = _read(path)
        if record.get("gave_up") and not record.get("notified_at"):
            record["notified_at"] = now().isoformat()
            write_json(path, record)
    return {"date": day, "notified_at": record.get("notified_at")}
