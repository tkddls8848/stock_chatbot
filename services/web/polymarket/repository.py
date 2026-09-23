"""Polymarket current generation을 읽는 공개 웹 전용 repository."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any

from services.web.core.clock import now
from services.web.polymarket import relevance
from services.web.polymarket.dashboard.storage import read_detail

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "webpub" / "polymarket"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_stamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _freshness(manifest: dict[str, Any], status: dict[str, Any]) -> dict[str, Any]:
    success = _parse_stamp(status.get("last_success_at") or manifest.get("generated_at"))
    interval = status.get("observed_interval_seconds")
    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = None
    age = max(0.0, (now() - success).total_seconds()) if success else None
    if success is None:
        state = "missing"
    elif interval is None or interval <= 0:
        state = "warming_up"
    elif age <= interval * 2:
        state = "normal"
    elif age <= interval * 6:
        state = "delayed"
    else:
        state = "stale"
    return {
        "state": state,
        "last_success_at": success.isoformat() if success else None,
        "age_seconds": round(age, 1) if age is not None else None,
        "observed_interval_seconds": interval,
    }


def make_etag(generation_id: str, route: str, query: dict[str, Any], variant: str = "json") -> str:
    canonical = "&".join(
        f"{key}={query[key]}" for key in sorted(query) if query[key] not in (None, "", False)
    )
    raw = f"{generation_id}|{route}|{canonical}|{variant}".encode()
    return '"' + hashlib.sha256(raw).hexdigest() + '"'


class PolymarketRepository:
    """current manifest만 mtime 단위로 교체하고 detail은 필요한 행만 seek한다."""

    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = root
        self._mtime_ns: int | None = None
        self._manifest: dict[str, Any] = {}
        self._events_by_id: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        # 검색용 주석(`annotate.py`가 쓴다). current.json과 따로 바뀌므로 mtime도
        # 따로 본다. 없으면 제목·태그만으로 검색한다.
        self._index_mtime_ns: int | None = None
        self._annotations: dict[str, tuple[str, str, str]] = {}
        self._index_lock = Lock()

    def load(self) -> dict[str, Any]:
        path = self.root / "current.json"
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return {}
        if mtime_ns == self._mtime_ns:
            return self._manifest
        with self._lock:
            if mtime_ns == self._mtime_ns:
                return self._manifest
            manifest = _read_json(path)
            events = manifest.get("events", [])
            if not manifest.get("generation_id") or not isinstance(events, list):
                return self._manifest
            index = {
                str(event["id"]): event
                for event in events
                if isinstance(event, dict) and event.get("id") is not None
            }
            self._manifest = manifest
            self._events_by_id = index
            self._mtime_ns = mtime_ns
        return self._manifest

    def load_index(self) -> dict[str, tuple[str, str, str]]:
        path = self.root / "search_index.json"
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError:
            return self._annotations
        if mtime_ns == self._index_mtime_ns:
            return self._annotations
        with self._index_lock:
            if mtime_ns == self._index_mtime_ns:
                return self._annotations
            rows = _read_json(path).get("events")
            if not isinstance(rows, dict):
                # 읽다 만 파일이면 직전 색인을 그대로 쓴다.
                return self._annotations
            self._annotations = {
                str(key): relevance.prepare_annotation(value)
                for key, value in rows.items()
                if isinstance(value, dict)
            }
            self._index_mtime_ns = mtime_ns
        return self._annotations

    def index_version(self) -> str:
        """ETag에 넣는다. 주석만 바뀐 주기에도 검색 결과가 달라지기 때문이다."""
        self.load_index()
        return str(self._index_mtime_ns or "")

    def health(self) -> dict[str, Any]:
        manifest = self.load()
        status = _read_json(self.root / "status.json")
        return {
            "available": bool(manifest),
            "generation_id": manifest.get("generation_id"),
            "generated_at": manifest.get("generated_at"),
            "coverage_status": manifest.get("coverage_status", status.get("coverage_status")),
            "freshness": _freshness(manifest, status),
            "last_attempt_at": status.get("last_attempt_at"),
            "last_result": status.get("last_result"),
            "error": status.get("error"),
            "resources": {
                key: status.get(key)
                for key in (
                    "source_request_count", "response_bytes", "walk_seconds",
                    "process_cpu_seconds", "rolling_cpu_seconds", "peak_rss_kib",
                    "mem_available_kib",
                )
            },
        }

    def summary(self, *, include_flagged: bool = False) -> dict[str, Any]:
        manifest = self.load()
        events = manifest.get("events", [])
        eligible = [
            event for event in events
            if event.get("data_status") == "ok"
            or (include_flagged and event.get("price_status") == "ok")
        ]
        by_type: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for event_type in ("binary", "exclusive_multi"):
            comparable = [
                event for event in eligible
                if event.get("event_type") == event_type
                and isinstance(event.get("leader_probability"), (int, float))
            ]
            by_type[event_type] = {
                "strong": sorted(
                    comparable, key=lambda event: event["leader_probability"], reverse=True
                )[:8],
                "tight": sorted(
                    comparable,
                    key=lambda event: event.get("leader_margin")
                    if isinstance(event.get("leader_margin"), (int, float)) else math.inf,
                )[:8],
            }
        active_events = [event for event in eligible if event.get("volume24hr") is not None]
        category_activity = []
        for category in manifest.get("categories", []):
            key = category.get("key")
            category_events = [event for event in events if event.get("category") == key]
            category_activity.append(
                {
                    **category,
                    "volume24hr": sum(float(event.get("volume24hr") or 0) for event in category_events),
                    "liquidity": sum(float(event.get("liquidity") or 0) for event in category_events),
                    "ok_count": sum(event.get("data_status") == "ok" for event in category_events),
                }
            )
        return {
            "generation_id": manifest.get("generation_id"),
            "generated_at": manifest.get("generated_at"),
            "source": manifest.get("source"),
            "coverage_status": manifest.get("coverage_status"),
            "freshness": self.health()["freshness"],
            "accounting": manifest.get("accounting", {}),
            "activity": manifest.get("activity", {}),
            "category_activity": category_activity,
            "type_counts": manifest.get("type_counts", {}),
            "status_counts": manifest.get("status_counts", {}),
            "named_category_ratio": manifest.get("named_category_ratio"),
            "named_category_target": manifest.get("named_category_target"),
            "include_flagged": include_flagged,
            "rankings": by_type,
            "most_active": sorted(
                active_events, key=lambda event: event.get("volume24hr") or 0, reverse=True
            )[:10],
        }

    def categories(self) -> dict[str, Any]:
        manifest = self.load()
        events = manifest.get("events", [])
        return {
            "generation_id": manifest.get("generation_id"),
            "categories": manifest.get("categories", []),
            "tags": manifest.get("raw_tags", []),
            "regions": sorted({region for event in events for region in event.get("regions", [])}),
            "event_types": sorted({str(event.get("event_type")) for event in events}),
            "data_statuses": sorted({str(event.get("data_status")) for event in events}),
        }

    def events(
        self,
        *,
        category: str | None = None,
        tag: str | None = None,
        region: str | None = None,
        event_type: str | None = None,
        status: str | None = None,
        query: str | None = None,
        sort: str | None = None,
        order: str = "desc",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        manifest = self.load()
        annotations = self.load_index()
        values = list(manifest.get("events", []))
        if category:
            values = [event for event in values if event.get("category") == category]
        if tag:
            values = [event for event in values if tag in event.get("tags", [])]
        if region:
            values = [event for event in values if region in event.get("regions", [])]
        if event_type:
            values = [event for event in values if event.get("event_type") == event_type]
        if status:
            values = [event for event in values if event.get("data_status") == status]
        tokens = relevance.query_tokens(query) if query else []
        ranks: dict[str, tuple[int, int]] = {}
        if tokens:
            compiled = relevance.compile_tokens(tokens)
            needed = relevance.minimum_matches(tokens)
            kept = []
            for event in values:
                rank = relevance.score(compiled, event, annotations.get(str(event.get("id"))))
                if rank[0] >= needed:
                    ranks[str(event.get("id"))] = rank
                    kept.append(event)
            values = kept
        # 질문이 있으면 관련도순이 기본이다. 질문 없이 관련도순을 고르면 매길
        # 관련도가 없으므로 거래량순으로 본다.
        sort = sort or ("relevance" if tokens else "volume24hr")
        if sort == "relevance" and not tokens:
            sort = "volume24hr"

        def volume(event: dict[str, Any]) -> float:
            value = event.get("volume24hr")
            return float(value) if isinstance(value, (int, float)) else -math.inf

        def key(event: dict[str, Any]):
            if sort == "relevance":
                return (*ranks[str(event.get("id"))], volume(event))
            value = event.get(sort)
            if sort == "title":
                return (value is None, str(value or "").casefold())
            return (value is None, float(value) if isinstance(value, (int, float)) else -math.inf)

        # 관련도는 방향이 하나뿐이다 — 덜 관련된 것부터 보여 줄 이유가 없다.
        values.sort(key=key, reverse=sort == "relevance" or order == "desc")
        total = len(values)
        start = (page - 1) * page_size
        page_events = []
        for event in values[start : start + page_size]:
            annotation = annotations.get(str(event.get("id")))
            # manifest의 dict를 고치지 않는다 — 다른 요청이 같은 객체를 읽는다.
            page_events.append({**event, "summary": annotation[2] if annotation else None})
        return {
            "generation_id": manifest.get("generation_id"),
            "total": total,
            "page": page,
            "page_size": page_size,
            "page_count": math.ceil(total / page_size) if total else 0,
            "sort": sort,
            "query_tokens": tokens,
            "search_index": {
                "annotated": sum(1 for key in annotations if key in self._events_by_id),
                "total": len(self._events_by_id),
            },
            "events": page_events,
        }

    def detail(self, event_id: str) -> dict[str, Any] | None:
        # 반환값은 쓰지 않지만 호출은 필요하다 — load()가 _events_by_id를 갱신한다.
        self.load()
        event = self._events_by_id.get(event_id)
        if event is None:
            return None
        # generation 교체와 detail seek 사이에 load가 바뀌어도 event 자체의 ref가
        # 가리키는 immutable generation을 읽는다.
        return read_detail(self.root, event)
