from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from .client import Snapshot
from .speech import (
    CLOSING_LINE, end_sentence, opening_line, speak_markets, to_polite_text,
    to_spoken_question, transition,
)

@dataclass(frozen=True)
class Scene:
    kind: str
    title: str
    kicker: str
    body: str
    narration: str
    accent: str = "gold"
    bullets: tuple[str, ...] = ()
    visual_query: str = "business strategy presentation"
    metric: str = ""
    metric_label: str = ""
    takeaway: str = ""
    volume_share: float = 0.0
    source_note: str = ""
    evidence: tuple[str, ...] = ()
    selection_note: str = ""
    probability: float | None = None
    source_url: str = ""
    event_id: str = ""
    market_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    date: str
    generation_id: str
    source_written_at: str
    scenes: tuple[Scene, ...]
    # 제목·설명이 쓰는 "가장 큰 사실". 여기서 한 번 정해 두면
    # metadata_for가 데이터를 다시 해석하지 않는다.
    lead_label: str = ""
    lead_volume: str = ""

    @property
    def narration(self) -> str:
        return "\n".join(scene.narration for scene in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "generation_id": self.generation_id,
            "source_written_at": self.source_written_at,
            "scenes": [asdict(scene) for scene in self.scenes],
            "narration": self.narration,
            "lead_label": self.lead_label,
            "lead_volume": self.lead_volume,
        }


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "집계 중"
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.1f}B달러"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M달러"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K달러"
    return f"{number:,.0f}달러"


_VISUAL_QUERIES = {
    "composite": "global cargo shipping containers trade",
    "macro": "central bank finance building",
    "equities": "stock exchange trading floor",
    "geopolitics": "United Nations Security Council meeting",
    "general": "business financial district skyline",
}


def build_scenario(
    snapshot: Snapshot, issues: list[dict], scripts: list[dict], *, production_date: date,
) -> Scenario:
    """베팅 질문과 확률을 ID로 결합한다. 분야 합계나 홈페이지 문단을 사용하지 않는다."""
    if not issues or len(issues) != len(scripts):
        raise ValueError("검증된 개별 이슈 원고가 필요합니다")
    source_stamp = snapshot.summary["generated_at"]
    shown_stamp = datetime.fromisoformat(source_stamp).strftime("%m/%d %H:%M %z")
    scenes = [Scene(
        # 화면 문구도 한국어다. 예전 영문 kicker "BETTING ISSUES"는 화면에 베팅이라는
        # 말을 그대로 띄우고 있었다 — 쓰지 않기로 한 말이라 남길 이유가 없다.
        kind="intro", title=scripts[0]["headline"], kicker=f"오늘의 전망 · {production_date:%m.%d}",
        body=f"참여가 활발한 예측 이슈 {len(issues)}개",
        narration=opening_line(scripts[0]["headline"], len(issues)),
        metric=str(len(issues)), metric_label="선정한 개별 이슈",
        takeaway="질문과 조건을 함께 읽습니다", source_note=f"자료 기준 {shown_stamp}",
        evidence=(issues[0]["title"],),
    )]
    for index, (issue, script) in enumerate(zip(issues, scripts, strict=True)):
        if issue["id"] != script["id"]:
            raise ValueError("원고와 이벤트가 일치하지 않습니다")
        bets, spoken = [], []
        evidence = [f"이벤트 질문: {issue['title']}", f"이벤트 설명: {issue['description']}"]
        for market, label in zip(issue["markets"], script["market_labels"], strict=True):
            if market["id"] != label["id"]:
                raise ValueError("개별 질문과 확률이 일치하지 않습니다")
            bets.append(f"{label['label']}: 예 {market['yes']}, 아니오 {market['no']}")
            spoken.append((label["label"], market["yes_probability"]))
            evidence.append(f"시장 {market['id']}: {market['question']} / 예 {market['yes']} / 아니오 {market['no']}")
        deadline = datetime.fromisoformat(issue["end_date"].replace("Z", "+00:00"))
        end_text = deadline.strftime("%Y-%m-%d %H:%M %z")
        volume = _money(issue["volume24hr"])
        evidence.extend((f"이벤트 24시간 참여 규모: {issue['volume24hr']} USD",
                         f"이벤트 종료 예정: {end_text} (개별 판정 시각과 다를 수 있음)"))
        evidence.extend(f"관련 뉴스 제목: {n['title']} / {n['url']}" for n in issue["news"] if n["id"] in script["news_ids"])
        # 화면은 정확한 수치를, 음성은 그 수치가 뜻하는 바를 맡는다. 확인점
        # (`watch_point`)은 화면의 체크포인트로만 남긴다 — 장면마다 읽으면
        # "…확인하세요"가 네댓 번 반복된다. 고지문은 마무리에서 한 번이다.
        scenes.append(Scene(
            kind="consensus", title=script["headline"], kicker=f"{index + 1:02d} · {issue['sector_label']}",
            body="\n".join(bets),
            narration=" ".join(part for part in (
                transition(index),
                to_spoken_question(script["question"]),
                speak_markets(spoken),
                end_sentence(to_polite_text(script["context"])),
            ) if part),
            accent=("gold", "blue", "red")[index % 3],
            bullets=(f"24시간 참여 규모 · {volume}", f"종료 예정 · {end_text}",
                     f"표시 선택지 · 유효 {issue['valid_market_count']}개 중 참여 규모 상위 {len(bets)}개"),
            visual_query=_VISUAL_QUERIES[issue["sector"]],
            metric=issue["markets"][0]["yes"], metric_label=script["market_labels"][0]["label"],
            probability=issue["markets"][0]["yes_probability"],
            takeaway=script["watch_point"], source_note=f"자료 기준 {shown_stamp}",
            evidence=tuple(evidence), selection_note=issue["selection"]["reason"],
            event_id=issue["id"],
            market_ids=tuple(m["id"] for m in issue["markets"]),
        ))
    scenes.append(Scene(
        kind="outro", title="확률은 예측입니다", kicker="조건부터 확인",
        body="각 질문의 조건과 판정 규칙을 확인하세요.",
        narration=CLOSING_LINE,
        metric="조건", metric_label="확률과 함께 확인", takeaway="참여 규모는 참여자 수가 아닙니다",
        source_note=f"자료 기준 {shown_stamp}",
    ))
    return Scenario(production_date.isoformat(), snapshot.generation_id, source_stamp, tuple(scenes),
                    lead_label=issues[0]["sector_label"], lead_volume=_money(issues[0]["volume24hr"]))
