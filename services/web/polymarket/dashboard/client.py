"""검증된 Gamma ``/events/keyset`` 전수 순회 클라이언트."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from services.web.polymarket.dashboard.transport import JsonEndpoint

REQUESTED_PAGE_SIZE = 500
MAX_PAGES = 1000


@dataclass
class WalkStats:
    pagination_mode: str = "events_keyset"
    coverage_status: str = "complete"
    page_count: int = 0
    actual_page_sizes: list[int] = field(default_factory=list)
    repeated_cursor_count: int = 0


class EventsClient:
    """G0에서 cursor 전진을 확인한 events keyset만 사용한다."""

    def __init__(self, *, base_url: str, timeout: float, session: Any | None = None):
        self.endpoint = JsonEndpoint(
            url=f"{base_url.rstrip('/')}/events/keyset",
            timeout=timeout,
            session=session,
        )
        self.stats = WalkStats()

    def walk_pages(self) -> Iterator[list[dict[str, Any]]]:
        self.stats = WalkStats()
        cursor: str | None = None
        seen_cursors: set[str] = set()
        seen_pages: set[tuple[str, ...]] = set()
        for _page_number in range(1, MAX_PAGES + 1):
            params: dict[str, Any] = {
                "closed": "false",
                "limit": REQUESTED_PAGE_SIZE,
            }
            if cursor:
                params["after_cursor"] = cursor
            payload = self.endpoint.request(params)
            raw = payload.get("events", []) if isinstance(payload, dict) else []
            page = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            signature = tuple(str(item.get("id") or item.get("slug") or "") for item in page)
            if signature and signature in seen_pages:
                self.stats.coverage_status = "failed"
                raise RuntimeError("Polymarket events keyset repeated an identical page")
            seen_pages.add(signature)
            self.stats.page_count += 1
            self.stats.actual_page_sizes.append(len(page))
            yield page

            next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
            if not next_cursor:
                return
            next_cursor = str(next_cursor)
            if next_cursor == cursor or next_cursor in seen_cursors:
                self.stats.repeated_cursor_count += 1
                self.stats.coverage_status = "failed"
                raise RuntimeError("Polymarket events keyset cursor did not advance")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        self.stats.coverage_status = "failed"
        raise RuntimeError("Polymarket events keyset exceeded its page safety limit")
