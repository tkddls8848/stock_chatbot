import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from polymarket_shorts.config import Settings
from polymarket_shorts import watch
from polymarket_shorts.watch import (CHALLENGE_DAYS, DROP_DAYS, MIN_TENURE_DAYS, choose, eligible, extend_history,
                                     horizon, rank, score_importance, update_tracking)

TODAY = date(2026, 11, 9)


def _row(identity, *, title=None, days=90.0, volume=10e6, liquidity=1e6, event_type="independent_multi",
         leader_probability=None, topic=None):
    title = title or f"Topic {identity} by...?"
    return {"id": identity, "title": title, "topic_key": topic or f"topic {identity}", "event_type": event_type,
            "days_to_end": days, "volume": volume, "liquidity": liquidity, "leader_probability": leader_probability}


def _ranked(identity, score, theme, topic=None):
    return {"id": identity, "title": f"T{identity}", "topic_key": topic or f"topic {identity}", "theme": theme,
            "score": score, "event_type": "independent_multi"}


def test_horizon_peaks_at_ninety_days_and_falls_both_ways():
    assert horizon(90) == pytest.approx(1)
    assert horizon(45) == pytest.approx(horizon(180)) == pytest.approx(.51, abs=.01)
    assert horizon(160) == pytest.approx(.63, abs=.01)
    assert horizon(729) < .01


def test_eligible_needs_horizon_volume_and_an_open_outcome():
    ledger = {"events": [
        _row("ok"), _row("soon", days=30), _row("small", volume=500_000),
        _row("settled", event_type="exclusive_multi", leader_probability=.97),
        _row("ladder_done"), _row("unknown"),
    ], "details": {"ladder_done": [{"id": "m", "label": "x", "yes": .99}, {"id": "n", "label": "y", "yes": .01}],
                   "ok": [{"id": "m", "label": "x", "yes": .3}]}}

    rows = eligible(ledger)

    # 개별 질문을 읽지 못한 묶음("unknown")은 판정 근거가 없어 열려 있다고 본다.
    assert [row["id"] for row in rows] == ["ok", "unknown"]
    assert rows[0]["horizon"] == pytest.approx(1)


def test_importance_is_cached_by_topic_and_bad_items_are_dropped(monkeypatch):
    rows = [dict(_row(name), scale=.5, horizon=1) for name in ("a", "b", "c")]
    cache = {"topic c": {"importance": 1, "theme": "다른 주제", "scored_on": "2026-11-01"}}
    asked = []
    def chat(settings, **kwargs):
        asked.append(json.loads(kwargs["user"]))
        return {"scores": [{"id": "a", "importance": 3, "theme": "연준 금리"},
                           {"id": "b", "importance": "3", "theme": "문자열 점수"},
                           {"id": "zzz", "importance": 2, "theme": "입력 밖"}]}
    monkeypatch.setattr(watch, "chat_json", chat)

    assert score_importance(rows, cache, Settings.from_env(), TODAY) == 1
    # 14일이 안 된 "c"는 다시 묻지 않는다.
    assert [item["id"] for item in asked[0]["candidates"]] == ["a", "b"]
    assert cache["topic a"] == {"importance": 3, "theme": "연준 금리", "title": "Topic a by...?", "scored_on": "2026-11-09"}
    assert "topic b" not in cache
    assert score_importance(rows[:1], cache, Settings.from_env(), TODAY) == 0


def test_rank_drops_minor_topics_and_keeps_one_variant_per_topic():
    rows = [dict(_row("a1", topic="fed"), scale=.9, horizon=1), dict(_row("a2", topic="fed"), scale=.5, horizon=1),
            dict(_row("b", topic="token"), scale=1, horizon=1)]
    cache = {"fed": {"importance": 3, "theme": "연준 금리"}, "token": {"importance": 1, "theme": "토큰"}}

    ranked = rank(rows, cache)

    assert [row["id"] for row in ranked] == ["a1"]
    assert ranked[0]["score"] == pytest.approx(.4 + .3 * .9 + .3)


def test_choose_fills_slots_with_different_themes():
    state = {"tracked": []}
    ranked = [_ranked("a", .9, "이란"), _ranked("b", .88, "이란"), _ranked("c", .8, "연준")]

    changes = choose(state, ranked, {"a", "b", "c"}, TODAY)

    assert [slot["event_id"] for slot in state["tracked"]] == ["a", "c"]
    assert [change["action"] for change in changes] == ["selected", "selected"]


def test_choose_rolls_a_closed_event_over_to_the_next_round_or_vacates():
    slot = {"topic_key": "fed decision", "event_id": "dec", "title": "Fed Decision in December?", "theme": "연준",
            "since": "2026-10-01", "score": .9, "below_days": 0}
    state = {"tracked": [dict(slot), dict(slot, topic_key="iran", event_id="iran1", theme="이란")]}
    ranked = [_ranked("jan", .85, "연준", topic="fed decision")]

    changes = choose(state, ranked, {"jan"}, TODAY)

    assert changes[0] == {"action": "rolled_over", "topic_key": "fed decision", "from": "dec", "to": "jan"}
    assert changes[1] == {"action": "closed", "topic_key": "iran", "event_id": "iran1"}
    assert [slot["event_id"] for slot in state["tracked"]] == ["jan"]


def test_choose_drops_an_open_but_ineligible_topic_after_a_week():
    slot = {"topic_key": "a", "event_id": "a", "title": "A", "theme": "이란", "since": "2026-10-01", "score": .9,
            "below_days": DROP_DAYS - 2}
    state = {"tracked": [dict(slot)]}
    choose(state, [], {"a"}, TODAY)
    assert state["tracked"][0]["below_days"] == DROP_DAYS - 1
    changes = choose(state, [], {"a"}, TODAY)
    assert changes == [{"action": "dropped", "topic_key": "a", "event_id": "a"}] and state["tracked"] == []


def test_challenger_needs_tenure_margin_and_a_consecutive_week():
    def tracked(since):
        return [{"topic_key": "topic a", "event_id": "a", "title": "A", "theme": "연준", "since": since, "score": .9,
                 "below_days": 0},
                {"topic_key": "topic b", "event_id": "b", "title": "B", "theme": "이란", "since": since, "score": .6,
                 "below_days": 0}]
    ranked = [_ranked("a", .9, "연준"), _ranked("c", .8, "유가"), _ranked("b", .6, "이란")]

    young = {"tracked": tracked((TODAY - timedelta(days=MIN_TENURE_DAYS - 1)).isoformat())}
    assert choose(young, ranked, {"a", "b", "c"}, TODAY) == [] and young["challenger"] is None

    state = {"tracked": tracked("2026-09-01")}
    for offset in range(CHALLENGE_DAYS - 1):
        assert choose(state, ranked, {"a", "b", "c"}, TODAY + timedelta(days=offset)) == []
    assert state["challenger"]["days"] == CHALLENGE_DAYS - 1
    # 하루라도 끊기면 처음부터 센다.
    skipped = json.loads(json.dumps(state))
    choose(skipped, ranked, {"a", "b", "c"}, TODAY + timedelta(days=CHALLENGE_DAYS))
    assert skipped["challenger"]["days"] == 1

    changes = choose(state, ranked, {"a", "b", "c"}, TODAY + timedelta(days=CHALLENGE_DAYS - 1))
    assert changes == [{"action": "replaced", "topic_key": "topic c", "event_id": "c", "replaced": "topic b"}]
    assert [slot["event_id"] for slot in state["tracked"]] == ["a", "c"]


def test_history_backfills_once_and_keeps_todays_recorded_value(tmp_path):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    slot = {"event_id": "e1", "title": "Fed Decision in January?", "event_type": "exclusive_multi"}
    markets = [{"id": "m1", "label": "No change", "yes": .775}, {"id": "m2", "label": "25 bps increase", "yes": .185}]

    class Prices:
        calls = []
        def daily(self, ids, *, until):
            self.calls.append(ids)
            return {"m1": {"2026-10-10": .655, "2026-11-09": .7}, "m2": {"2026-10-10": .315}}

    prices = Prices()
    stamp = datetime(2026, 11, 9, 20, tzinfo=timezone.utc)
    record = extend_history(settings, slot, markets, TODAY, prices, stamp)
    assert record["markets"]["m1"]["points"] == {"2026-10-10": .655, "2026-11-09": .775}
    assert record["backfilled_on"] == "2026-11-09"

    extend_history(settings, slot, [dict(markets[0], yes=.78)], TODAY + timedelta(days=1), prices, stamp)
    assert len(prices.calls) == 1
    saved = json.loads((tmp_path / "shorts/watch/history/e1.json").read_text(encoding="utf-8"))
    assert list(saved["markets"]["m1"]["points"]) == ["2026-10-10", "2026-11-09", "2026-11-10"]


def test_update_tracking_end_to_end(tmp_path, monkeypatch):
    settings = replace(Settings.from_env(), output_dir=tmp_path / "shorts")
    ledger = {"date": "2026-11-09", "generation_id": "g1", "generated_at": "2026-11-09T20:00:00+09:00",
              "events": [_row("fed", title="Fed Decision in January?", days=79, event_type="exclusive_multi",
                              leader_probability=.775),
                         _row("token", title="Will Base launch a token by ___ ?", days=417)],
              "details": {}}
    monkeypatch.setattr(watch, "chat_json", lambda settings, **kw: {"scores": [
        {"id": "fed", "importance": 3, "theme": "연준 금리"}, {"id": "token", "importance": 0, "theme": "토큰"}]})

    class Client:
        def detail(self, identity, generation, *, ledger=False):
            assert identity == "fed" and ledger
            return {"markets": [{"id": "m1", "outcome_label": "No change", "active": True, "closed": False,
                                 "price_valid": True, "yes_probability": .775}]}

    summary = update_tracking(settings, TODAY, ledger, Client(), None)

    assert summary["tracked"] == ["Fed Decision in January?"] and summary["llm_calls"] == 1
    state = json.loads((tmp_path / "shorts/watch/topics.json").read_text(encoding="utf-8"))
    assert state["ranking"][0]["id"] == "fed" and state["log"][0]["action"] == "selected"
    assert (tmp_path / "shorts/watch/history/fed.json").exists()


def test_price_history_reads_tokens_then_daily_points():
    from polymarket_shorts.client import PriceHistory

    class Response:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    class Session:
        urls = []
        def get(self, url, timeout):
            self.urls.append(url)
            if "gamma" in url:
                return Response([{"id": "m1", "clobTokenIds": '["111", "222"]'}])
            # 같은 한국 날짜의 점이 둘이면 뒤의 점이 남는다.
            return Response({"history": [{"t": 1762700400, "p": .6}, {"t": 1762704000, "p": .65}]})

    session = Session()
    until = datetime(2026, 11, 9, 20, tzinfo=timezone(timedelta(hours=9)))
    result = PriceHistory(session=session).daily(["m1", "m2"], until=until)

    assert "id=m1&id=m2" in session.urls[0] and "market=111" in session.urls[1] and len(session.urls) == 2
    assert result == {"m1": {"2025-11-10": .65}}
