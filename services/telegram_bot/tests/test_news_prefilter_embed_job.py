"""임베딩 one-shot: 조절기·메모리 관문, 증분 임베딩, 섀도 군집, 매일 비교, 상태 화면."""

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np

from services.telegram_bot.features.news_prefilter import embed_job
from services.telegram_bot.features.news_prefilter.host_cpu import HostCpuVerdict
from services.telegram_bot.features.news_prefilter.optimizer import FEATURE_NAMES, TrainingSample
from services.telegram_bot.features.news_prefilter.report import _embedding_lines


class _Budget:
    def __init__(self, reasons=("",)):
        self.reasons = list(reasons)

    def check(self):
        reason = self.reasons.pop(0) if len(self.reasons) > 1 else self.reasons[0]
        return HostCpuVerdict(reason=reason, balance_percent=80.0, usage=0.02, steal=0.0, window_hours=24)


def _topic_encoder(calls):
    """'topic:N' 접두로 같은 주제를 같은 방향에 둔다."""
    def factory(_model_dir):
        def encode(texts):
            calls.append(list(texts))
            rows = []
            for text in texts:
                topic = int(text.split(":")[1].split()[0]) if text.startswith("topic:") else 99
                vector = np.zeros(8, dtype=np.float32)
                vector[topic % 8] = 1.0
                vector[(topic + 1) % 8] = 0.05 * (len(text) % 3)
                rows.append(vector / np.linalg.norm(vector))
            return np.stack(rows)
        return encode
    return factory


def _write_events(path, events):
    path.write_text(json.dumps([
        {"event_id": eid, "text": text, "signature": 0, "first_seen": seen, "last_seen": seen}
        for eid, text, seen in events
    ]), encoding="utf-8")


def _fixed_now(monkeypatch, hour=12):
    monkeypatch.setattr(embed_job, "now", lambda: datetime(2026, 11, 28, hour, tzinfo=timezone(timedelta(hours=9))))


def _run(tmp_path, **kwargs):
    defaults = dict(
        embed_dir=tmp_path / "embedding", model_dir=tmp_path / "model",
        event_file=tmp_path / "events.json", observation_file=tmp_path / "obs.jsonl",
        budget=_Budget(), memory_mb=lambda: 3000.0,
    )
    defaults.update(kwargs)
    defaults["embed_dir"].mkdir(exist_ok=True)
    return embed_job.run(**defaults)


def test_skips_without_loading_model_when_memory_or_credit_is_short(tmp_path, monkeypatch):
    _fixed_now(monkeypatch)
    calls = []
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls), memory_mb=lambda: 900.0)
    assert status["result"] == "memory_low" and calls == []
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls), budget=_Budget(["credit"]))
    assert status["result"] == "credit" and calls == []
    assert _run(tmp_path)["result"] == "model_missing"
    saved = json.loads((tmp_path / "embedding" / "status.json").read_text(encoding="utf-8"))
    assert saved["result"] == "model_missing"


def test_hourly_embeds_only_new_events_and_records_shadow_pairs(tmp_path, monkeypatch):
    _fixed_now(monkeypatch, hour=1)   # 매일 작업 전 시각
    events = tmp_path / "events.json"
    _write_events(events, [
        ("a", "topic:1 kospi falls 2%", "2026-11-28T09:00:00+09:00"),
        ("b", "topic:2 fed holds rates", "2026-11-28T09:10:00+09:00"),
        ("r", "recap|KR|kospi|2026-11-28T09", "2026-11-28T09:00:00+09:00"),
    ])
    calls = []
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls))
    assert status["result"] == "ok" and status["hourly"]["embedded"] == 2
    assert all(not text.startswith("recap|") for batch in calls for text in batch)

    _write_events(events, [
        ("a", "topic:1 kospi falls 2%", "2026-11-28T09:00:00+09:00"),
        ("b", "topic:2 fed holds rates", "2026-11-28T09:10:00+09:00"),
        ("c", "topic:1 KOSPI closes 2% lower", "2026-11-28T10:00:00+09:00"),
    ])
    calls.clear()
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls))
    assert calls == [["topic:1 KOSPI closes 2% lower"]]     # 이미 임베딩한 사건은 다시 하지 않는다
    shadow = json.loads((tmp_path / "embedding" / "shadow.json").read_text(encoding="utf-8"))
    assert shadow["pairs"][0]["earlier"] == "topic:1 kospi falls 2%"
    assert shadow["days"]["2026-11-28"]["new"] == 3
    assert shadow["days"]["2026-11-28"]["0.85"] == 1
    assert status["daily_date"] == ""


def test_steal_mid_run_saves_progress_and_stops(tmp_path, monkeypatch):
    _fixed_now(monkeypatch, hour=1)
    _write_events(tmp_path / "events.json", [
        (f"e{i}", f"topic:{i} news {i}", f"2026-11-28T09:{i:02d}:00+09:00") for i in range(40)
    ])
    ticks = iter(range(0, 10000, 11))
    monkeypatch.setattr(embed_job.time, "monotonic", lambda: next(ticks))
    status = _run(tmp_path, encoder_factory=_topic_encoder([]), budget=_Budget(["", "steal"]))
    assert status["result"] == "steal"
    assert 0 < status["hourly"]["embedded"] < 40
    saved = embed_job._load_vectors(tmp_path / "embedding" / "event_vectors.npz")
    assert len(saved) == status["hourly"]["embedded"]


def test_daily_compares_v1_and_v2_once_per_day(tmp_path, monkeypatch):
    _fixed_now(monkeypatch, hour=4)
    _write_events(tmp_path / "events.json", [])
    lines = []
    start = date(2026, 11, 20)
    for i in range(240):
        day = (start + timedelta(days=i // 40)).isoformat()
        positive = i % 2
        title = f"topic:{1 if positive else 5} story {i}"
        features = {name: 0.0 for name in FEATURE_NAMES}
        lines.append({"type": "candidate", "candidate_id": str(i), "observed_at": f"{day}T10:00:00+09:00",
                      "title": title, "features": features})
        lines.append({"type": "outcome", "candidate_id": str(i), "impact": "high" if positive else "low"})
    (tmp_path / "obs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    calls = []
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls))
    comparison = status["daily"]["comparison"]
    assert status["daily_date"] == "2026-11-28"
    assert comparison["labels"] == 240 and comparison["inner_folds"] == 3
    assert comparison["v2_ap"] > comparison["prevalence"]
    calls.clear()
    status = _run(tmp_path, encoder_factory=_topic_encoder(calls))
    assert calls == [] and status["daily"]["comparison"] == comparison   # 같은 날은 다시 하지 않는다
    assert (tmp_path / "obs.jsonl").read_text(encoding="utf-8").count("\n") == 480  # 관측 파일은 읽기만


def test_compare_models_needs_enough_days():
    samples = [TrainingSample("2026-11-28", f"t{i}", {}, i % 2) for i in range(200)]
    assert embed_job.compare_models(samples, {}) is None


def test_status_screen_lines_escape_titles():
    lines = _embedding_lines({
        "status": {"at": "2026-11-28T03:25", "result": "ok", "cpu_seconds": 12.0, "max_rss_mb": 1040,
                   "hourly": {"events": 3000, "embedded": 40},
                   "daily_date": "2026-11-28",
                   "daily": {"comparison": {"v1_ap": 0.88, "v2_ap": 0.91, "prevalence": 0.72,
                                            "test_days": ["2026-11-27"], "labels": 1011}}},
        "shadow": {"days": {"2026-11-28": {"new": 10, "0.70": 3, "0.85": 1}},
                   "pairs": [{"similarity": 0.83, "new": "S&P <500>", "earlier": "a"}]},
    })
    text = "\n".join(lines)
    assert "S&amp;P &lt;500&gt;" in text
    assert "v2(임베딩) AP 0.91" in text
    assert _embedding_lines(None) == []
