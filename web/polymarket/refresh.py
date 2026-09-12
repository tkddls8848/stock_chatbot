"""현재 Polymarket 전체 generation을 만드는 독립 one-shot.

과거 값은 읽거나 쓰지 않는다. 실패 시 status만 갱신하고 current(last-good)는
절대 교체하지 않는다.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import statistics
import time
from typing import Any, Iterator

from shared.core.clock import ensure_jst, now
from shared.core.config import (
    POLYMARKET_BASE_URL,
    POLYMARKET_PROXY_URL,
    POLYMARKET_TIMEOUT,
    POLYMARKET_WEB_DIR,
    POLYMARKET_WEB_LOW_LIQUIDITY,
    POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS,
    POLYMARKET_WEB_MAX_DAILY_REQUESTS,
)
from shared.core.storage import write_json_atomic
from web.polymarket.dashboard.client import EventsClient
from web.polymarket.dashboard.models import normalize_event
from web.polymarket.dashboard.storage import write_generation
from web.polymarket.dashboard.taxonomy import (
    CATEGORY_LABELS,
    NAMED_CATEGORY_TARGET,
    TAXONOMY_VERSION,
)
from web.polymarket.dashboard.transport import build_session

logger = logging.getLogger(__name__)

# 일일 예산을 세는 창. 상수는 core/config.py에 있다.
BUDGET_WINDOW_HOURS = 24

# manifest에 남길 태그 수. 태그 필터는 event_count 상위 200개만 그리므로
# (web/pages.py의 `slice(0,200)`) 그 아래는 아무도 읽지 않는다. 전부 담으면
# 꼬리의 수만 개가 manifest 봉투를 1.7 MiB(실측) 불려 16 MiB 상한을 갉아먹는다.
RAW_TAG_LIMIT = 200


def _resource_usage() -> tuple[float | None, int | None]:
    try:
        import resource
    except ImportError:  # Windows 개발 환경
        return None, None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime + usage.ru_stime), int(usage.ru_maxrss)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _attempt_history(previous: dict[str, Any], stamp: str) -> tuple[list[str], float | None]:
    attempts = [str(value) for value in previous.get("attempts", []) if _iso(str(value))]
    attempts.append(stamp)
    attempts = attempts[-6:]
    moments = [_iso(value) for value in attempts]
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(moments, moments[1:], strict=False)
        if left is not None and right is not None and right >= left
    ]
    return attempts, statistics.median(intervals) if intervals else None


def _mem_available_kib() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def recent_usage(previous: dict[str, Any], *, hours: int = BUDGET_WINDOW_HOURS) -> dict[str, Any]:
    """최근 `hours` 시간 안의 CPU·요청 사용량을 더한다.

    개수가 아니라 시각으로 자른다 — 주기가 바뀌면 "표본 12개"가 덮는 시간이
    같이 바뀌어 하루 예산이 하루를 뜻하지 않게 된다.
    """
    cutoff = now() - timedelta(hours=hours)
    samples: list[dict[str, Any]] = []
    for sample in previous.get("cpu_samples", []):
        if not isinstance(sample, dict):
            continue
        stamp = _iso(str(sample.get("at", "")))
        # 저장된 값에 오프셋이 없을 수 있다(CLAUDE.md의 시각 규칙).
        if stamp is None or ensure_jst(stamp) < cutoff:
            continue
        samples.append(sample)
    return {
        "samples": samples,
        "cpu_seconds": round(sum(float(s.get("cpu_seconds") or 0.0) for s in samples), 3),
        "requests": sum(int(s.get("requests") or 0) for s in samples),
    }


def over_budget(usage: dict[str, Any]) -> str:
    """예산을 넘겼으면 어느 쪽인지 돌려준다. 넘지 않았으면 빈 문자열.

    Nice·CPUWeight는 **경쟁이 있을 때만** 양보시킨다. 새벽에 봇이 한가하면
    이 프로세스가 CPU를 100% 쓰고 그만큼 Lightsail 버스트 크레딧이 탄다 —
    총량을 막는 것은 이 함수뿐이다.
    """
    if usage["cpu_seconds"] >= POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS:
        return "cpu"
    if usage["requests"] >= POLYMARKET_WEB_MAX_DAILY_REQUESTS:
        return "requests"
    return ""


def _base_status(previous: dict[str, Any], attempted_at: str) -> dict[str, Any]:
    attempts, observed = _attempt_history(previous, attempted_at)
    return {
        **previous,
        "previous_attempt_at": previous.get("last_attempt_at"),
        "last_attempt_at": attempted_at,
        "attempts": attempts,
        "observed_interval_seconds": observed,
    }


def refresh(*, root: Path = POLYMARKET_WEB_DIR) -> dict[str, Any]:
    started_wall = time.monotonic()
    started_cpu = time.process_time()
    cpu_before, _rss_before = _resource_usage()
    attempted_at = now().isoformat()
    status_path = root / "status.json"
    previous = _read_json(status_path)
    status = _base_status(previous, attempted_at)

    usage = recent_usage(previous)
    exceeded = over_budget(usage)
    if exceeded:
        logger.warning(
            "[POLYMARKET_WEB] 24시간 예산 초과(%s)라 이번 주기를 건너뛴다: "
            "cpu=%.1f/%.1fs requests=%d/%d",
            exceeded, usage["cpu_seconds"], POLYMARKET_WEB_MAX_DAILY_CPU_SECONDS,
            usage["requests"], POLYMARKET_WEB_MAX_DAILY_REQUESTS,
        )
        status.update({
            "last_result": "skipped_budget",
            "skipped_reason": exceeded,
            "rolling_cpu_seconds": usage["cpu_seconds"],
            "rolling_requests": usage["requests"],
            "cpu_samples": usage["samples"],
            "error": None,
        })
        write_json_atomic(status_path, status)
        # current는 건드리지 않는다. 직전 generation이 그대로 남아 화면이 산다.
        return {
            "state": "skipped_budget",
            "reason": exceeded,
            "cpu_seconds": usage["cpu_seconds"],
            "requests": usage["requests"],
        }

    generation_id = now().strftime("%Y%m%dT%H%M%S%f%z")
    client = EventsClient(
        base_url=POLYMARKET_BASE_URL,
        timeout=POLYMARKET_TIMEOUT,
        session=build_session(POLYMARKET_PROXY_URL),
    )
    accounting = Counter()
    category_counts = Counter()
    type_counts = Counter()
    status_counts = Counter()
    raw_tags = Counter()
    unique_ids: set[str] = set()
    activity = {
        "market_count": 0,
        "volume24hr": 0.0,
        "volume24hr_missing_count": 0,
        "liquidity": 0.0,
        "liquidity_missing_count": 0,
    }
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "generation_id": generation_id,
        "generated_at": attempted_at,
        "source": "Polymarket Gamma /events/keyset",
        # pagination_mode·coverage_status는 순회가 끝난 뒤 client.stats에서 받는다.
        # 여기에 "complete"를 박아 두면 화면 상단이 순회 결과를 확인하지 않고
        # "전수 순회 완료"를 쓴다.
        "taxonomy_version": TAXONOMY_VERSION,
        "named_category_target": NAMED_CATEGORY_TARGET,
    }

    def rows() -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        scan_index = 0
        for page in client.walk_pages():
            for event in page:
                accounting["fetched_record_count"] += 1
                raw_identity = event.get("id") or event.get("slug")
                if raw_identity:
                    identity = str(raw_identity)
                else:
                    accounting["missing_identity_count"] += 1
                    identity = f"scan-{scan_index}"
                scan_index += 1
                if identity in unique_ids:
                    accounting["duplicate_event_count"] += 1
                    continue
                unique_ids.add(identity)
                accounting["unique_event_count"] += 1
                if event.get("active") is not True or event.get("closed") is not False:
                    accounting["excluded_non_open_count"] += 1
                    continue
                accounting["open_event_count"] += 1
                compact, detail = normalize_event(
                    event,
                    identity=identity,
                    low_liquidity=POLYMARKET_WEB_LOW_LIQUIDITY,
                )
                if compact["price_status"] == "ok":
                    accounting["consensus_ready_event_count"] += 1
                else:
                    accounting["unavailable_event_count"] += 1
                category_counts[compact["category"]] += 1
                type_counts[compact["event_type"]] += 1
                status_counts[compact["data_status"]] += 1
                # system_tags·market_count는 compact에 없다(크기 때문에 detail 전용).
                # detail이 common의 상위집합이라 집계는 여기서 그대로 읽는다.
                raw_tags.update(compact["tags"] + compact["regions"] + detail["system_tags"])
                activity["market_count"] += detail["market_count"]
                if compact["volume24hr"] is None:
                    activity["volume24hr_missing_count"] += 1
                else:
                    activity["volume24hr"] += compact["volume24hr"]
                if compact["liquidity"] is None:
                    activity["liquidity_missing_count"] += 1
                else:
                    activity["liquidity"] += compact["liquidity"]
                yield compact, detail
        open_count = accounting["open_event_count"]
        named_count = open_count - category_counts["other"]
        for key in (
            "fetched_record_count",
            "unique_event_count",
            "duplicate_event_count",
            "missing_identity_count",
            "open_event_count",
            "excluded_non_open_count",
            "consensus_ready_event_count",
            "unavailable_event_count",
        ):
            accounting.setdefault(key, 0)
        manifest.update(
            {
                "pagination_mode": client.stats.pagination_mode,
                "coverage_status": client.stats.coverage_status,
                "accounting": dict(accounting),
                "walk": {
                    "page_count": client.stats.page_count,
                    "actual_page_sizes": client.stats.actual_page_sizes,
                    "duplicate_event_count": accounting["duplicate_event_count"],
                    "missing_identity_count": accounting["missing_identity_count"],
                    "shifted_page_count": 0,
                    "source_request_count": client.endpoint.metrics.request_count,
                    "retry_count": client.endpoint.metrics.retry_count,
                    "response_bytes": client.endpoint.metrics.response_bytes,
                },
                "activity": activity,
                "category_counts": dict(category_counts),
                "type_counts": dict(type_counts),
                "status_counts": dict(status_counts),
                "categories": [
                    {"key": key, "label": label, "event_count": category_counts[key]}
                    for key, label in CATEGORY_LABELS.items()
                ],
                "raw_tags": [
                    {"tag": tag, "event_count": count}
                    for tag, count in raw_tags.most_common(RAW_TAG_LIMIT)
                ],
                "named_category_ratio": named_count / open_count if open_count else 0.0,
            }
        )

    try:
        result = write_generation(root, manifest, rows())
        # 회계 등식은 generation 승격 직후에도 다시 검증한다.
        a = result["accounting"]
        if a["fetched_record_count"] != a["unique_event_count"] + a["duplicate_event_count"]:
            raise ValueError("fetched accounting mismatch")
        if a["unique_event_count"] != a["open_event_count"] + a["excluded_non_open_count"]:
            raise ValueError("unique accounting mismatch")
        if a["open_event_count"] != a["consensus_ready_event_count"] + a["unavailable_event_count"]:
            raise ValueError("open accounting mismatch")
        if sum(result["category_counts"].values()) != a["open_event_count"]:
            raise ValueError("category accounting mismatch")

        wall_seconds = time.monotonic() - started_wall
        process_cpu_seconds = time.process_time() - started_cpu
        cpu_after, peak_rss_kib = _resource_usage()
        if cpu_before is not None and cpu_after is not None:
            process_cpu_seconds = cpu_after - cpu_before
        samples = usage["samples"] + [
            {
                "at": attempted_at,
                "cpu_seconds": round(process_cpu_seconds, 3),
                "requests": client.endpoint.metrics.request_count,
            }
        ]
        result["walk"]["walk_seconds"] = round(wall_seconds, 3)
        status.update(
            {
                "last_result": "success",
                "last_success_at": attempted_at,
                "generation_id": generation_id,
                "coverage_status": result["coverage_status"],
                "source_request_count": client.endpoint.metrics.request_count,
                "response_bytes": client.endpoint.metrics.response_bytes,
                "walk_seconds": round(wall_seconds, 3),
                "process_cpu_seconds": round(process_cpu_seconds, 3),
                "rolling_cpu_seconds": round(
                    sum(float(item.get("cpu_seconds") or 0.0) for item in samples), 3
                ),
                "rolling_requests": sum(int(item.get("requests") or 0) for item in samples),
                "cpu_samples": samples,
                "peak_rss_kib": peak_rss_kib,
                "mem_available_kib": _mem_available_kib(),
                "error": None,
            }
        )
        write_json_atomic(status_path, status)
        return result
    except BaseException as error:
        # 실패해도 쓴 것은 쓴 것이다. manifest 상한 초과처럼 219 page를 전부
        # 돌고 나서 죽는 실패가 있어, 여기서 세지 않으면 반복 실패가 예산을
        # 그대로 통과한다.
        failed_cpu = time.process_time() - started_cpu
        cpu_after, _peak = _resource_usage()
        if cpu_before is not None and cpu_after is not None:
            failed_cpu = cpu_after - cpu_before
        samples = usage["samples"] + [
            {
                "at": attempted_at,
                "cpu_seconds": round(failed_cpu, 3),
                "requests": client.endpoint.metrics.request_count,
            }
        ]
        status.update(
            {
                "last_result": "failed",
                "coverage_status": "failed",
                "error": f"{type(error).__name__}: {error}"[:500],
                "walk_seconds": round(time.monotonic() - started_wall, 3),
                "process_cpu_seconds": round(failed_cpu, 3),
                "source_request_count": client.endpoint.metrics.request_count,
                "rolling_cpu_seconds": round(
                    sum(float(item.get("cpu_seconds") or 0.0) for item in samples), 3
                ),
                "rolling_requests": sum(int(item.get("requests") or 0) for item in samples),
                "cpu_samples": samples,
            }
        )
        write_json_atomic(status_path, status)
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = refresh()
    if result.get("state") == "skipped_budget":
        # 의도된 정지다. systemd가 실패로 세지 않게 0으로 끝낸다.
        print(json.dumps(result, ensure_ascii=False))
        return
    print(
        json.dumps(
            {
                "generation_id": result["generation_id"],
                "open_event_count": result["accounting"]["open_event_count"],
                "page_count": result["walk"]["page_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
