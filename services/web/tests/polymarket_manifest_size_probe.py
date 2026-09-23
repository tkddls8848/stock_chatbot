"""실패한 generation의 compact manifest 크기를 바이트 단위로 실측하는 독립 프로브.

``write_generation``이 16 MiB 상한에 걸려 멈추면 ``manifest.json``도
``current.json``도 남지 않아 **얼마나 넘겼는지** 알 수 없다. 그런데 그 시점에
detail shard는 이미 전부 기록돼 있다. 이 프로브는 그 shard만 읽어 manifest를
같은 방식으로 재구성하고 계획서(``services/web/docs/polymarket-dashboard.md`` 7-4)의 상한과
비교한다. **API를 호출하지 않는다** — 실패한 실행이 남긴 파일만 읽는다.

compact 필드 목록은 하드코딩하지 않고 ``normalize_event``를 실제로 한 번 불러
얻는다. 어떤 필드를 compact에 두고 어떤 것을 detail로 내릴지가 바로 이 프로브가
재려는 대상이므로, 목록을 베껴 두면 고친 뒤의 예측이 조용히 틀린다.

detail 행 자체는 compact를 옮겨도 내용이 그대로다. 그래서 **고치기 전에 남은
shard로 고친 뒤의 크기를 미리 잴 수 있다** — API를 다시 순회하지 않아도 된다.

    ./venv/bin/python tests/polymarket_manifest_size_probe.py
    ./venv/bin/python tests/polymarket_manifest_size_probe.py <generation_dir>
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parents[3]
GENERATIONS_DIR = BASE_DIR / "data" / "webpub" / "polymarket" / "generations"
sys.path.insert(0, str(BASE_DIR))

from services.web.polymarket.dashboard.models import normalize_event  # noqa: E402
from services.web.polymarket.dashboard.storage import MAX_MANIFEST_BYTES  # noqa: E402

# 현재 코드가 compact에 두는 키. 빈 event 하나를 정규화해서 그대로 받아 온다.
COMPACT_KEYS = frozenset(
    normalize_event({"id": "0", "markets": []}, identity="0", low_liquidity=0.0)[0]
)


def _encoded(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _latest_generation() -> Path:
    candidates = [path for path in GENERATIONS_DIR.iterdir() if path.is_dir()]
    if not candidates:
        raise SystemExit(f"generation이 없다: {GENERATIONS_DIR}")
    return max(candidates, key=lambda path: path.name)


def measure(generation_dir: Path) -> dict[str, object]:
    shards = sorted(generation_dir.glob("details-*.jsonl"))
    if not shards:
        raise SystemExit(f"detail shard가 없다: {generation_dir}")

    total = 0
    count = 0
    field_bytes: dict[str, int] = {}
    for shard in shards:
        offset = 0
        with shard.open("rb") as handle:
            for line in handle:
                detail = json.loads(line)
                # write_generation이 만드는 manifest 항목을 그대로 재현한다.
                entry = {key: value for key, value in detail.items() if key in COMPACT_KEYS}
                entry["generation_id"] = detail.get("generation_id")
                entry["detail_ref"] = {
                    "shard": shard.name,
                    "offset": offset,
                    "length": len(line),
                    "sha256": hashlib.sha256(line).hexdigest(),
                }
                offset += len(line)
                total += len(_encoded(entry))
                count += 1
                for key, value in entry.items():
                    field_bytes[key] = field_bytes.get(key, 0) + len(_encoded({key: value}))

    return {
        "generation": generation_dir.name,
        "event_count": count,
        "compact_bytes": total,
        "detail_bytes": sum(shard.stat().st_size for shard in shards),
        "shard_count": len(shards),
        "field_bytes": field_bytes,
    }


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else _latest_generation()
    result = measure(target)
    compact = int(result["compact_bytes"])
    count = int(result["event_count"])
    verdict = "초과" if compact > MAX_MANIFEST_BYTES else "이내"

    print(f"generation   {result['generation']}")
    print(f"event 수     {count:,}")
    print(f"detail shard {int(result['detail_bytes']) / 1048576:.1f} MiB "
          f"({result['shard_count']}개)")
    print()
    print(f"compact 합계 {compact / 1048576:.2f} MiB  "
          f"(상한 {MAX_MANIFEST_BYTES / 1048576:.0f} MiB, {verdict})")
    print(f"상한 대비    {compact / MAX_MANIFEST_BYTES * 100:.0f}%")
    print(f"event 평균   {compact // max(count, 1)} B")
    print(f"16 MiB에 맞추려면 event당 {MAX_MANIFEST_BYTES // max(count, 1)} B 이하여야 한다")
    print()
    print("필드별 비중:")
    field_bytes: dict[str, int] = result["field_bytes"]  # type: ignore[assignment]
    for key, value in sorted(field_bytes.items(), key=lambda item: -item[1]):
        print(f"  {key:24s} {value / 1048576:6.2f} MiB  {value / max(compact, 1) * 100:5.1f}%  "
              f"{value // max(count, 1):4d} B/event")
    print()
    print("* manifest 봉투(accounting·categories·raw_tags 등)는 위 합계에 없다.")


if __name__ == "__main__":
    main()
