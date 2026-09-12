"""generation 보존과 실패 정리를 검증한다.

이 파일이 지키는 규칙은 둘이다. **살아 있는 generation을 지우지 않는다**와
**실패가 디스크에 쓰레기를 남기지 않는다**. detail shard는 generation 하나가
100 MiB를 넘어(실측 116 MiB) 3시간 주기로 쌓이면 1GB 인스턴스의 디스크가 먼저
찬다. 반대로 `keep`을 잘못 세면 방금 승격한 current가 가리키는 shard를 지워
상세 화면이 통째로 죽는다 — 어느 쪽으로 틀려도 조용히 운영이 멈춘다.
"""

import json

import pytest

from web.polymarket.dashboard.storage import (
    KEEP_GENERATIONS,
    MAX_MANIFEST_BYTES,
    prune_generations,
    read_detail,
    write_generation,
)


def _manifest(generation_id: str, opened: int = 1) -> dict:
    return {
        "generation_id": generation_id,
        "accounting": {
            "fetched_record_count": opened,
            "unique_event_count": opened,
            "duplicate_event_count": 0,
            "open_event_count": opened,
            "excluded_non_open_count": 0,
            "consensus_ready_event_count": opened,
            "unavailable_event_count": 0,
        },
        "category_counts": {"politics": opened},
    }


def _rows(count: int, filler: int = 0):
    """filler는 **compact 쪽**을 부풀린다.

    manifest에 들어가는 것은 compact뿐이라 detail만 키우면 shard만 커지고
    manifest 상한은 영원히 걸리지 않는다.
    """
    for index in range(count):
        compact = {
            "id": str(index),
            "title": f"event {index}" + "x" * filler,
            "category": "politics",
        }
        detail = {**compact, "description": "detail", "markets": []}
        yield compact, detail


def _generation_names(root):
    return sorted(path.name for path in (root / "generations").iterdir() if path.is_dir())


def test_write_generation_promotes_current_and_reads_the_detail_back(tmp_path):
    result = write_generation(tmp_path, _manifest("20260901T000000000000+0900"), _rows(3))

    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert current["generation_id"] == "20260901T000000000000+0900"
    assert len(result["events"]) == 3
    # byte-addressed seek이 실제로 그 행을 되찾는지 확인한다.
    assert read_detail(tmp_path, result["events"][2])["title"] == "event 2"


def test_prune_keeps_the_current_and_the_previous_generation(tmp_path):
    for name in ("20260901T000100000000+0900", "20260901T000200000000+0900",
                 "20260901T000300000000+0900", "20260901T000400000000+0900"):
        (tmp_path / "generations" / name).mkdir(parents=True)

    removed = prune_generations(tmp_path)

    assert removed == ["20260901T000100000000+0900", "20260901T000200000000+0900"]
    assert _generation_names(tmp_path) == [
        "20260901T000300000000+0900",
        "20260901T000400000000+0900",
    ]


def test_prune_does_nothing_when_there_is_nothing_older_to_drop(tmp_path):
    (tmp_path / "generations" / "20260901T000100000000+0900").mkdir(parents=True)

    assert prune_generations(tmp_path) == []
    assert _generation_names(tmp_path) == ["20260901T000100000000+0900"]


def test_prune_refuses_to_keep_nothing(tmp_path):
    (tmp_path / "generations" / "20260901T000100000000+0900").mkdir(parents=True)

    with pytest.raises(ValueError):
        prune_generations(tmp_path, keep=0)

    assert _generation_names(tmp_path) == ["20260901T000100000000+0900"]


def test_successful_write_prunes_but_leaves_the_generation_current_points_at(tmp_path):
    names = [f"20260901T00{index:02d}00000000+0900" for index in range(1, 4)]
    for name in names:
        write_generation(tmp_path, _manifest(name), _rows(2))

    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert current["generation_id"] == names[-1]
    assert _generation_names(tmp_path) == names[-KEEP_GENERATIONS:]
    # current가 가리키는 shard가 남아 있어야 상세가 뜬다.
    assert read_detail(tmp_path, current["events"][0])["title"] == "event 0"


def test_oversized_manifest_removes_its_own_directory_and_keeps_last_good(tmp_path):
    good = "20260901T000100000000+0900"
    write_generation(tmp_path, _manifest(good, opened=2), _rows(2))

    # compact 항목을 부풀려 manifest 상한만 넘긴다.
    huge = 20000
    with pytest.raises(ValueError, match="manifest exceeds"):
        write_generation(
            tmp_path, _manifest("20260901T000200000000+0900", opened=huge), _rows(huge, filler=800)
        )

    assert _generation_names(tmp_path) == [good]
    current = json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))
    assert current["generation_id"] == good


def test_failed_row_iteration_removes_its_own_directory(tmp_path):
    good = "20260901T000100000000+0900"
    write_generation(tmp_path, _manifest(good), _rows(1))

    def exploding_rows():
        yield {"id": "0", "title": "t"}, {"id": "0", "markets": []}
        raise RuntimeError("source failed mid-walk")

    with pytest.raises(RuntimeError):
        write_generation(tmp_path, _manifest("20260901T000200000000+0900"), exploding_rows())

    assert _generation_names(tmp_path) == [good]
    assert json.loads((tmp_path / "current.json").read_text(encoding="utf-8"))[
        "generation_id"
    ] == good


def test_manifest_limit_reports_the_actual_size(tmp_path):
    huge = 20000
    with pytest.raises(ValueError) as error:
        write_generation(
            tmp_path, _manifest("20260901T000100000000+0900", opened=huge), _rows(huge, filler=800)
        )

    message = str(error.value)
    assert str(MAX_MANIFEST_BYTES) in message
    assert f"{huge} events" in message
