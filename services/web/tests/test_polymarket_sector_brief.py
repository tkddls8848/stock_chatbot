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


def _event(index, tags, *, volume=50_000.0, probability=0.7, status="ok"):
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

    assert rows[0]["title_outlook"] == "낮음"  # 0.26 — 모델에는 숫자가 아니라 등급만 간다
    # leader를 같이 보내면 모델이 둘을 섞어 쓴다.
    assert "leader" not in rows[0]
    assert "leader_outlook" not in rows[0]


def test_multi_choice_events_keep_the_leading_candidate():
    """다지선다는 제목이 참·거짓 명제가 아니라 정규화할 대상이 없다."""
    rows = named_events(
        [{"title": "Who wins?", "leader": "Candidate A", "leader_probability": 0.55,
          "volume24hr": 1.0, "event_type": "exclusive_multi"}],
        1,
    )

    assert rows[0]["leader"] == "Candidate A"
    assert rows[0]["leader_outlook"] == "엇갈림"
    assert "title_outlook" not in rows[0]


def test_aggregate_is_not_truncated_by_the_name_limit(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(50)])
    analyzer = _Analyzer()

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   named_limit=5, min_events=1)

    equities = next(g for g in result["groups"] if g["key"] == "equities")
    assert equities["event_count"] == 50
    assert equities["named_count"] == 5
    assert len(analyzer.calls[0][2]) == 5
    assert analyzer.calls[0][1]["event_count"] == 50


def test_thin_events_are_pruned_before_grouping(tmp_path):
    root = tmp_path / "polymarket"
    events = [_event(i, ["stocks"]) for i in range(5)]
    events += [_event(9, ["stocks"], volume=24_999.99), _event(10, ["stocks"], volume=None)]
    _write_current(root, events)
    analyzer = _Analyzer()

    result = build(root=root, target=tmp_path / "brief.json", analyzer=analyzer,
                   min_events=1, min_volume=25_000.0)

    equities = next(g for g in result["groups"] if g["key"] == "equities")
    assert equities["event_count"] == 5
    assert [row["title"] for row in analyzer.calls[0][2]] == [f"event {i}" for i in range(5)]
    assert result["min_volume"] == 25_000.0
    assert set(result["previous"]) == {str(i) for i in range(5)}


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


def test_total_failure_rewrites_the_file_with_only_safe_paragraphs(tmp_path):
    """전부 실패해도 파일을 쓴다. 예전에는 직전 파일을 그대로 둬서, 모델이 다른 질문의 숫자를 붙인 옛 글이
    계속 나갔다(검수 재현). 같은 형식의 직전 단락은 이어받는다."""
    root = tmp_path / "polymarket"
    target = tmp_path / "brief.json"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    first = build(root=root, target=target, analyzer=_Analyzer(), min_events=10)
    kept = next(g for g in first["groups"] if g["status"] == "ok")

    result = build(root=root, target=target, analyzer=_Analyzer(fail_all=True), min_events=10)
    row = next(g for g in result["groups"] if g["key"] == kept["key"])
    assert row["status"] == "failed" and row["stale"] is True and row["paragraph"] == kept["paragraph"]
    assert json.loads(target.read_text(encoding="utf-8"))["written_at"] == result["written_at"]


def test_total_failure_does_not_keep_an_old_paragraph_with_model_written_numbers(tmp_path):
    root = tmp_path / "polymarket"
    target = tmp_path / "brief.json"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    target.write_text(json.dumps({"groups": [{"key": "equities", "status": "ok",
                                              "paragraph": "전체적으로 엇갈린다. 국제유가 사상 최고치 달성 가능성은 20.5%다."}]},
                                 ensure_ascii=False), encoding="utf-8")
    result = build(root=root, target=target, analyzer=_Analyzer(fail_all=True), min_events=10)
    row = next(g for g in result["groups"] if g["key"] == "equities")
    assert "20.5%" not in row.get("paragraph", "")
    assert "20.5%" not in target.read_text(encoding="utf-8")


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
    raw = "전체적으로 서로 다른 정책 질문의 전망이 섞여 있어 하나의 방향으로 묶기 어렵다. 상위 질문은 정책 변경 여부를 묻는다."
    events = [{"title": "t", "fact": "‘정책 변경’에 대해 참여자들은 그 가능성을 25%로 본다."}]
    assert _analyzer(tmp_path, raw).analyze("거시·통화", {"event_count": 20}, events) == \
        raw + " ‘정책 변경’에 대해 참여자들은 그 가능성을 25%로 본다."
    with pytest.raises(PolymarketBriefError, match="overview"):
        _analyzer(tmp_path, "정책 질문 3개가 열려 있다. " + raw).analyze("거시·통화", {"event_count": 20}, events)


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
    good = "전체적으로 연준 금리 경로에서는 동결 쪽에 무게가 실리지만 연내 인하 횟수는 엇갈린다. 상위 질문에서 시장 참여자들은 정책 변경 가능성을 낮게 보고 있다."
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


@pytest.mark.parametrize("opening, carries_previous", [
    ("전체적으로 금과 원유에 대한 기대가 주를 이룬다.", False),          # 첫 문장 반려
    ("전체적으로 베팅에 대한 참여자들의 판단이 갈린다.", True),            # 금지어
    ("전체적으로 다양한 이슈에 대한 시장 전망이 분산되어 있습니다.", True),  # 문체
])
def test_an_opening_correction_does_not_hand_back_the_rejected_answer(tmp_path, opening, carries_previous):
    """돌려준 이전 응답을 모델이 글자째 다시 냈다(10/2 주식·시장). 첫 문장 반려는 새로 쓰게 한다."""
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
    good = "전체적으로 연준 금리 경로에서는 동결 쪽에 무게가 실리지만 연내 인하 횟수는 엇갈린다. 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."
    calls = []
    class Backend:
        def generate(self, **kwargs):
            calls.append((json.loads(kwargs["user_prompt"]), kwargs["temperature"]))
            return opening + " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다." if len(calls) == 1 else good
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    assert PolymarketBriefAnalyzer(Backend(), prompt, 900).analyze("거시", {"event_count": 20}, [{"title": "t"}]) == good
    assert ("previous_response" in calls[1][0]["revision"]) is carries_previous
    assert calls[0][1] < calls[1][1]  # 교정은 조금 더 높은 온도로 다시 쓴다


@pytest.mark.parametrize("first", [
    # 확률 반려
    "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 해협 정상화 가능성은 20.5%로 낮게 평가된다.",
    # 문체 반려가 먼저 걸리는 숫자 응답(12차 검수 재현)
    "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 해협 정상화 가능성은 0.205 수준으로 평가됩니다.",
    # 한글로 쓴 확률
    "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 해협 정상화 가능성은 백분의 이십 정도로 평가된다.",
    # 길이 반려 — 사유의 숫자(글자 수)도 보내지 않는다
    "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. " + "가" * 2000,
])
def test_a_correction_never_hands_numbers_back_to_the_model(tmp_path, first):
    """교정 입력에도 숫자가 없다. 반려된 응답의 숫자를 돌려주면 모델이 다른 질문에 옮겨 붙였다(12차 검수)."""
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
    good = ("전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 원유 쪽은 연말까지 사상 최고치에 이를 여지를 "
            "낮게 보고, 해협 통행 정상화도 낮게 평가된다.")
    prompts = []

    class Backend:
        def generate(self, **kwargs):
            prompts.append(kwargs["user_prompt"])
            return first if len(prompts) == 1 else good

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    paragraph = PolymarketBriefAnalyzer(Backend(), prompt, 900).analyze("복합", {"event_count": 20}, _composite_rows())
    assert paragraph.startswith(good)
    revision = json.loads(prompts[1])["revision"]
    # 숫자가 든 응답은 돌려주지 않는다. 숫자 없는 응답(길이 반려)은 고칠 데이터로 돌려줘도 된다.
    assert ("previous_response" in revision) is not (any(char.isdigit() for char in first) or "백분의" in first)
    assert not any(char.isdigit() for char in json.dumps(revision, ensure_ascii=False))
    from services.web.llm.polymarket_brief import has_model_probability
    assert not has_model_probability(json.dumps(revision, ensure_ascii=False))
    assert json.loads(prompts[1])["events"] == json.loads(prompts[0])["events"]


def test_invalid_correction_is_not_accepted(tmp_path):
    from services.web.llm import PolymarketBriefError
    raw = "전체적으로 기업과 암호자산 관련 이벤트에 대한 예측이 주를 이룬다. 상위 질문에서는 여러 기업과 자산에 대한 질문이 포함되어 있다."
    with pytest.raises(PolymarketBriefError, match="전체 요약"):
        _analyzer(tmp_path, raw).analyze("주식", {"event_count": 20}, [{"title": "t"}])


# ── 오탐: 서버 실측으로 화면에서 빠진 정상 문장 ──────────────────────────────
#
# 매 4시간 주기마다 5그룹 중 1~3그룹이 "전체 요약: 주제 나열 대신 전망의 차이…"로
# 두 번 연속 반려돼 화면에서 빠졌다. 원인은 규칙이 **낱말**만 보고 끝맺음을 보지
# 않은 것이다 — 종속절의 "포함해"·"구성하기"까지 나열로 셌고, 전망을 설명하는
# 낱말 목록이 좁아 같은 뜻의 다른 표현을 단서 없음으로 읽었다.

@pytest.mark.parametrize("opening", [
    # 종속절의 나열 낱말. 결론은 "묶기 어렵다"로 전망의 한계다.
    "전체적으로 우세가 뚜렷한 질문과 경합이 이어지는 질문이 함께 포함돼 하나의 방향으로 묶기 어렵다.",
    "전체적으로 참여자들의 판단이 한쪽으로 쏠리지 않아 분야 전체의 방향을 구성하기 어렵다.",
    # 같은 뜻을 목록에 없던 낱말로 쓴 문장.
    "전체적으로 참여자들의 시선이 금리 경로에 쏠려 있으나 방향은 아직 뚜렷하지 않다.",
    "전체적으로 낙관과 비관이 팽팽히 맞서 어느 쪽도 우위를 잡지 못한다.",
])
def test_a_first_sentence_that_explains_the_outlook_is_not_an_inventory(tmp_path, opening):
    raw = opening + " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."

    assert _analyzer(tmp_path, raw).analyze(
        "거시·통화", {"event_count": 20}, [{"title": "t"}]) == raw


def test_a_plain_declarative_ending_in_anida_is_not_honorific(tmp_path):
    """프롬프트가 "사실 확정이 아니다"라고 쓰라고 지시하는데 `니다`만 보고 막았다."""
    raw = ("전체적으로 참여자들의 전망이 엇갈려 하나로 묶기 어렵다. "
           "이 수치는 참여자들의 집단 전망이며 사실 확정이 아니다.")

    assert _analyzer(tmp_path, raw).analyze(
        "거시·통화", {"event_count": 20}, [{"title": "t"}]) == raw


@pytest.mark.parametrize("opening", [
    "전체적으로 금과 원유에 대한 기대가 주를 이룬다.",
    "전체적으로 기술·금융·원자재 등 다양한 주제로 구성되어 있다.",
    "전체적으로 여러 예측 질문에 관심이 집중된다.",
])
def test_an_opening_that_ends_in_an_inventory_is_still_rejected(tmp_path, opening):
    """나열을 허용해 품질을 떨어뜨리지 않는다. 끝맺음이 나열이면 그대로 반려다."""
    from services.web.llm import PolymarketBriefError

    raw = opening + " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."

    # "관심이 집중된다"는 관심 소개 사유로 더 구체적으로 반려된다. 어느 쪽이든 반려다.
    with pytest.raises(PolymarketBriefError, match="전체 요약|관심 소개"):
        _analyzer(tmp_path, raw).analyze("주식", {"event_count": 20}, [{"title": "t"}])


def test_the_two_overview_failures_name_different_causes(tmp_path):
    """교정은 한 번뿐이라 사유가 뭉뚱그려지면 모델이 엉뚱한 곳을 고친다."""
    from services.web.llm.polymarket_brief import PolymarketBriefError, validate_editorial

    tail = " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."
    reasons = []
    for opening in ("전체적으로 금과 원유에 대한 기대가 주를 이룬다.",
                    "전체적으로 여러 예측 질문이 열려 있다."):
        with pytest.raises(PolymarketBriefError) as caught:
            validate_editorial(opening + tail, {"event_count": 20})
        reasons.append(str(caught.value))

    assert reasons[0] != reasons[1]


@pytest.mark.parametrize("opening", [
    "전체적으로 질문별 전망의 차이가 커 하나의 정책 방향으로 묶기 어렵다.",
    "전체적으로 질문별 전망의 차이가 커 하나의 공통된 방향으로 정리하기 어렵다.",
    "전체적으로 휴전과 확전 질문이 섞여 하나의 정책 방향으로 보기 어렵다.",
])
def test_a_boilerplate_opening_is_sent_back_once(tmp_path, opening):
    """10/2 00시 다섯 분야 중 넷이 같은 문장으로 시작했다. 어느 분야에나 붙는 문장은 요약이 아니다."""
    from services.web.llm import PolymarketBriefError

    raw = opening + " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."
    with pytest.raises(PolymarketBriefError, match="상투 문장"):
        _analyzer(tmp_path, raw).analyze("지정학", {"event_count": 20}, [{"title": "t"}])


@pytest.mark.parametrize("opening", [
    "전체적으로 중동 항로 정상화와 유가 최고가에 관심을 보인다.",
    "전체적으로 중동 지역 갈등과 미국의 대이란 정책에 관심이 높다.",
    "전체적으로 중동 지역 갈등에 대한 참여자들의 전망이 집중되고 있다.",
])
def test_an_opening_that_only_introduces_interest_gets_a_shaped_correction(tmp_path, opening):
    """일반 사유로 교정하면 모델이 같은 문장을 그대로 다시 냈다. 고칠 모양을 사유에 담는다."""
    from services.web.llm import PolymarketBriefError

    raw = opening + " 상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다."
    with pytest.raises(PolymarketBriefError, match="관심 소개") as caught:
        _analyzer(tmp_path, raw).analyze("지정학", {"event_count": 20}, [{"title": "t"}])
    assert "⟨쟁점⟩에서는" in str(caught.value)


def test_the_prompt_no_longer_hands_out_a_sentence_to_copy():
    from services.web.core.config import POLYMARKET_BRIEF_PROMPT_FILE

    prompt = POLYMARKET_BRIEF_PROMPT_FILE.read_text(encoding="utf-8")

    assert "하나의 정책\n  방향으로 묶기 어렵다.\"" not in prompt
    assert "구체적 쟁점" in prompt


def test_the_prompt_states_the_first_sentence_predicate_contract():
    """규칙이 끝맺음을 보므로 프롬프트도 끝맺음을 말해야 한다."""
    from services.web.core.config import POLYMARKET_BRIEF_PROMPT_FILE

    prompt = POLYMARKET_BRIEF_PROMPT_FILE.read_text(encoding="utf-8")

    assert "첫 문장의 끝맺음이 곧 결론이다" in prompt
    assert "주를 이룬다" in prompt


@pytest.mark.parametrize("name", ["Polymarket", "폴리마켓", "예측시장"])
def test_a_paragraph_naming_the_source_service_is_rejected(tmp_path, name):
    from services.web.llm import PolymarketBriefError
    raw = (f"전체적으로 {name} 참여자들의 전망이 엇갈려 하나의 방향으로 묶기 어렵다. "
           "상위 질문에서 참여자들은 정책 변경 가능성을 낮게 보고 있다.")
    with pytest.raises(PolymarketBriefError, match="금지어"):
        _analyzer(tmp_path, raw).analyze("거시·통화", {"event_count": 20}, [{"title": "t"}])



# ── 확률 문장은 서버가 주어와 함께 쓴다 (2026-10-03 복합: 유가 문장에 호르무즈의 20.5%) ──────

_OIL = {"id": "435099", "title": "Crude Oil all time high by...?", "event_type": "independent_multi",
        "leader": None, "leader_probability": None, "volume24hr": 62870.6}
_HORMUZ = {"id": "999", "title": "Strait of Hormuz traffic returns to normal by December 31?", "event_type": "binary",
           "leader": "No", "leader_probability": 0.795, "volume24hr": 16866.3}
_OIL_DETAIL = {"markets": [
    {"outcome_label": "May 31", "closed": True, "active": True, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.0},
    {"outcome_label": "December 31", "closed": False, "active": True, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.37},
    {"outcome_label": "Inactive", "closed": False, "active": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.9},
    {"outcome_label": "Bad price", "closed": False, "active": True, "price_valid": False, "yes_label": "Yes", "yes_probability": 0.5},
]}
_LABELS = {"435099": "원유 가격이 역사적 최고치를 기록할지", "999": "호르무즈 해협 통행이 정상화될지"}


def _composite_rows(detail=lambda event: _OIL_DETAIL):
    from services.web.polymarket.sector_brief import named_events
    return named_events([_OIL, _HORMUZ], 120, labels=_LABELS, detail=detail)


def test_every_question_carries_its_own_probability_or_says_it_has_none():
    """유가(independent_multi)는 대표 확률이 없어 입력이 비었고, 모델이 유일한 숫자를 빌려 썼다."""
    oil, hormuz = _composite_rows()
    assert oil["options"] == [{"label": "December 31", "outlook": "낮음"}]
    assert oil["fact"] == "‘Crude Oil all time high by...?’의 열린 선택지별 확률은 December 31 37%다."
    assert hormuz["fact"] == "‘Strait of Hormuz traffic returns to normal by December 31?’에 대해 참여자들은 그 가능성을 20.5%로 본다."
    # 모델에는 읽기 쉬운 한국어 이름을 준다(사실 문장의 주어는 원문 제목).
    assert hormuz["label"] == "호르무즈 해협 통행이 정상화될지"
    unread, _ = _composite_rows(detail=lambda event: None)
    assert unread["probability_available"] is False and unread["fact"] is None


def test_the_observed_misattribution_is_rejected_and_numbers_come_only_from_the_server(tmp_path):
    from services.web.llm import PolymarketBriefError
    rows = _composite_rows()
    opening = "전체적으로 호르무즈 해협과 원유 가격에서 참여자들의 판단이 엇갈린다. "
    observed = opening + "국제유가 사상 최고치 달성 가능성은 20.5%로 낮게 평가받고 있다."
    with pytest.raises(PolymarketBriefError, match="확률 숫자"):
        _analyzer(tmp_path, observed).analyze("복합", {"event_count": 20}, rows)
    clean = opening + "해협 정상화는 낮게 보지만 원유 최고치 경신 여지는 남아 있다고 본다."
    text = _analyzer(tmp_path, clean).analyze("복합", {"event_count": 20}, rows)
    # 숫자는 서버가 질문 이름과 함께, 참여 규모순으로 붙인다.
    assert text == clean + (" ‘Crude Oil all time high by...?’의 열린 선택지별 확률은 December 31 37%다."
                            " ‘Strait of Hormuz traffic returns to normal by December 31?’에 대해 참여자들은 그 가능성을 20.5%로 본다.")


def test_the_model_never_sees_the_server_fact_sentences(tmp_path):
    seen = []

    class Backend:
        def generate(self, **kwargs):
            seen.append(json.loads(kwargs["user_prompt"]))
            return "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 원유 쪽은 연말까지 상승 여지가 남아 있다고 본다."

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
    PolymarketBriefAnalyzer(Backend(), prompt, 900).analyze("복합", {"event_count": 20}, _composite_rows())
    assert all("fact" not in row for row in seen[0]["events"])
    assert [row["label"] for row in seen[0]["events"]] == ["원유 가격이 역사적 최고치를 기록할지", "호르무즈 해협 통행이 정상화될지"]


def test_the_model_input_carries_no_probability_numbers(tmp_path):
    """1차 장치: 모델은 확률 숫자를 받지 않는다. 입력에 없는 숫자는 다른 질문에 옮겨 붙일 수 없다."""
    seen = []

    class Backend:
        def generate(self, **kwargs):
            seen.append(kwargs["user_prompt"])
            return "전체적으로 원유와 해협 질문에서 참여자들의 판단이 엇갈린다. 원유 쪽은 연말까지 상승 여지가 남아 있다고 본다."

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("test", encoding="utf-8")
    from services.web.llm.polymarket_brief import PolymarketBriefAnalyzer
    totals = {"event_count": 20, "volume24hr": 79737.0, "probability": {"median": 0.37, "strong": 2, "tight": 3}}
    PolymarketBriefAnalyzer(Backend(), prompt, 900).analyze("복합", totals, _composite_rows())

    def floats(value):
        if isinstance(value, float):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from floats(item)
        elif isinstance(value, list):
            for item in value:
                yield from floats(item)

    sent = json.loads(seen[0])
    assert [value for value in floats(sent) if 0 <= value <= 1] == []  # 참여 규모(달러)만 남는다
    assert sent["totals"]["probability"] == {"median_outlook": "낮음", "strong": 2, "tight": 3}
    oil, hormuz = sent["events"]
    assert oil["options"] == [{"label": "December 31", "outlook": "낮음"}]
    assert hormuz["title_outlook"] == "낮음"
    assert not any(token in seen[0] for token in ("0.205", "0.795", "0.37", "20.5%", "37%"))


@pytest.mark.parametrize(("value", "band"), [
    (0.0, "매우 낮음"), (0.199, "매우 낮음"), (0.2, "낮음"), (0.4, "엇갈림"), (0.6, "엇갈림"),
    (0.61, "높음"), (0.8, "높음"), (0.81, "매우 높음"), (1.0, "매우 높음"),
])
def test_outlook_bands(value, band):
    from services.web.llm.polymarket_brief import outlook
    assert outlook(value) == band


@pytest.mark.parametrize("written", ["20.5%", "20.5％", "20.5 퍼센트", "21프로", "확률 0.205", "20.50%", "100%"])
def test_any_model_written_probability_is_rejected(written):
    from services.web.llm.polymarket_brief import ProbabilityWritten, attach_facts
    with pytest.raises(ProbabilityWritten, match="확률 숫자"):
        attach_facts(f"전체적으로 판단이 엇갈린다. 가능성은 {written} 수준이다.", ["사실."])


@pytest.mark.parametrize("text", ["2026년 원유 가격", "10년물 국채", "3개 질문", "프로그램 매매", "1.5배"])
def test_ordinary_numbers_are_not_probabilities(text):
    from services.web.llm.polymarket_brief import attach_facts
    assert attach_facts(f"전체적으로 {text}에서 판단이 엇갈린다.", []) == f"전체적으로 {text}에서 판단이 엇갈린다."


def test_at_most_three_facts_are_attached_in_order():
    from services.web.llm.polymarket_brief import attach_facts
    assert attach_facts("단락이다.", ["가.", None, "나.", "다.", "라."]) == "단락이다. 가. 나. 다."


@pytest.mark.parametrize(("value", "text"), [(0.205, "20.5%"), (0.37, "37%"), (0.0, "0%"), (1.0, "100%"),
                                             (0.12345, "12.3%"), (0.12355, "12.4%")])
def test_percent_rendering(value, text):
    from services.web.polymarket.sector_brief import _percent
    assert _percent(value) == text


def test_named_two_choice_binary_uses_the_leading_choice():
    from services.web.polymarket.sector_brief import named_events
    event = {"id": "7", "title": "Bitcoin Up or Down?", "event_type": "binary", "leader": "Up",
             "leader_probability": 0.6, "outcome_labels": ["Up", "Down"], "volume24hr": 10}
    row = named_events([event], 10, labels={"7": "비트코인이 오를지 내릴지"})[0]
    # 지금까지는 binary면 title_probability(None)만 보내 이 질문도 빈칸이었다.
    assert "title_outlook" not in row and row["leader"] == "Up" and row["leader_outlook"] == "엇갈림"
    # 흔한 선택지는 방향이 바뀌지 않게 한국어로 옮긴다.
    assert row["fact"] == "‘Bitcoin Up or Down?’에서는 상승 쪽이 60%로 가장 앞선다."


def test_a_failed_group_does_not_inherit_an_old_paragraph_with_model_written_numbers(tmp_path):
    """옛 형식 단락에는 모델이 숫자를 직접 쓴 글(관측 오류 포함)이 있다. 숫자가 든 것은 이어받지 않는다."""
    import services.web.polymarket.sector_brief as sb
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)] + [_event(100 + i, ["inflation"]) for i in range(12)])
    target = tmp_path / "brief.json"
    target.write_text(json.dumps({"groups": [{"key": "equities", "paragraph": "옛 단락 20.5%."}]}), encoding="utf-8")

    class Mixed:
        def analyze(self, label, totals, events):
            if label == "주식·시장":
                raise sb.PolymarketBriefError("boom")
            return "전체적으로 판단이 엇갈린다."

    result = build(root=root, target=target, analyzer=Mixed(), min_events=1, quiet_hours=set(),
                   search_index=tmp_path / "none.json")
    equities = next(g for g in result["groups"] if g["key"] == "equities")
    # 옛 단락 대신 서버가 쓴 사실 문장만 둔다.
    assert equities["status"] == "failed" and equities.get("facts_only") is True
    assert "옛 단락" not in equities["paragraph"] and "20.5%" not in equities["paragraph"]
    # 같은 형식의 직전 단락은 이어받는다.
    target.write_text(json.dumps({"groups": [{"key": "equities", "paragraph": "새 형식 단락.",
                                              "paragraph_format": sb.PARAGRAPH_FORMAT}]}), encoding="utf-8")
    result = build(root=root, target=target, analyzer=Mixed(), min_events=1, quiet_hours=set(),
                   search_index=tmp_path / "none.json")
    equities = next(g for g in result["groups"] if g["key"] == "equities")
    assert equities["paragraph"] == "새 형식 단락." and equities["stale"] is True
    # 옛 형식은 숫자가 없어도 이어받지 않는다 — 형식 번호가 같은 것만(사실 문장만 둔다).
    target.write_text(json.dumps({"groups": [{"key": "equities", "paragraph": "전체적으로 판단이 엇갈린다."}]}),
                      encoding="utf-8")
    result = build(root=root, target=target, analyzer=Mixed(), min_events=1, quiet_hours=set(),
                   search_index=tmp_path / "none.json")
    equities = next(g for g in result["groups"] if g["key"] == "equities")
    assert equities.get("facts_only") is True and "stale" not in equities


@pytest.mark.parametrize("written", ["20.5프로다", "20.5프로로 낮다", "확률 0,205"])
def test_korean_endings_and_comma_decimals_are_still_probabilities(written):
    """검수에서 찾은 우회: 조사·어미가 붙은 "프로", 쉼표 소수."""
    from services.web.llm.polymarket_brief import ProbabilityWritten, attach_facts, has_model_probability
    assert has_model_probability(written)
    with pytest.raises(ProbabilityWritten):
        attach_facts(f"전체적으로 판단이 엇갈린다. 가능성은 {written}.", [])


@pytest.mark.parametrize("text", ["2.5배", "3개 프로그램", "프로젝트 2건", "1.8조 달러"])
def test_multipliers_and_words_are_not_probabilities(text):
    from services.web.llm.polymarket_brief import has_model_probability
    assert not has_model_probability(text)


def test_label_is_used_only_when_it_matches_the_current_title_and_its_numbers():
    from services.web.polymarket.annotate import title_hash
    from services.web.polymarket.sector_brief import _label
    fed = {"id": "1", "title": "Fed rate cut by...?"}
    # 요약이 원문에 없는 날짜를 지어냈다(2026-10-03 실측) → 원문 제목.
    assert _label(fed, {"1": {"summary": "연준이 2025년 12월 금리를 인하할지", "h": title_hash(fed["title"])}}) == fed["title"]
    hormuz = {"id": "2", "title": "Strait of Hormuz traffic returns to normal by December 31?"}
    good = {"summary": "호르무즈 해협 통행이 12월 31일까지 정상화될지", "h": title_hash(hormuz["title"])}
    assert _label(hormuz, {"2": good}) == good["summary"]   # 영문 달 이름은 숫자로 친다
    assert _label(hormuz, {"2": {**good, "h": "stale"}}) == hormuz["title"]   # 제목이 바뀐 뒤의 옛 주석


def test_option_ordering_ties_limit_and_blank_labels():
    from services.web.polymarket.sector_brief import _open_options
    detail = {"markets": [
        {"outcome_label": "B", "closed": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.4},
        {"outcome_label": "A", "closed": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.4},
        {"outcome_label": "C", "closed": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.9},
        {"outcome_label": "D", "closed": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.1},
        {"outcome_label": "", "closed": False, "price_valid": True, "yes_label": "Yes", "yes_probability": 0.99},
        {"outcome_label": "E", "closed": False, "price_valid": True, "yes_label": "Over", "yes_probability": 0.95},
    ]}
    options, total = _open_options(detail)
    assert options == [("C", 0.9), ("A", 0.4), ("B", 0.4)] and total == 4


def test_a_broken_detail_read_only_empties_that_question(tmp_path):
    """상세 하나가 깨져도 분야·실행은 계속된다. 그 질문만 확률 없음."""
    root = tmp_path / "polymarket"
    events = [_event(i, ["stocks"]) for i in range(11)]
    events.append({**_event(99, ["stocks"]), "event_type": "independent_multi", "leader": None,
                   "leader_probability": None, "generation_id": "x",
                   "detail_ref": {"shard": "missing.jsonl", "offset": 0, "length": 10, "sha256": "0"}})
    _write_current(root, events)
    seen = []

    class Recording:
        def analyze(self, label, totals, rows):
            seen.extend(rows)
            return "전체적으로 판단이 엇갈린다."

    result = build(root=root, target=tmp_path / "brief.json", analyzer=Recording(), min_events=10, quiet_hours=set(),
                   search_index=tmp_path / "none.json")
    assert result is not None
    broken = next(row for row in seen if row["title"] == "event 99")
    assert broken["probability_available"] is False and broken["fact"] is None


def test_a_fact_with_forbidden_copy_is_not_published():
    from services.web.llm.polymarket_brief import attach_facts
    assert attach_facts("단락이다.", ["‘Polymarket 질문’에 대해 참여자들은 그 가능성을 20%로 본다.", "좋은 사실."]) == "단락이다. 좋은 사실."



def test_a_misleading_summary_cannot_become_the_subject_of_a_fact():
    """2차 검수 재현: 호르무즈 event에 유가 요약(해시 일치, 숫자 없음)을 넣으면 "원유 … 20.5%"가 나왔다.
    사실 문장의 주어는 원문 제목이라 요약이 무엇이든 숫자는 제 질문에 붙는다."""
    from services.web.polymarket.annotate import title_hash
    from services.web.polymarket.sector_brief import named_events
    wrong = {"999": {"summary": "원유 가격이 역사적 최고치를 기록할지", "h": title_hash(_HORMUZ["title"])}}
    row = named_events([_HORMUZ], 10, labels=wrong)[0]
    assert row["fact"] == "‘Strait of Hormuz traffic returns to normal by December 31?’에 대해 참여자들은 그 가능성을 20.5%로 본다."
    assert "원유" not in row["fact"]


def test_facts_only_fallback_uses_the_same_publication_checks(tmp_path):
    """2차 검수 재현: 실패 대체 경로가 금지어 검사를 우회해 출처 서비스명이 공개됐다."""
    root = tmp_path / "polymarket"
    events = [{**_event(i, ["stocks"]), "title": f"Will Polymarket grow {i}?"} for i in range(12)]
    _write_current(root, events)
    result = build(root=root, target=tmp_path / "brief.json", analyzer=_Analyzer(fail_all=True), min_events=10,
                   quiet_hours=set(), search_index=tmp_path / "none.json")
    assert "Polymarket" not in (tmp_path / "brief.json").read_text(encoding="utf-8")
    assert result["state"] == "failed"


def test_run_state_and_group_counts_tell_a_total_failure_from_success(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)] + [_event(100 + i, ["inflation"]) for i in range(12)])
    target = tmp_path / "brief.json"
    ok = build(root=root, target=target, analyzer=_Analyzer(), min_events=10, quiet_hours=set(),
               search_index=tmp_path / "none.json")
    assert ok["state"] == "ok" and ok["group_counts"]["ok"] == 2
    first_written = {g["key"]: g["paragraph_written_at"] for g in ok["groups"] if g.get("paragraph")}

    failed = build(root=root, target=target, analyzer=_Analyzer(fail_all=True), min_events=10, quiet_hours=set(),
                   search_index=tmp_path / "none.json")
    assert failed["state"] == "failed" and failed["group_counts"]["stale"] == 2 and failed["group_counts"]["ok"] == 0
    # 이어받은 단락은 원래 쓴 시각을 그대로 갖는다.
    assert {g["key"]: g["paragraph_written_at"] for g in failed["groups"] if g.get("paragraph")} == first_written


def test_a_reused_facts_only_paragraph_keeps_its_marker(tmp_path):
    root = tmp_path / "polymarket"
    _write_current(root, [_event(i, ["stocks"]) for i in range(12)])
    target = tmp_path / "brief.json"
    first = build(root=root, target=target, analyzer=_Analyzer(fail_all=True), min_events=10, quiet_hours=set(),
                  search_index=tmp_path / "none.json")
    assert next(g for g in first["groups"] if g["key"] == "equities")["facts_only"] is True
    again = build(root=root, target=target, analyzer=_Analyzer(fail_all=True), min_events=10, quiet_hours=set(),
                  search_index=tmp_path / "none.json")
    row = next(g for g in again["groups"] if g["key"] == "equities")
    assert row["stale"] is True and row["facts_only"] is True


def test_the_page_says_when_only_probabilities_are_shown():
    from services.web.pages.polymarket import POLYMARKET_HTML
    assert "확률만 표시" in POLYMARKET_HTML


@pytest.mark.parametrize("text", ["2026년", "10년물 국채", "1.2배 상승"])
def test_more_units_are_not_probabilities(text):
    from services.web.llm.polymarket_brief import has_model_probability
    assert not has_model_probability(text)


def test_per_cent_in_english_is_a_probability():
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability("20.5 per cent") and has_model_probability("20.5 percent")


def test_published_length_drops_trailing_facts():
    from services.web.llm.polymarket_brief import MAX_PUBLISHED_CHARS, attach_facts
    paragraph = "가" * (MAX_PUBLISHED_CHARS - 20)
    assert attach_facts(paragraph, ["짧은 사실.", "나" * 50 + "."]) == paragraph + " 짧은 사실."



@pytest.mark.parametrize("written", ["이십점오 퍼센트", "20.5 퍼 센트", "확률은 0.205도 가능하다", "약 %",
                                     "확률은 0.205일 것이다", "이십점오 프로다"])
def test_percent_words_are_rejected_even_without_digits(written):
    """3차 검수의 우회: 숫자를 한글로 쓰거나 낱말을 띄우거나, 조사 "도"가 단위로 읽히는 경우."""
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(written)


def test_facts_only_paragraph_is_also_capped(tmp_path):
    from services.web.llm.polymarket_brief import MAX_PUBLISHED_CHARS
    root = tmp_path / "polymarket"
    _write_current(root, [{**_event(i, ["stocks"]), "title": "T" * 700 + str(i)} for i in range(12)])
    result = build(root=root, target=tmp_path / "brief.json", analyzer=_Analyzer(fail_all=True), min_events=10,
                   quiet_hours=set(), search_index=tmp_path / "none.json")
    row = next(g for g in result["groups"] if g["key"] == "equities")
    assert row["facts_only"] is True and 0 < len(row["paragraph"]) <= MAX_PUBLISHED_CHARS


def test_the_page_shows_when_a_reused_paragraph_was_written():
    from services.web.pages.polymarket import POLYMARKET_HTML
    assert "paragraph_written_at" in POLYMARKET_HTML


@pytest.mark.parametrize(("text", "expected"), [
    ("오프로드 차량 수요", False), ("이프로틴", False), ("이 프로그램 매매", False),
    ("삼 프로 오른다", True), ("20프로대", True), ("21프로", True),
])
def test_pro_counts_only_when_followed_by_a_particle_or_boundary(text, expected):
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(text) is expected



@pytest.mark.parametrize("written", ["20프로였다", "20프로보다 낮다", "20프로임을 확인했다", "0.205배분된다", "이십 프로"])
def test_fifth_review_bypasses_are_rejected(written):
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(written)


@pytest.mark.parametrize("text", [
    "20.5프로덕션", "0.25포인트", "0.5배", "관세율 20%", "10/31 회의 이후", "2026/27 시즌", "5분의 시간",
])
def test_intended_over_rejection_of_non_probability_numbers(text):
    """보수 정책: 확률이 아닌 표현도 반려된다. 모델은 숫자 없이 흐름만 쓴다(교정 1회 → 실패하면 사실 문장만)."""
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(text)



@pytest.mark.parametrize("written", ["삼 프로밖에 안 된다", "삼 프로든 오 프로든", ".205", "영점이공오", "100분의 20"])
def test_sixth_review_bypasses_are_rejected(written):
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(written)


@pytest.mark.parametrize("written", [
    "국제유가 사상 최고치 달성 가능성은 백분의 이십 정도로 낮게 평가된다.", "삼분의 일", "십분의 이", "구십분의 일", "천분의 5",
    "국제유가 사상 최고치 달성 가능성은 100분의 이십 정도로 낮게 평가된다.", "100분의 일", "10분의 삼", "십 분의 이",
    "구분의 일", "1/5", "1 / 5",
    "국제유가 사상 최고치 달성 가능성은 ⅕ 정도로 낮게 평가된다.", "1⁄5", "1∕5",
    "국제유가 사상 최고치 달성 가능성은 5분의 하나 정도로 낮게 평가된다.", "다섯분의 하나",
    "국제유가 사상 최고치 달성 가능성은 공점이공오 정도로 낮게 평가된다.",
    "국제유가 사상 최고치 달성 가능성은 20٪ 정도로 낮게 평가된다.", "제로점 이", "200‰",
])
def test_korean_numeral_fractions_are_rejected(written):
    """7·8차 검수 재현: 한글 수사·혼합 표기 분수도 모델이 쓴 확률이다."""
    from services.web.llm.polymarket_brief import has_model_probability
    assert has_model_probability(written)


@pytest.mark.parametrize("text", [
    "업종 구분의 기준이 흔들린다", "이 분의 판단과 엇갈린다", "대부분의 참여자가 낙관한다",
    "협상 재개 전 다섯 분의 이동 시간이 필요하다는 점에서 일정의 불확실성이 크다.",
    "항공 점검 강화로 공급 회복 시점이 불확실하다.", "협상 쟁점이 프로그램 종료 여부에 달려 있다.",
    "협상의 핵심 쟁점이 프로젝트 승인 여부에 달려 있다.", "운영 점검이 길어진다.",
])
def test_words_ending_in_bun_ui_are_not_fractions(text):
    from services.web.llm.polymarket_brief import has_model_probability
    assert not has_model_probability(text)


def test_the_api_never_serves_a_paragraph_of_another_format(tmp_path, monkeypatch):
    """배포 순간부터, 파일이 새로 써지기 전에도 옛 단락(다른 질문의 숫자가 붙은 글)을 내보내지 않는다."""
    from fastapi.testclient import TestClient
    from services.web import server
    from services.web.core import config
    public = tmp_path / "public"
    (public / "polymarket").mkdir(parents=True)
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    (public / "polymarket" / "sector_brief.json").write_text(json.dumps({
        "generation_id": "g", "groups": [
            {"key": "composite", "status": "ok", "paragraph": "국제유가 사상 최고치 달성 가능성은 20.5%다.", "overview": "x"},
            {"key": "macro", "status": "ok", "paragraph": "새 형식 단락이다.",
             "paragraph_format": config.POLYMARKET_BRIEF_PARAGRAPH_FORMAT},
            {"key": "general", "status": "ok", "paragraph": "실험 형식 2 단락.", "paragraph_format": 2},
        ]}, ensure_ascii=False), encoding="utf-8")
    body = TestClient(server.build_app()).get("/api/forecast/sector-brief").json()
    groups = {g["key"]: g for g in body["groups"]}
    assert "paragraph" not in groups["composite"] and groups["composite"]["status"] == "failed"
    assert "paragraph" not in groups["general"]
    assert groups["macro"]["paragraph"] == "새 형식 단락이다."
    assert "20.5%" not in json.dumps(body, ensure_ascii=False)


def test_sector_brief_etag_changes_when_old_paragraphs_are_filtered(tmp_path, monkeypatch):
    """7차 검수 재현: 같은 generation의 옛 응답 ETag로 조건부 요청하면 304가 나가 옛 단락 캐시가 살아남았다."""
    from fastapi.testclient import TestClient
    from services.web import server
    from services.web.core import config
    from services.web.polymarket.repository import make_etag
    public = tmp_path / "public"
    (public / "polymarket").mkdir(parents=True)
    monkeypatch.setattr(server, "PUBLIC_DIR", public)
    path = public / "polymarket" / "sector_brief.json"

    def write(group):
        path.write_text(json.dumps({"generation_id": "g", "groups": [group]}, ensure_ascii=False), encoding="utf-8")

    write({"key": "composite", "status": "ok", "paragraph": "국제유가 사상 최고치 달성 가능성은 20.5%다."})
    client = TestClient(server.build_app())
    old_etag = make_etag("g", "sector_brief", {})  # 필터 전 서버가 내보내던 ETag
    for method in ("get", "head"):
        response = getattr(client, method)("/api/forecast/sector-brief", headers={"If-None-Match": old_etag})
        assert response.status_code == 200
    first = client.get("/api/forecast/sector-brief")
    assert "20.5%" not in first.text
    assert client.get("/api/forecast/sector-brief", headers={"If-None-Match": first.headers["etag"]}).status_code == 304

    # 같은 generation 안에서 형식 3 단락이 새로 써지면 ETag가 바뀐다.
    write({"key": "composite", "status": "ok", "paragraph": "새 단락.",
           "paragraph_format": config.POLYMARKET_BRIEF_PARAGRAPH_FORMAT})
    second = client.get("/api/forecast/sector-brief", headers={"If-None-Match": first.headers["etag"]})
    assert second.status_code == 200 and "새 단락." in second.text
    assert second.headers["cache-control"] == "no-cache"
