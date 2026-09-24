"""섹터 브리프의 선정·집계·실패 격리.

이 파일이 지키는 규칙은 셋이다.

**섹터는 겹치지 않는다.** event 하나가 두 그룹에 들어가면 같은 베팅이 두 단락에
나오고 집계가 이중으로 잡힌다.

**집계는 전부, 이름은 상위만.** 대상이 1,000건을 넘어 이름 상한이 실제로
걸린다. 집계까지 잘리면 화면의 event 수가 거짓이 된다.

**한 분야의 실패가 나머지를 막지 않는다.** 그리고 전부 실패하면 직전 파일을
건드리지 않는다 — 반쯤 빈 파일로 덮으면 마지막으로 성공한 정리를 잃는다.
"""

import json

import pytest

from services.web.polymarket.dashboard.models import title_probability
from services.web.polymarket.dashboard.taxonomy import assign_brief_group, brief_groups
from services.web.polymarket.sector_brief import (
    build,
    collect_groups,
    named_events,
    summarize,
)


class _Analyzer:
    """분야 라벨로 결과를 정하는 가짜 분석기."""

    def __init__(self, fail_labels=(), fail_all=False):
        self._fail_labels = set(fail_labels)
        self._fail_all = fail_all
        self.calls = []

    def analyze(self, group_label, totals, events):
        from services.web.llm import PolymarketBriefError

        self.calls.append((group_label, totals, events))
        if self._fail_all or group_label in self._fail_labels:
            raise PolymarketBriefError("boom")
        return f"{group_label} 단락 " + "가" * 80


def _event(index, tags, *, volume=100.0, probability=0.7, status="ok"):
    return {
        "id": str(index),
        "title": f"event {index}",
        "tags": tags,
        "volume24hr": volume,
        "liquidity": 1000.0,
        "leader": "Yes",
        "leader_probability": probability,
        "data_status": status,
        "event_type": "binary",
        "end_date": "2027-01-01T00:00:00Z",
    }


def _write_current(root, events):
    root.mkdir(parents=True, exist_ok=True)
    (root / "current.json").write_text(
        json.dumps({"generation_id": "20260901T000000000000+0900",
                    "generated_at": "2026-09-01T00:00:00+09:00",
                    "events": events}, ensure_ascii=False),
        encoding="utf-8",
    )


# ── 섹터·그룹 배정 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (["geopolitics", "economy"], "composite"),
        (["foreign-policy", "fed"], "composite"),
        (["geopolitics"], "geopolitics"),
        (["foreign-policy", "world"], "geopolitics"),
        (["economy"], "general"),
        (["finance"], "general"),
        (["stocks"], "equities"),
        (["pre-market"], "equities"),
        (["inflation"], "macro"),
        (["macro-indicators"], "macro"),
        # 국가 태그는 감시 대상이 아니다 — 지정학으로 끌어오지 않는다.
        (["iran", "economy"], "general"),
        (["china"], None),
        (["soccer", "ukraine"], None),
        ([], None),
    ],
)
def test_group_assignment(tags, expected):
    group = assign_brief_group(tags)
    assert (group["key"] if group else None) == expected


def test_group_priority_is_fixed_when_an_event_carries_several_tags():
    # equities가 macro·general보다 앞이라 항상 equities로 간다.
    assert assign_brief_group(["stocks", "inflation", "economy"])["key"] == "equities"
    assert assign_brief_group(["inflation", "economy"])["key"] == "macro"


def test_sectors_never_overlap():
    events = [
        _event(1, ["geopolitics", "economy"]),
        _event(2, ["geopolitics"]),
        _event(3, ["stocks"]),
        _event(4, ["soccer"]),
    ]
    buckets = collect_groups(events)

    placed = [event["id"] for bucket in buckets.values() for event in bucket]
    assert sorted(placed) == ["1", "2", "3"]
    assert len(placed) == len(set(placed))


def test_every_group_spec_is_rendered_even_when_empty():
    assert [group["key"] for group in brief_groups()] == [
        "composite", "equities", "macro", "general", "geopolitics",
    ]


# ── 집계와 이름 ────────────────────────────────────────────────────────────

def test_summary_counts_everything():
    events = [_event(i, ["stocks"], volume=10.0, probability=0.95) for i in range(5)]
    events += [_event(9, ["stocks"], volume=1.0, probability=0.5, status="no_liquidity")]

    totals = summarize(events)

    assert totals["event_count"] == 6
    assert totals["volume24hr"] == 51.0
    assert totals["probability"]["strong"] == 5
    assert totals["probability"]["tight"] == 1
    assert totals["status_counts"] == {"ok": 5, "no_liquidity": 1}


def test_named_events_take_the_largest_by_volume():
    events = [_event(i, ["stocks"], volume=float(i)) for i in range(10)]

    named = named_events(events, 3)

    assert [row["title"] for row in named] == ["event 9", "event 8", "event 7"]


def test_binary_probability_is_normalised_to_the_title_direction():
    """leader_probability는 부호가 없다. 제목 기준으로 돌려놓고 넘긴다.

    실측(2026-09-01): leader와 확률을 그대로 넘기면 모델이 셋 중 둘꼴로
    "제재 완화 가능성 74%"라고 쓴다 — 74%는 완화되지 **않을** 확률인데도.
    힌트를 더 줘도 제목 표현에 앵커링한다. 그래서 숫자를 모델이 읽는 방향에
    맞춘다.
    """
    yes = {"leader": "Yes", "leader_probability": 0.82, "event_type": "binary"}
    no = {"leader": "No", "leader_probability": 0.82, "event_type": "binary"}

    assert title_probability(yes) == 0.82
    assert title_probability(no) == 0.18
    assert title_probability({"leader": "No", "leader_probability": None}) is None


def test_binary_events_carry_only_the_title_probability():
    rows = named_events(
        [{"title": "Will X happen?", "leader": "No", "leader_probability": 0.74,
          "volume24hr": 1.0, "event_type": "binary"}],
        1,
    )

    assert rows[0]["title_probability"] == 0.26
    # leader를 같이 보내면 모델이 둘을 섞어 쓴다.
    assert "leader" not in rows[0]
    assert "leader_probability" not in rows[0]


def test_multi_choice_events_keep_the_leading_candidate():
    """다지선다는 제목이 참·거짓 명제가 아니라 정규화할 대상이 없다."""
    rows = named_events(
        [{"title": "Who wins?", "leader": "Candidate A", "leader_probability": 0.55,
          "volume24hr": 1.0, "event_type": "exclusive_multi"}],
        1,
    )

    assert rows[0]["leader"] == "Candidate A"
    assert rows[0]["leader_probability"] == 0.55
    assert "title_probability" not in rows[0]


def test_aggregate_is_not_truncated_by_the_name_limit(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"], volume=1.0) for i in range(50)])
    analyzer = _Analyzer()

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   named_limit=5, min_events=1)

    equities = next(g for g in result["groups"] if g["key"] == "equities")
    assert equities["event_count"] == 50
    assert equities["named_count"] == 5
    assert len(analyzer.calls[0][2]) == 5
    assert analyzer.calls[0][1]["event_count"] == 50


# ── 표본과 실패 ────────────────────────────────────────────────────────────

def test_thin_groups_are_left_blank_without_calling_the_model(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(3)])
    analyzer = _Analyzer()

    target = tmp_path / "brief.json"
    result = build(root=root, target=target, analyzer=analyzer, min_events=10)

    # 모든 그룹이 표본 미달이면 쓸 단락이 하나도 없다 -> 파일을 만들지 않는다.
    assert result is None
    assert not target.exists()
    assert analyzer.calls == []


def test_composite_has_a_much_lower_bar_than_the_other_groups(tmp_path):
    """복합은 두 태그를 동시에 단 event만 들어와 구조적으로 얇다(실측 8건).

    지정학을 감시 목록에 넣은 이유가 이 교차 지점이라, 표본이 얇다고 비워 두면
    그 이유가 화면에서 사라진다.
    """
    root = tmp_path / "polymarket"
    _write_current(
        root,
        [_event(i, ["geopolitics", "economy"]) for i in range(3)]
        + [_event(100 + i, ["stocks"]) for i in range(3)],
    )
    analyzer = _Analyzer()

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   min_events=10, min_events_by_group={"composite": 2})

    statuses = {g["key"]: g["status"] for g in result["groups"]}
    assert statuses["composite"] == "ok"
    # 다른 그룹은 같은 3건이어도 일반 기준을 그대로 받는다.
    assert statuses["equities"] == "insufficient_sample"
    assert [call[0] for call in analyzer.calls] == ["복합(경제·지정학)"]


def test_the_shipped_override_lets_a_two_event_composite_through():
    from services.web.core.config import (
        POLYMARKET_BRIEF_MIN_EVENTS,
        POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP,
    )

    assert POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP["composite"] < POLYMARKET_BRIEF_MIN_EVENTS
    assert POLYMARKET_BRIEF_MIN_EVENTS_BY_GROUP["composite"] >= 2


def test_one_failing_group_does_not_block_the_others(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(
        root,
        [_event(i, ["stocks"]) for i in range(12)]
        + [_event(100 + i, ["geopolitics"]) for i in range(12)],
    )
    analyzer = _Analyzer(fail_labels={"지정학"})

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   min_events=10)

    statuses = {g["key"]: g["status"] for g in result["groups"]}
    assert statuses["equities"] == "ok"
    assert statuses["geopolitics"] == "failed"


def test_a_failed_group_reuses_the_previous_paragraph(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "brief.json"
    _write_current(
        root,
        [_event(i, ["stocks"]) for i in range(12)]
        + [_event(100 + i, ["geopolitics"]) for i in range(12)],
    )

    build(root=root, target=target, analyzer=_Analyzer(), min_events=10)
    second = build(root=root, target=target,
                   analyzer=_Analyzer(fail_labels={"주식·시장"}), min_events=10)

    equities = next(g for g in second["groups"] if g["key"] == "equities")
    assert equities["status"] == "failed"
    # 실패해도 화면이 통째로 비지 않게 직전 단락을 이어받고, 낡았다고 표시한다.
    assert equities["paragraph"].startswith("주식·시장 단락")
    assert equities["stale"] is True


def test_total_failure_leaves_the_last_good_file_untouched(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "brief.json"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    build(root=root, target=target, analyzer=_Analyzer(), min_events=10)
    before = target.read_text(encoding="utf-8")

    assert build(root=root, target=target, analyzer=_Analyzer(fail_all=True),
                 min_events=10) is None
    assert target.read_text(encoding="utf-8") == before


def test_quiet_hours_skip_the_model_without_touching_the_file(tmp_path, monkeypatch):
    """야간에는 줄글만 멈춘다. 직전 파일을 건드리지 않아 화면은 그것을 계속 본다."""
    from datetime import datetime

    from services.web.core.clock import JST

    root = tmp_path / "polymarket"
    target = tmp_path / "brief.json"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    build(root=root, target=target, analyzer=_Analyzer(), min_events=10)
    before = target.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "services.web.polymarket.sector_brief.now",
        lambda: datetime(2026, 9, 5, 3, 30, tzinfo=JST),
    )
    analyzer = _Analyzer()
    result = build(root=root, target=target, analyzer=analyzer,
                   min_events=10, quiet_hours={3})

    assert result == {"state": "skipped_quiet_hours", "hour": 3}
    assert analyzer.calls == []
    assert target.read_text(encoding="utf-8") == before


def test_outside_quiet_hours_the_run_proceeds(tmp_path, monkeypatch):
    from datetime import datetime

    from services.web.core.clock import JST

    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    monkeypatch.setattr(
        "services.web.polymarket.sector_brief.now",
        lambda: datetime(2026, 9, 5, 6, 0, tzinfo=JST),
    )
    analyzer = _Analyzer()

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   min_events=10, quiet_hours={3})

    assert result["state"] == "ok"
    assert analyzer.calls


def test_the_shipped_quiet_hours_skip_only_0400():
    """08시를 거르면 기상 후 첫 화면이 미장 마감 전 상태가 된다."""
    from services.web.core.config import POLYMARKET_BRIEF_QUIET_HOURS

    assert set(POLYMARKET_BRIEF_QUIET_HOURS) == {4}


def test_quiet_hours_are_real_timer_slots():
    """timer 슬롯에 없는 시각은 영영 오지 않아 정지가 조용히 사라진다."""
    import re
    from pathlib import Path

    from services.web.core.config import POLYMARKET_BRIEF_QUIET_HOURS

    timer = Path(__file__).resolve().parents[3] / "infra/systemd/stock-chatbot-polymarket-refresh.timer"
    line = next(x for x in timer.read_text(encoding="utf-8").splitlines() if x.startswith("OnCalendar="))
    slots = {int(h) for h in re.search(r"\s([\d,]+):00:00", line).group(1).split(",")}
    assert slots == {0, 4, 8, 12, 16, 20}
    assert set(POLYMARKET_BRIEF_QUIET_HOURS) <= slots


def test_missing_generation_is_a_quiet_exit(tmp_path):
    analyzer = _Analyzer()

    assert build(root=tmp_path / "none", target=tmp_path / "brief.json",
                 analyzer=analyzer) is None
    assert analyzer.calls == []


def test_previous_probabilities_are_stored_for_the_next_run(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"], probability=0.42) for i in range(12)])

    result = build(root=root, target=tmp_path / "brief.json", analyzer=_Analyzer(),
                   min_events=10)

    assert result["previous"]["0"] == {"p": 0.42, "leader": "Yes"}
    assert len(result["previous"]) == 12


# ── 응답 검증 ──────────────────────────────────────────────────────────────
#
# 응답은 평문 단락이다. JSON 봉투로 받지 않는다 — 출력이 문자열 하나뿐이라
# 봉투가 검증에 보태는 것이 없고, 실측에서 모델이 봉투를 무시하고 평문만
# 돌려줬다(2026-09-01). 검증은 여기서 직접 한다.

def _analyzer(tmp_path, raw):
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer

    prompt = tmp_path / "p.txt"
    prompt.write_text("prompt", encoding="utf-8")

    class _Backend:
        def generate(self, **_kwargs):
            return raw

    return PolymarketBriefAnalyzer(_Backend(), prompt, 900)


def test_plain_prose_is_accepted_with_whitespace_collapsed(tmp_path):
    body = "가" * 100
    analyzer = _analyzer(tmp_path, f"\n\n  {body}\n\n{body} ")

    assert analyzer.analyze("주식·시장", {}, [{"title": "t"}]) == f"{body} {body}"


def test_an_empty_thinking_block_is_stripped(tmp_path):
    body = "가" * 100
    analyzer = _analyzer(tmp_path, f"<think>\n\n</think>\n\n{body}")

    assert analyzer.analyze("주식·시장", {}, [{"title": "t"}]) == body


def test_a_fenced_paragraph_is_unwrapped(tmp_path):
    body = "가" * 100
    analyzer = _analyzer(tmp_path, f"```\n{body}\n```")

    assert analyzer.analyze("주식·시장", {}, [{"title": "t"}]) == body


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   \n  ",
        "너무 짧다",
        "가" * 2000,
        # 봉투를 다시 만들어 보낸 응답은 단락이 아니다.
        json.dumps({"paragraph": "가" * 100}, ensure_ascii=False),
    ],
)
def test_bad_responses_are_rejected(tmp_path, raw):
    from services.web.llm import PolymarketBriefError

    analyzer = _analyzer(tmp_path, raw)

    with pytest.raises(PolymarketBriefError):
        analyzer.analyze("주식·시장", {}, [{"title": "t"}])


def test_a_paragraph_that_echoes_an_event_title_is_rejected(tmp_path):
    from services.web.llm import PolymarketBriefError

    title = "Will the Fed cut rates before December 2027?"
    analyzer = _analyzer(tmp_path, "가" * 80 + title + "가" * 80)

    with pytest.raises(PolymarketBriefError, match="echoes"):
        analyzer.analyze("거시·통화", {}, [{"title": title}])


def test_an_empty_group_never_reaches_the_backend(tmp_path):
    from services.web.llm import PolymarketBriefError

    analyzer = _analyzer(tmp_path, "가" * 100)

    with pytest.raises(PolymarketBriefError, match="no events"):
        analyzer.analyze("주식·시장", {}, [])


def test_overview_must_precede_individual_probabilities(tmp_path):
    from services.web.llm import PolymarketBriefError
    raw = "전체적으로 서로 다른 정책 질문의 전망이 섞여 있어 하나의 방향으로 묶기 어렵다. 상위 질문에서 참여자들은 정책 변경 가능성을 25%로 보고 있다."
    assert _analyzer(tmp_path, raw).analyze("거시·통화", {"event_count": 20}, [{"title": "t"}]) == raw
    with pytest.raises(PolymarketBriefError, match="overview"):
        _analyzer(tmp_path, "정책 변경 가능성은 25%이다. " + raw).analyze(
            "거시·통화", {"event_count": 20}, [{"title": "t"}])


def test_build_exposes_overview_as_first_sentence(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    result = build(root=root, target=tmp_path / "brief.json", analyzer=_Analyzer(), min_events=10,
                   quiet_hours=set())
    row = next(group for group in result["groups"] if group["status"] == "ok")
    assert row["overview"] == row["paragraph"].rstrip(".") + "."


@pytest.mark.parametrize("opening", [
    "전체적으로 다양한 이슈에 대한 시장 전망이 분산되어 있습니다.",
    "전체적으로 베팅에 대한 참여자들의 판단이 갈립니다.",
    "전체적으로 기업과 암호자산 관련 이벤트에 대한 예측이 주를 이룬다.",
    "전체적으로 금과 원유에 대한 기대가 주를 이룬다.",
    "전체적으로 베팅에 대한 참여자들의 판단이 갈린다.",
])
def test_editorial_violations_get_exactly_one_correction(tmp_path, opening):
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
    good = "전체적으로 질문별 전망의 차이가 커 하나의 정책 방향으로 묶기 어렵다. 상위 질문에서 시장 참여자들은 정책 변경 가능성을 낮게 보고 있다."
    calls = []
    class Backend:
        def generate(self, **kwargs):
            calls.append(json.loads(kwargs["user_prompt"]))
            return opening + " 상위 질문에서 시장 참여자들은 정책 변경 가능성을 낮게 보고 있다." if len(calls) == 1 else good
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    analyzer = PolymarketBriefAnalyzer(Backend(), prompt, 900)
    assert analyzer.analyze("거시", {"event_count": 20}, [{"title": "t"}]) == good
    assert len(calls) == 2
    assert calls[1]["revision"]["reason"]
    assert calls[1]["events"] == calls[0]["events"]


def test_invalid_correction_is_not_accepted(tmp_path):
    from services.web.llm import PolymarketBriefError
    raw = "전체적으로 기업과 암호자산 관련 이벤트에 대한 예측이 주를 이룬다. 상위 질문에서는 여러 기업과 자산에 대한 질문이 포함되어 있다."
    with pytest.raises(PolymarketBriefError, match="전체 요약"):
        _analyzer(tmp_path, raw).analyze("주식", {"event_count": 20}, [{"title": "t"}])


@pytest.mark.parametrize("name", ["Polymarket", "폴리마켓", "예측시장"])
def test_a_paragraph_naming_the_source_service_is_rejected(tmp_path, name):
    from services.web.llm import PolymarketBriefError
    raw = (f"전체적으로 {name} 참여자들의 전망이 엇갈려 하나의 방향으로 묶기 어렵다. "
           "상위 질문에서 참여자들은 정책 변경 가능성을 25%로 보고 있다.")
    with pytest.raises(PolymarketBriefError, match="금지어"):
        _analyzer(tmp_path, raw).analyze("거시·통화", {"event_count": 20}, [{"title": "t"}])
