"""그날 트렌드 조명의 기준선·부호·선정.

이 파일이 지키는 규칙은 넷이다.

**하루는 JST 하루다.** 날짜가 바뀌면 기준선을 그 주기 값으로 다시 세운다.
기준선이 어제에 머물면 "오늘 이동"이 어제부터의 이동이 된다.

**부호가 안정된 값으로만 뺀다.** binary는 제목 기준 확률로 정규화해 Yes↔No가
뒤집혀도 이동이 그대로 보이고, 다지선다는 1위가 바뀌면 숫자를 빼지 않는다 —
서로 다른 후보의 확률이라 차이가 뜻을 갖지 않는다.

**비교할 것이 없으면 조명하지 않는다.** 첫 실행은 스냅숏만 남기고 빈 화면을
쓴다. 0을 이동이라고 부르지 않는다.

**상태는 화면이 아니라 파일에 남는다.** 다음 주기가 뺄셈할 baseline·previous를
같은 파일에 쓰되, 라우트가 그것을 잘라 내보낸다.
"""

import json

from services.web.core.clock import today
from services.web.polymarket.trending import (
    build,
    candidates,
    movement,
    snapshot,
    spotlight,
)


def _event(index, *, volume=10_000.0, probability=0.5, leader="Yes",
           event_type="binary", status="ok"):
    return {
        "id": str(index),
        "title": f"event {index}",
        "category": "politics",
        "category_label": "정치·선거",
        "event_type": event_type,
        "data_status": status,
        "leader": leader,
        "leader_probability": probability,
        "volume24hr": volume,
        "liquidity": 50_000.0,
        "end_date": "2027-01-01T00:00:00Z",
    }


def _manifest(events, generation_id="20260917T000000000000+0900"):
    return {
        "generation_id": generation_id,
        "generated_at": "2026-09-17T00:00:00+09:00",
        "events": events,
    }


def _write_manifest(root, events, generation_id="20260917T000000000000+0900"):
    root.mkdir(parents=True, exist_ok=True)
    (root / "current.json").write_text(
        json.dumps(_manifest(events, generation_id), ensure_ascii=False), encoding="utf-8"
    )


def test_candidates_drop_thin_and_broken_events():
    events = [
        _event(1, volume=50_000.0),
        _event(2, volume=100.0),                 # 거래량 미달
        _event(3, volume=50_000.0, status="low_liquidity"),  # 상태 비정상
        _event(4, volume=20_000.0),
    ]
    selected = candidates(events, min_volume=2_000.0, limit=10)
    assert [row["id"] for row in selected] == ["1", "4"]


def test_candidates_keep_only_top_volume():
    events = [_event(index, volume=float(index) * 1_000.0) for index in range(3, 9)]
    selected = candidates(events, min_volume=2_000.0, limit=2)
    assert [row["id"] for row in selected] == ["8", "7"]


def test_binary_move_survives_leader_flip():
    """leader가 Yes에서 No로 넘어간 주기가 가장 큰 이동인데 원값으로는 0에 가깝다."""
    before = snapshot([_event(1, leader="Yes", probability=0.55)])
    after = _event(1, leader="No", probability=0.62)
    result = movement(after, before["1"])
    # 제목 기준: 0.55 -> 0.38. 원값으로 빼면 0.07이지만 실제 이동은 -0.17이다.
    assert result["change"] == -0.17
    assert result["crossed_half"] is True
    assert result["leader_changed"] is False


def test_multi_leader_change_is_not_subtracted():
    before = snapshot([_event(1, event_type="exclusive_multi", leader="A", probability=0.40)])
    after = _event(1, event_type="exclusive_multi", leader="B", probability=0.44)
    result = movement(after, before["1"])
    assert result["leader_changed"] is True
    assert result["change"] is None


def test_movement_without_baseline_is_empty():
    assert movement(_event(1), None) == {
        "change": None,
        "leader_changed": False,
        "crossed_half": False,
        "volume_change": None,
    }


def test_spotlight_puts_leader_change_first_then_size():
    rows = [
        {"id": "a", "leader_changed": False, "change_day": 0.03, "volume24hr": 10.0},
        {"id": "b", "leader_changed": False, "change_day": -0.20, "volume24hr": 10.0},
        {"id": "c", "leader_changed": True, "change_day": None, "volume24hr": 5.0},
        {"id": "d", "leader_changed": False, "change_day": 0.005, "volume24hr": 99.0},
    ]
    chosen = spotlight(rows, basis="day", limit=10, move_floor=0.02)
    # 1위 교체가 먼저, 그다음 이동 폭 순이다. 바닥(2pp) 아래는 아예 빠진다.
    assert [row["id"] for row in chosen] == ["c", "b", "a"]
    assert chosen[0]["basis_change"] is None
    assert chosen[1]["basis_change"] == -0.20


def test_first_run_only_stores_snapshot(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "trending.json"
    _write_manifest(root, [_event(1), _event(2)])
    result = build(root=root, target=target)
    assert result["state"] == "warming_up"
    assert result["spotlight"] == []
    assert result["basis"] == "previous"
    assert set(result["baseline"]["events"]) == {"1", "2"}
    assert result["baseline"]["day"] == today().isoformat()
    assert json.loads(target.read_text(encoding="utf-8"))["previous"]["events"].keys()


def test_second_run_measures_against_today_baseline(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "trending.json"
    _write_manifest(root, [_event(1, probability=0.40), _event(2, probability=0.50)])
    build(root=root, target=target)

    _write_manifest(
        root,
        [_event(1, probability=0.61), _event(2, probability=0.505)],
        generation_id="20260917T030000000000+0900",
    )
    result = build(root=root, target=target)
    assert result["state"] == "ok"
    assert result["basis"] == "day"
    assert [row["id"] for row in result["spotlight"]] == ["1"]
    assert result["spotlight"][0]["basis_change"] == 0.21
    # 기준선은 그날 첫 주기를 유지한다. 직전 주기 값으로 덮으면 하루치 이동이 사라진다.
    assert result["baseline"]["generation_id"] == "20260917T000000000000+0900"


def test_day_change_resets_baseline(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "trending.json"
    _write_manifest(root, [_event(1, probability=0.40)])
    build(root=root, target=target)

    stale = json.loads(target.read_text(encoding="utf-8"))
    stale["baseline"]["day"] = "2001-01-01"
    stale["previous"]["events"]["1"]["p"] = 0.30
    target.write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")

    _write_manifest(root, [_event(1, probability=0.45)], generation_id="g2")
    result = build(root=root, target=target)
    assert result["baseline"]["day"] == today().isoformat()
    assert result["baseline"]["generation_id"] == "g2"
    # 기준선을 막 세운 주기라 직전 주기 대비로 고른다.
    assert result["basis"] == "previous"
    assert [row["id"] for row in result["spotlight"]] == ["1"]
    assert result["spotlight"][0]["basis_change"] == 0.15


def test_new_and_volume_lists(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "trending.json"
    _write_manifest(root, [_event(1, volume=10_000.0)])
    build(root=root, target=target)

    _write_manifest(
        root,
        [_event(1, volume=25_000.0), _event(2, volume=9_000.0)],
        generation_id="g2",
    )
    result = build(root=root, target=target)
    assert [row["id"] for row in result["new_entries"]] == ["2"]
    assert [row["id"] for row in result["volume_movers"]] == ["1"]
    assert result["volume_movers"][0]["volume_change"] == 15_000.0


def test_missing_current_generation_returns_none(tmp_path):
    root = tmp_path / "polymarket"
    root.mkdir(parents=True)
    target = tmp_path / "trending.json"
    assert build(root=root, target=target) is None
    assert not target.exists()
