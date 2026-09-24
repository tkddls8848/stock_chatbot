"""상태 파일의 원자적 저장.

`storage/`의 상태 파일은 전부 이 모듈로 쓴다. 같은 디렉터리에 임시 파일을 끝까지
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
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterable


def write_text_atomic(path: Path, data: str | Iterable[str]) -> None:
    """`path`를 `data`로 교체한다. 실패하면 예외를 올리고 원본을 남긴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            if isinstance(data, str):
                handle.write(data)
            else:
                handle.writelines(data)
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


class FileLockTimeout(RuntimeError):
    """다른 프로세스가 잠금을 오래 쥐고 있다."""


@contextmanager
def file_lock(path: Path, *, stale_seconds: float = 60.0, timeout: float = 10.0):
    """다른 프로세스와 같이 쓰는 파일의 잠금(공유 저장소 `storage/`).

    `flock`은 NFS·SMB 위에서 믿을 수 없어 잠금 파일을 `O_CREAT | O_EXCL`로 만든다 —
    만들기에 성공한 쪽만 들어간다. 잠금을 쥔 채 죽은 프로세스가 영영 막지 않도록
    `stale_seconds`보다 오래된 잠금은 죽은 것으로 보고 치운다. 잠금 안에서 할 일은
    "다시 읽고 → 고치고 → 원자적으로 쓴다"뿐이라 수 초를 넘지 않는다.
    같은 로직이 웹의 `core/storage.py`에도 있다 — 모듈끼리 코드를 공유하지 않는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale_seconds:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() >= deadline:
                raise FileLockTimeout(str(path)) from None
            time.sleep(0.05)
            continue
        try:
            os.write(descriptor, f"{os.getpid()}\n".encode())
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
