"""상태 파일의 원자적 저장.

`data/`의 상태 파일은 전부 이 모듈로 쓴다. 같은 디렉터리에 임시 파일을 끝까지
쓰고 `os.replace()`로 바꿔치기하므로, 쓰는 도중 프로세스가 죽거나 디스크가 차도
직전 파일이 온전히 남는다. 대상 파일을 곧바로 열어 쓰면 그 순간 내용이 비고,
실패하면 잘린 JSON이 남아 다음 기동이 상태를 통째로 잃는다.

임시 파일을 **같은 디렉터리**에 두는 것이 조건이다. `os.replace()`는 같은
파일시스템 안에서만 원자적이라 `%TEMP%`를 거치면 보장이 사라진다.

실패는 삼키지 않는다. 호출자가 반환값으로 판단하는 것(스냅숏을 남겼는가,
관심종목이 저장됐는가)이 있어서, 저장 실패를 성공으로 보고하면 손실된 데이터
자체보다 나쁜 상태 — 사라진 줄 모르는 상태 — 가 된다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_text_atomic(path: Path, data: str) -> None:
    """`path`를 `data`로 교체한다. 실패하면 예외를 올리고 원본을 남긴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            # 교체 자체는 원자적이지만, OS가 죽으면 내용이 아직 캐시에만 있을 수
            # 있다. 빈 파일로 교체되는 경우를 막으려면 여기서 내려야 한다.
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """`path`를 바이트 산출물로 원자 교체한다 (PNG 등)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_json_atomic(path: Path, payload: Any, *, indent: int | None = None) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=indent))
