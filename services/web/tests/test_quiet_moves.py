"""뉴스보다 먼저 움직인 컨센서스 선정. 파일은 임시 폴더만 쓴다."""

from datetime import timedelta

from services.web.core.storage import write_json_atomic
from services.web.polymarket import trending as t


def event(id="one", **changes):
    return {"id": id, "title": "Will Taiwan sanctions end before December?", "data_status": "ok",
            "day_change": .05, "volume24hr": 25_000, "liquidity": 10_000, "category": "politics",
            "category_label": "정치", "event_type": "binary", "leader": "No", "leader_probability": .8,
            "end_date": (t.now() + timedelta(days=30)).isoformat(), **changes}


def news(documents=()):
    return {"generated_at": t.now().isoformat(), "documents": list(documents)}


def article(**changes):
    return {"kind": "news", "title": "", "text": "", "published_at": t.now().isoformat(), **changes}


def test_quiet_candidate_gates_and_ranking():
    rows = [event(), event("small", day_change=.049), event("thin", volume24hr=24_999),
            event("empty", liquidity=9_999), event("missing", day_change=None),
            event("bad", data_status="unpriced"), event("ending", end_date=t.now().isoformat()),
            event("composite", category="composite", tags=["sports"])]
    rows += [event(category, category=category) for category in ("sports", "weather_climate", "crypto", "culture")]
    assert [r["id"] for r in t.quiet_moves(rows, {}, news())["quiet_moves"]] == ["one"]
    rows += [event(str(i), day_change=-.1, volume24hr=30_000 + i) for i in range(5)]
    result = t.quiet_moves(rows, {}, news())
    assert [r["id"] for r in result["quiet_moves"]] == ["4", "3", "2", "1", "0"]
    single = t.quiet_moves([event()], {}, news())["quiet_moves"][0]
    assert round(single["probability"], 2) == .2 and single["summary"] is None
    assert t.quiet_moves([event()], {}, news([article(text="Taiwan sanctions") for _ in range(3)]))["quiet_moves"] == []
    assert t.quiet_moves([event()], {}, news([
        article(text="Taiwan sanctions", kind="report"),
        article(text="Taiwan sanctions", published_at=(t.now() - timedelta(hours=25)).isoformat()),
    ]))["quiet_moves"][0]["news_count"] == 0


def test_related_news_requires_specific_whole_words():
    annotation = {"keywords": ["대만", "가격 전망", "연간", "결과", "시장", "경제"]}
    docs = [article(title="대만 제재", text="Taiwan sanctions"), article(text="TAIWAN sanctions"),
            article(title="가격 전망 연간 결과 시장 경제", text="price market December"),
            article(text="Taiwan Taiwan"), article(text="Taiwan sanctionsXYZ"), article(title="대만족")]
    assert t.related_news_count(event(), annotation, docs) == 2
    assert t.related_news_count(event(), {}, docs) == 2
    # 조사가 붙은 한국어 제목("대만이")은 같은 낱말이다.
    assert t.related_news_count(event(), annotation, [article(title="대만이 반발")]) == 1


def test_news_freshness_and_missing_fail_closed(tmp_path):
    fresh = news()
    fresh["generated_at"] = (t.now() - timedelta(hours=24)).isoformat()
    assert t.quiet_moves([event()], {}, fresh)["quiet_moves_state"] == "ok"
    stale = news()
    stale["generated_at"] = (t.now() - timedelta(hours=26, seconds=1)).isoformat()
    assert t.quiet_moves([event()], {}, stale)["quiet_moves_state"] == "news_stale"
    assert t.quiet_moves([event()], {}, stale)["quiet_moves"] == []
    assert t.quiet_moves([event()], {}, {})["quiet_moves_state"] == "news_missing"
    root = tmp_path / "polymarket"
    write_json_atomic(root / "current.json", {"generation_id": "g", "events": [event()]})
    result = t.build(root=root, target=root / "trending.json")
    assert result["quiet_moves_state"] == "news_missing"
    assert result["quiet_moves_meta"]["news_generated_at"] is None


def test_quiet_failure_preserves_existing_trending(tmp_path, monkeypatch):
    root = tmp_path / "polymarket"
    target = root / "trending.json"
    write_json_atomic(root / "current.json", {"generation_id": "g", "events": [event(volume24hr=200_000, liquidity=50_000)]})
    first = t.build(root=root, target=target)
    target.unlink()
    def fail(*args):
        raise RuntimeError("calculation failed")
    monkeypatch.setattr(t, "quiet_moves", fail)
    result = t.build(root=root, target=target)
    assert result["quiet_moves_state"] == "failed" and result["quiet_moves"] == []
    for key in ("state", "spotlight", "new_entries", "volume_movers", "candidate_count", "baseline", "previous"):
        assert result[key] == first[key]
