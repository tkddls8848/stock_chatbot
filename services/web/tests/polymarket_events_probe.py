"""운영 출구 IP에서 Gamma events 페이지네이션을 실측하는 독립 프로브.

테스트 러너와 무관하게 실행할 수 있게 두어 Lightsail의 ``/tmp``에 복사해 쓴다.
응답 본문은 저장하지 않고 순회 품질과 태그 빈도만 JSON으로 출력한다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import resource
import time
from typing import Any

import requests


BASE_URL = "https://gamma-api.polymarket.com"
TIMEOUT_SECONDS = 30


def _records(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        value = payload.get(key, [])
    else:
        value = payload
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _identity(event: dict[str, Any], index: int) -> str:
    return str(event.get("id") or event.get("slug") or f"missing:{index}")


def _tag_names(event: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tag in event.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        name = tag.get("slug") or tag.get("label")
        if name:
            names.append(str(name))
    return names


def walk(mode: str, *, active: bool = False) -> dict[str, Any]:
    keyset = mode == "keyset"
    url = f"{BASE_URL}/events/keyset" if keyset else f"{BASE_URL}/events"
    requested_limit = 500
    session = requests.Session()
    seen: set[str] = set()
    page_signatures: set[tuple[str, ...]] = set()
    tags: Counter[str] = Counter()
    page_sizes: list[int] = []
    duplicates = 0
    shifted_pages = 0
    nested_markets = 0
    response_bytes = 0
    cursor: str | None = None
    offset = 0
    started_wall = time.monotonic()
    started_cpu = time.process_time()

    for page_number in range(1, 1001):
        params: dict[str, Any] = {
            "closed": "false",
            "limit": requested_limit,
        }
        if active:
            params["active"] = "true"
        if keyset and cursor:
            params["after_cursor"] = cursor
        if not keyset:
            params.update(
                {
                    "offset": offset,
                    "order": "id",
                    "ascending": "true",
                }
            )
        response = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        response_bytes += len(response.content)
        payload = response.json()
        events = _records(payload, "events")
        identities = tuple(_identity(event, offset + index) for index, event in enumerate(events))
        if identities in page_signatures and identities:
            raise RuntimeError(f"repeated page at {page_number}: {identities[:3]}")
        page_signatures.add(identities)
        page_sizes.append(len(events))

        page_duplicate = False
        for event, identity in zip(events, identities, strict=True):
            if identity in seen:
                duplicates += 1
                page_duplicate = True
                continue
            seen.add(identity)
            tags.update(_tag_names(event))
            nested_markets += len(event.get("markets") or [])
        if page_duplicate and not keyset:
            shifted_pages += 1

        if keyset:
            next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            if not next_cursor:
                break
            if str(next_cursor) == str(cursor):
                raise RuntimeError(f"cursor did not advance at page {page_number}")
            cursor = str(next_cursor)
        else:
            if not events:
                break
            offset += len(events)
    else:
        raise RuntimeError("page safety limit exceeded")

    return {
        "mode": mode,
        "active_filter": active,
        "requested_limit": requested_limit,
        "page_count": len(page_sizes),
        "page_sizes": page_sizes,
        "unique_event_count": len(seen),
        "duplicate_event_count": duplicates,
        "shifted_page_count": shifted_pages,
        "nested_market_count": nested_markets,
        "response_bytes": response_bytes,
        "wall_seconds": round(time.monotonic() - started_wall, 3),
        "cpu_seconds": round(time.process_time() - started_cpu, 3),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "top_tags": tags.most_common(100),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("keyset", "offset"))
    parser.add_argument("--active", action="store_true")
    args = parser.parse_args()
    proxy = os.environ.get("POLYMARKET_PROXY_URL", "").strip()
    if proxy:
        os.environ.setdefault("HTTPS_PROXY", proxy)
    print(json.dumps(walk(args.mode, active=args.active), ensure_ascii=False))


if __name__ == "__main__":
    main()
