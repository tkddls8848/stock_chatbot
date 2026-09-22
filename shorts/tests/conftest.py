"""쇼츠 테스트가 환경에 기대는 것을 한곳에 적는다.

이 스위트는 루트 `pytest`에 잡히지 않아 따로 돌려야 하고(`code_guide.md`),
그래서 조용히 썩기 쉽다. 환경이 모자라 못 도는 것과 코드가 깨진 것을 구분해
두지 않으면 빨간 줄이 쌓여도 원인을 구분할 수 없다.
"""

import os
from pathlib import Path

import pytest

from polymarket_shorts.render import find_font


def _has_cjk_font() -> bool:
    # 프로덕션과 같은 순서로 찾는다 — SHORTS_FONT_FILE 이 먼저고 시스템 경로가
    # 다음이다(config.Settings.from_env → render.find_font). 여기서만 다르게
    # 보면 skip 사유가 사실과 어긋난다.
    configured = os.getenv("SHORTS_FONT_FILE", "").strip()
    try:
        find_font(Path(configured) if configured else None)
    except Exception:
        return False
    return True


# 렌더는 한글 글리프가 있는 폰트를 시스템에서 찾는다(render.find_font). README의
# `apt-get install fonts-noto-cjk`를 건너뛴 기기에서는 코드가 멀쩡해도 못 돈다.
requires_cjk_font = pytest.mark.skipif(
    not _has_cjk_font(),
    reason="한글 폰트가 없다. fonts-noto-cjk 설치 또는 SHORTS_FONT_FILE 지정",
)

# WinGet 경로 탐색은 os.name == "nt"에서만 의미가 있고, 리눅스에서 흉내 내면
# pathlib이 WindowsPath를 만들지 못해 NotImplementedError로 끝난다.
windows_only = pytest.mark.skipif(
    os.name != "nt",
    reason="WinGet 설치 경로 탐색은 Windows에서만 검증할 수 있다",
)


@pytest.fixture
def cjk_font() -> Path:
    """프로덕션과 같은 순서로 해석한 한글 폰트 경로.

    테스트가 `find_font()`를 인자 없이 부르면 SHORTS_FONT_FILE 을 무시해,
    폰트를 지정해 둔 기기에서도 시스템 경로에 없으면 못 돈다.
    """
    configured = os.getenv("SHORTS_FONT_FILE", "").strip()
    return find_font(Path(configured) if configured else None)


@pytest.fixture
def event_factory():
    def make(identity="e1", **changes):
        return {"id": identity, "title": "Fed Decision in October?", "tags": ["fed"],
                "generation_id": "g1", "data_status": "ok", "event_type": "exclusive_multi",
                "volume24hr": 100000, "liquidity": 200000, "end_date": "2026-10-29T03:59:00Z",
                "leader": "No change", "leader_probability": .55, **changes}
    return make


@pytest.fixture
def issue_source(event_factory):
    from polymarket_shorts.client import Snapshot
    from polymarket_shorts.markets import shortlist, prepare_issue

    summary = {"generation_id": "g1", "generated_at": "2026-09-23T09:00:00+09:00", "freshness": {"state": "normal"}}
    snapshot = Snapshot(summary, (event_factory(),), {})
    candidate = shortlist(snapshot)[0][0]
    candidate["selection"] = {"reason": "연준의 금리 결정은 금융시장 자금조달 비용과 연결되는 주요 이슈입니다."}
    detail = {**event_factory(), "active": True, "closed": False, "slug": "fed-october",
              "description": "Federal Reserve target rate decision in October.", "markets": [
                  {"id": "m1", "question": "Will the Fed keep rates unchanged in October?", "outcome_label": "No change",
                   "active": True, "closed": False, "price_valid": True, "price_warning": None,
                   "yes_probability": .55, "no_probability": .45, "volume24hr": 90000, "liquidity": 100000},
                  {"id": "m2", "question": "Will the Fed raise rates by 25 bps in October?", "outcome_label": "25 bps increase",
                   "active": True, "closed": False, "price_valid": True, "price_warning": None,
                   "yes_probability": .4, "no_probability": .6, "volume24hr": 10000, "liquidity": 100000},
              ]}
    issue = prepare_issue(candidate, detail, [])
    script = {"id": "e1", "headline": "연준의 금리 결정", "question": "연준은 10월에 금리를 어떻게 결정할까요?",
              "market_labels": [{"id": "m1", "label": "10월 금리 동결"}, {"id": "m2", "label": "10월 금리 25bp 인상"}],
              "context": "금리 결정은 기업의 자금조달 비용과 연결됩니다.",
              "watch_point": "연준의 공식 결정문을 확인하세요.", "news_ids": []}
    return snapshot, candidate, detail, issue, script
