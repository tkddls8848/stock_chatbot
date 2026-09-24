"""텔레그램 관리 패널(`/shorts`)이 묻는 제작·검수 상태.

봇은 이 패키지를 import하지 않는다. `python -m polymarket_shorts.cli --status`를
하위 프로세스로 부르고 stdout의 JSON 한 줄을 읽는다 — 그 JSON이 둘 사이의 계약이다.
폴더 구조(날짜 폴더·`workflow.json`의 현재 수정본)를 아는 것은 쇼츠뿐이라 여기에 둔다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Settings
from .review import REVIEW_FILE, ReviewError
from .workflow import current_target

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def latest_root(settings: Settings) -> Path | None:
    """가장 최근 제작일 폴더. 선별만 하고 영상이 없는 날(적합 이슈 없음)도 포함한다."""
    if not settings.output_dir.is_dir():
        return None
    days = sorted(p for p in settings.output_dir.iterdir() if p.is_dir() and _DAY.fullmatch(p.name))
    return days[-1] if days else None


def current_status(settings: Settings) -> dict[str, Any]:
    root = latest_root(settings)
    if root is None:
        return {"state": "empty", "output_dir": str(settings.output_dir)}
    selection = _read(root / "selection.json")
    payload: dict[str, Any] = {
        "state": "ok",
        "date": root.name,
        "root": str(root),
        "selection_status": selection.get("status"),
        "failure": selection.get("error") or selection.get("failed_stage"),
    }
    if not (root / REVIEW_FILE).is_file() and not (root / "workflow.json").is_file():
        payload["review_status"] = None
        return payload
    try:
        target = current_target(root)
    except ReviewError as error:
        return {**payload, "state": "error", "failure": str(error)}
    record = _read(target / REVIEW_FILE)
    video = target / str(record.get("video") or "")
    payload.update({
        "target": str(target),
        "revision": target != root,
        "review_status": record.get("status"),
        "produced_at": record.get("produced_at"),
        "duration_seconds": record.get("duration_seconds"),
        "video_path": str(video) if video.is_file() else None,
        "video_bytes": video.stat().st_size if video.is_file() else None,
        "metadata": record.get("youtube") or {},
    })
    return payload
