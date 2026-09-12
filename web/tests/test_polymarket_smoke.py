"""실제 Gamma API를 호출하는 opt-in 스모크 테스트.

기본 실행(`python -m pytest -q`)에서는 건너뛴다. **운영 Lightsail은 출구 IP가
달라 한국 PC에서 열렸다는 사실이 서버 접근을 보장하지 않는다.** 새 인스턴스에서
refresh one-shot을 걸기 전에 서버에서 한 번 돌려 읽기가 되는지 확인한다.

```bash
RUN_POLYMARKET_SMOKE=1 python -m pytest -q -m polymarket_smoke
```

확인 항목은 셋이다. `/events/keyset`이 **읽히는지**(451 지역 차단이면 여기서
죽는다), 커서가 실제로 **전진하는지**, 그리고 응답이 `normalize_event`가 읽는
모양인지. mock 테스트가 답하지 못하는 것은 이 셋뿐이다 — 나머지 계약은
`test_polymarket_dashboard_client.py`와 `..._transport.py`가 본다.

CLOB(`/prices-history`)은 더 이상 확인하지 않는다. 과거 시세를 읽던 백필과
이력 경로가 철수했고(web/docs/polymarket-dashboard.md 10-3), 현재 대시보드는
지금 열린 event만 본다.
"""

import os

import pytest

from shared.core.config import POLYMARKET_BASE_URL, POLYMARKET_PROXY_URL, POLYMARKET_TIMEOUT
from web.polymarket.dashboard.client import EventsClient
from web.polymarket.dashboard.models import normalize_event
from web.polymarket.dashboard.transport import build_session

pytestmark = [
    pytest.mark.polymarket_smoke,
    pytest.mark.skipif(
        os.environ.get("RUN_POLYMARKET_SMOKE") != "1",
        reason="RUN_POLYMARKET_SMOKE=1 이 아닐 때는 실제 API를 호출하지 않는다",
    ),
]


def _client() -> EventsClient:
    return EventsClient(
        base_url=POLYMARKET_BASE_URL,
        timeout=POLYMARKET_TIMEOUT,
        session=build_session(POLYMARKET_PROXY_URL),
    )


def test_events_keyset_is_readable_from_this_exit_ip():
    """451이면 여기서 죽는다. 그때는 .env에 POLYMARKET_PROXY_URL을 채운다."""
    pages = _client().walk_pages()
    first = next(iter(pages))
    pages.close()

    assert first, "keyset 첫 page가 비어 있습니다"
    assert all(isinstance(event, dict) for event in first)


def test_the_cursor_actually_advances_past_the_first_page():
    """옛 `/markets/keyset`은 커서를 넘겨도 같은 첫 page를 돌려줬다."""
    client = _client()
    pages = client.walk_pages()
    first = next(iter(pages))
    second = next(iter(pages), None)
    pages.close()

    assert second is not None, "두 번째 page가 없습니다 — 전수 순회가 성립하지 않습니다"
    first_ids = {str(event.get("id")) for event in first}
    second_ids = {str(event.get("id")) for event in second}
    assert not (first_ids & second_ids), "두 page가 같은 event를 돌려줬습니다"
    assert client.stats.page_count == 2


def test_a_live_event_normalizes_into_the_compact_and_detail_shapes():
    """응답 형식이 바뀌면 정규화가 여기서 먼저 깨진다."""
    pages = _client().walk_pages()
    first = next(iter(pages))
    pages.close()

    open_events = [
        event
        for event in first
        if event.get("active") is True and event.get("closed") is False
    ]
    assert open_events, "열린 event를 하나도 찾지 못했습니다"

    compact, detail = normalize_event(
        open_events[0], identity=str(open_events[0].get("id")), low_liquidity=1000.0
    )

    assert compact["id"] and compact["title"]
    assert compact["event_type"] in {
        "binary", "exclusive_multi", "independent_multi", "unknown_multi"
    }
    assert compact["data_status"] in {
        "ok", "low_liquidity", "no_liquidity", "liquidity_missing", "unavailable"
    }
    # compact는 목록용이라 상세 전용 필드가 새어 들어오면 안 된다(16 MiB 상한).
    assert "markets" not in compact and "description" not in compact
    assert isinstance(detail["markets"], list)
    probability = compact["leader_probability"]
    assert probability is None or 0.0 <= probability <= 1.0
