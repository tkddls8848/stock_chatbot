"""같은 폴더의 임시 파일을 교체하는 원자적 JSON 저장과, 객체 하나를 읽는 JSON 읽기."""
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    """없거나 깨졌거나 객체가 아니면 빈 dict다(제작 시도 기록·상태·승인 기록이 같이 쓴다)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
