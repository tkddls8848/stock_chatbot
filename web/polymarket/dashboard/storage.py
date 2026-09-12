"""Compact manifest와 byte-addressed detail JSONL generation 저장."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any, Iterable

from shared.core.storage import write_json_atomic

logger = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_SHARD_BYTES = 16 * 1024 * 1024
# current와 직전 하나만 남긴다. detail shard는 generation 하나가 100 MiB를
# 넘으므로(실측 116 MiB) 두 벌 넘게 두면 2시간 주기로 디스크가 먼저 찬다.
# 직전 하나를 남기는 것은 이력이 아니라, 승격 순간에 이미 들어와 있던 요청이
# 자기가 읽던 generation의 shard를 계속 seek할 수 있게 하기 위한 것이다 —
# current.json의 event가 자기 generation_id를 들고 있어서 그렇다.
KEEP_GENERATIONS = 2


def _encoded(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _validate_accounting(manifest: dict[str, Any]) -> None:
    accounting = manifest.get("accounting", {})
    fetched = int(accounting.get("fetched_record_count", 0))
    unique = int(accounting.get("unique_event_count", 0))
    duplicate = int(accounting.get("duplicate_event_count", 0))
    opened = int(accounting.get("open_event_count", 0))
    non_open = int(accounting.get("excluded_non_open_count", 0))
    ready = int(accounting.get("consensus_ready_event_count", 0))
    unavailable = int(accounting.get("unavailable_event_count", 0))
    if fetched != unique + duplicate:
        raise ValueError("fetched accounting mismatch")
    if unique != opened + non_open:
        raise ValueError("unique accounting mismatch")
    if opened != ready + unavailable:
        raise ValueError("open accounting mismatch")
    if sum(manifest.get("category_counts", {}).values()) != opened:
        raise ValueError("category accounting mismatch")


def prune_generations(root: Path, keep: int = KEEP_GENERATIONS) -> list[str]:
    """오래된 generation 디렉토리를 지우고 지운 이름을 돌려준다.

    generation_id가 ``%Y%m%dT%H%M%S%f%z``라 이름 정렬이 곧 생성 순서다.
    실패해도 예외를 올리지 않는다 — 이 시점에는 새 generation이 이미 승격돼
    있어서, 정리에 실패했다고 성공한 수집을 실패로 되돌릴 이유가 없다.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    base = root / "generations"
    if not base.is_dir():
        return []
    existing = sorted((path for path in base.iterdir() if path.is_dir()), key=lambda p: p.name)
    removed: list[str] = []
    for path in existing[:-keep]:
        try:
            shutil.rmtree(path)
        except OSError as error:
            logger.warning("[POLYMARKET_WEB] generation 정리 실패: %s (%s)", path.name, error)
            continue
        removed.append(path.name)
    return removed


def write_generation(
    root: Path,
    manifest: dict[str, Any],
    rows: Iterable[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    generation_id = str(manifest["generation_id"])
    generation_dir = root / "generations" / generation_id
    generation_dir.mkdir(parents=True, exist_ok=False)
    events: list[dict[str, Any]] = []
    shard_index = -1
    shard_handle = None
    shard_size = 0
    try:
        for compact, detail in rows:
            detail = {**detail, "generation_id": generation_id}
            data = _encoded(detail)
            if len(data) > MAX_SHARD_BYTES:
                raise ValueError(f"detail row exceeds shard limit: {compact['id']}")
            if shard_handle is None or shard_size + len(data) > MAX_SHARD_BYTES:
                if shard_handle is not None:
                    shard_handle.flush()
                    os.fsync(shard_handle.fileno())
                    shard_handle.close()
                shard_index += 1
                shard_size = 0
                shard_handle = open(generation_dir / f"details-{shard_index:03d}.jsonl", "wb")
            offset = shard_size
            shard_handle.write(data)
            shard_size += len(data)
            events.append(
                {
                    **compact,
                    "generation_id": generation_id,
                    "detail_ref": {
                        "shard": f"details-{shard_index:03d}.jsonl",
                        "offset": offset,
                        "length": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    },
                }
            )
        if shard_handle is not None:
            shard_handle.flush()
            os.fsync(shard_handle.fileno())
            shard_handle.close()
            shard_handle = None
        full_manifest = {**manifest, "events": events, "detail_shard_count": shard_index + 1}
        _validate_accounting(full_manifest)
        manifest_data = json.dumps(full_manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(manifest_data) > MAX_MANIFEST_BYTES:
            # 실제 크기를 함께 남긴다. 이 실패에서 알아야 할 것은 "넘었다"가
            # 아니라 "얼마나"이고, 그걸 모르면 shard를 뒤져 다시 재야 한다.
            raise ValueError(
                f"manifest exceeds {MAX_MANIFEST_BYTES} bytes: "
                f"{len(manifest_data)} bytes / {len(events)} events"
            )
        write_json_atomic(generation_dir / "manifest.json", full_manifest)
        # current는 마지막에만 바꾼다. 앞 단계 실패 시 last-good이 그대로 남는다.
        write_json_atomic(root / "current.json", full_manifest)
        prune_generations(root)
        return full_manifest
    except BaseException:
        if shard_handle is not None:
            shard_handle.close()
        # 이 실행이 만든 디렉토리만 지운다. current.json은 아직 이쪽을 가리킨
        # 적이 없으므로 참조하는 곳이 없고, shard는 generation 하나에 100 MiB를
        # 넘어 남겨 두면 실패가 반복될수록 디스크만 찬다.
        shutil.rmtree(generation_dir, ignore_errors=True)
        raise


def read_detail(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    ref = event["detail_ref"]
    path = root / "generations" / str(event["generation_id"]) / ref["shard"]
    with open(path, "rb") as handle:
        handle.seek(int(ref["offset"]))
        data = handle.read(int(ref["length"]))
    if hashlib.sha256(data).hexdigest() != ref["sha256"]:
        raise ValueError("detail hash mismatch")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("detail is not an object")
    return value
