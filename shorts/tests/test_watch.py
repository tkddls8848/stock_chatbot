import json
from dataclasses import replace
from datetime import date

from polymarket_shorts.client import Snapshot, SourceError
from polymarket_shorts.config import Settings
from polymarket_shorts.watch import ledger_dir, ledger_events, record_ledger


SUMMARY = {"generation_id": "g1", "generated_at": "2026-11-09T20:00:00+09:00"}


def _snapshot(event_factory):
    return Snapshot(SUMMARY, (
        event_factory("small", volume=1_000_000, end_date="2027-06-30T00:00:00Z"),
        event_factory("big", volume=30_000_000, end_date="2027-07-01T00:00:00Z", event_type="independent_multi",
                      leader=None, leader_probability=None),
        event_factory("soon", volume=90_000_000, end_date="2026-12-01T00:00:00Z"),
        event_factory("sport", volume=99_000_000, end_date="2027-07-01T00:00:00Z", category="sports"),
        event_factory("ends", volume=99_000_000, end_date="2026-11-10T12:00:00Z"),
        event_factory("thin", volume=99_000_000, end_date="2027-07-01T00:00:00Z", data_status="low_liquidity"),
        event_factory("missing", volume=None, liquidity=5, end_date="2027-07-01T00:00:00Z"),
    ), {})


def test_ledger_keeps_open_market_events_by_cumulative_volume(event_factory):
    rows = ledger_events(_snapshot(event_factory))

    # 경기 분야·이틀 안 마감·비정상 이벤트는 빼고, 누적 값이 없는 판은 맨 뒤로 간다.
    assert [row["id"] for row in rows] == ["soon", "big", "small", "missing"]
    assert rows[1]["leader_probability"] is None and rows[1]["topic_key"] == "fed decision"


def test_record_reads_markets_only_for_long_horizon_events(tmp_path, event_factory):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    folder = ledger_dir(settings)
    folder.mkdir(parents=True)
    (folder / "2026-07-01.json").write_text("{}", encoding="utf-8")
    (folder / "2026-07-20.json").write_text("{}", encoding="utf-8")
    (folder / "notes.json").write_text("{}", encoding="utf-8")

    class Client:
        calls = []
        def detail(self, identity, generation, *, ledger=False):
            assert generation == "g1" and ledger is True
            self.calls.append(identity)
            if identity == "small":
                raise SourceError("웹 API를 읽지 못했습니다")
            return {"markets": [
                {"id": "m1", "outcome_label": "December 31", "active": True, "closed": False,
                 "price_valid": True, "yes_probability": .24},
                {"id": "m0", "outcome_label": "October 31", "active": False, "closed": True,
                 "price_valid": True, "yes_probability": .01},
            ]}

    client = Client()
    written = record_ledger(settings, date(2026, 11, 9), _snapshot(event_factory), client)

    # "soon"은 종료까지 45일이 안 돼 개별 질문을 읽지 않는다.
    assert client.calls == ["big", "small", "missing"]
    assert json.loads((folder / "2026-11-09.json").read_text(encoding="utf-8")) == written
    assert written["details"]["big"] == [{"id": "m1", "label": "December 31", "yes": .24}]
    assert written["detail_failures"] == [{"id": "small", "reason": "웹 API를 읽지 못했습니다"}]
    assert len(written["events"]) == 4 and set(written["details"]) == {"big", "missing"}
    # 넉 달(120일)이 지난 기록만 지우고 날짜 이름이 아닌 파일은 건드리지 않는다.
    assert not (folder / "2026-07-01.json").exists()
    assert (folder / "2026-07-20.json").exists() and (folder / "notes.json").exists()


def test_client_counts_ledger_details_apart_from_production_budget():
    from polymarket_shorts.client import MAX_DETAILS, MAX_LEDGER_DETAILS, PolymarketWebClient

    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"generation_id": "g1", "id": "e1"}

    class Session:
        def get(self, url, timeout):
            return Response()

    client = PolymarketWebClient("https://example.test", session=Session())
    for _ in range(MAX_LEDGER_DETAILS):
        client.detail("e1", "g1", ledger=True)
    for _ in range(MAX_DETAILS):
        client.detail("e1", "g1")
    assert client.requests["ledger_details"] == MAX_LEDGER_DETAILS
    assert client.requests["details"] == MAX_DETAILS


def test_record_reads_tracked_events_first_and_keeps_room_for_new_ones(tmp_path, event_factory):
    from polymarket_shorts.client import MAX_LEDGER_DETAILS
    from polymarket_shorts.core.storage import write_json
    from polymarket_shorts.watch import SLOTS

    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    write_json(tmp_path / "shorts" / "watch" / "topics.json", {"tracked": [{"event_id": "e40"}]})
    events = tuple(event_factory(f"e{index}", volume=1e8 - index, end_date="2027-07-01T00:00:00Z")
                   for index in range(41))

    class Client:
        calls = []
        def detail(self, identity, generation, *, ledger=False):
            self.calls.append(identity)
            return {"markets": []}

    client = Client()
    record_ledger(settings, date(2026, 11, 9), Snapshot(SUMMARY, events, {}), client)
    assert client.calls[0] == "e40" and len(client.calls) == MAX_LEDGER_DETAILS - SLOTS
