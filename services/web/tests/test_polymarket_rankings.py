"""화면 확률 순위(`repository.summary`)가 무엇을 세는지 고정한다.

**결과 확정을 향한 수렴은 컨센서스가 굳은 것이 아니다.** 5분짜리 가격 방향과
오늘 밤 경기는 마감이 다가올수록 확률이 0·1로 빨려 들어간다. 이 event들을 확률
순위에 두면 "가장 굳은 예측" 여덟 칸이 컨센서스가 아니라 마감 시계를 재는 표가
된다 — 라벨이 Yes/No가 아닌 두 결과 시장을 읽기 시작하면서 이런 event가 한꺼번에
순위 후보로 들어왔다(2026-09-25 실측: 5분 가격 방향만 3,616건).

그날 트렌드(`trending.candidates`)가 후보를 고를 때 쓰는 기준과 **같은 상수**를
읽는다. 전체 목록·검색·필터에서는 빼지 않는다 — 지금 열려 있는 질문은 다 보여 준다.
"""

import json
from datetime import timedelta

from services.web.core.clock import now
from services.web.core.config import POLYMARKET_MIN_HOURS_TO_END
from services.web.polymarket.repository import PolymarketRepository

GENERATION = "20260925T160000000000+0900"


def _event(identity, title, *, end_date, probability=0.9, margin=0.8):
    return {
        "id": identity,
        "title": title,
        "tags": [],
        "regions": [],
        "category": "crypto",
        "category_label": "가상자산",
        "event_type": "binary",
        "data_status": "ok",
        "price_status": "ok",
        "liquidity_status": "ok",
        "liquidity": 16388.0,
        "volume24hr": 5000.0,
        "end_date": end_date,
        "leader": "Up",
        "leader_probability": probability,
        "leader_margin": margin,
    }


def _stamp(**delta):
    return (now() + timedelta(**delta)).astimezone().isoformat()


def _repository(tmp_path, events):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "current.json").write_text(
        json.dumps({"generation_id": GENERATION, "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )
    return PolymarketRepository(tmp_path)


def _ranked_ids(summary):
    binary = summary["rankings"]["binary"]
    return (
        [event["id"] for event in binary["strong"]],
        [event["id"] for event in binary["tight"]],
    )


def test_an_event_closing_within_the_horizon_leaves_the_probability_rankings(tmp_path):
    repository = _repository(
        tmp_path / "pm",
        [
            _event("soon", "Bitcoin Up or Down - 5분", end_date=_stamp(hours=1), probability=0.99),
            _event("later", "연준이 12월에 금리를 내릴까?", end_date=_stamp(days=30), probability=0.71),
        ],
    )

    strong, tight = _ranked_ids(repository.summary())

    assert strong == ["later"]
    assert tight == ["later"]


def test_the_same_event_stays_in_the_full_list(tmp_path):
    """순위에서만 뺀다. 목록·검색·필터는 지금 열린 질문을 다 보여 준다."""
    repository = _repository(
        tmp_path / "pm",
        [_event("soon", "Bitcoin Up or Down - 5분", end_date=_stamp(hours=1))],
    )

    listed = repository.events(page=1, page_size=10)

    assert [event["id"] for event in listed["events"]] == ["soon"]
    assert listed["total"] == 1


def test_the_volume_ranking_is_not_filtered(tmp_path):
    """가장 활발한 목록은 확률 순위가 아니라 참여 규모 순위다."""
    repository = _repository(
        tmp_path / "pm",
        [_event("soon", "Bitcoin Up or Down - 5분", end_date=_stamp(hours=1))],
    )

    assert [event["id"] for event in repository.summary()["most_active"]] == ["soon"]


def test_the_horizon_is_the_shared_constant(tmp_path):
    """트렌드 후보 선정과 같은 값을 읽는다. 경계 양쪽을 한 번씩 본다."""
    repository = _repository(
        tmp_path / "pm",
        [
            _event(
                "inside",
                "경계 안",
                end_date=_stamp(hours=POLYMARKET_MIN_HOURS_TO_END - 1),
            ),
            _event(
                "outside",
                "경계 밖",
                end_date=_stamp(hours=POLYMARKET_MIN_HOURS_TO_END + 1),
                probability=0.8,
                margin=0.6,
            ),
        ],
    )

    strong, _ = _ranked_ids(repository.summary())

    assert strong == ["outside"]


def test_an_unreadable_deadline_is_not_a_reason_to_drop_the_event(tmp_path):
    """마감을 읽지 못하면 걸러낼 근거가 없다. 추측으로 빼지 않는다."""
    no_date = _event("none", "마감 없음", end_date=None)
    broken = _event("broken", "마감이 깨짐", end_date="2026-13-45")
    naive = _event("naive", "시간대 없음", end_date="2026-09-25T00:00:00")
    repository = _repository(tmp_path / "pm", [no_date, broken, naive])

    strong, _ = _ranked_ids(repository.summary())

    assert sorted(strong) == ["broken", "naive", "none"]
