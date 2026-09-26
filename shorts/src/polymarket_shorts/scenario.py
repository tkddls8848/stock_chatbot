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
    # 화면이 그리는 선택지. (이름, 정확한 예 확률 문자열, 그 확률 0~1)이고
    # `body`는 같은 내용을 검수 기록용으로 옮겨 적은 글이다. 화면은 예·아니오
    # 쌍 대신 '예' 확률 하나만 큰 숫자와 막대로 보여 준다 — 이지선다에서
    # 아니오는 나머지라 두 번 적을 값이 아니고, 두 줄이 되면 어느 쪽 숫자를
    # 봐야 하는지 한눈에 들어오지 않는다.
    options: tuple[tuple[str, str, float], ...] = ()
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

    def __post_init__(self) -> None:
        # 제작 원고(`scenario.json`)에서 되읽으면 리스트로 온다. 렌더가 카운트업
        # 프레임마다 조각을 이어 붙이므로 여기서 튜플로 굳힌다.
        object.__setattr__(self, "options", tuple(tuple(row) for row in self.options))


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
        #
        # 도입은 훅이다. 예전에는 "선정한 개별 이슈 2"라는 큰 숫자 카드가 첫 화면을
        # 차지했는데, 2라는 수는 계속 볼 이유가 되지 못한다. 그 자리에 오늘의 첫
        # 질문을 띄워 바로 끌어들이고, 편수는 잔글씨 한 줄로 내린다.
        kind="intro", title=scripts[0]["headline"], kicker=f"오늘의 전망 · {production_date:%m.%d}",
        body=to_spoken_question(scripts[0]["question"]),
        narration=opening_line(scripts[0]["headline"], len(issues)),
        bullets=(f"오늘의 질문 · {len(issues)}개",),
        source_note=f"자료 기준 {shown_stamp}",
        evidence=(issues[0]["title"],),
    )]
    for index, (issue, script) in enumerate(zip(issues, scripts, strict=True)):
        if issue["id"] != script["id"]:
            raise ValueError("원고와 이벤트가 일치하지 않습니다")
        options, spoken = [], []
        evidence = [f"이벤트 질문: {issue['title']}", f"이벤트 설명: {issue['description']}"]
        for market, label in zip(issue["markets"], script["market_labels"], strict=True):
            if market["id"] != label["id"]:
                raise ValueError("개별 질문과 확률이 일치하지 않습니다")
            options.append((label["label"], market["yes"], market["yes_probability"]))
            spoken.append((label["label"], market["yes_probability"]))
            evidence.append(f"시장 {market['id']}: {market['question']} / 예 {market['yes']} / 아니오 {market['no']}")
        deadline = datetime.fromisoformat(issue["end_date"].replace("Z", "+00:00"))
        end_text = deadline.strftime("%Y-%m-%d %H:%M %z")
        volume = _money(issue["volume24hr"])
        evidence.extend((f"이벤트 24시간 참여 규모: {issue['volume24hr']} USD",
                         f"이벤트 종료 예정: {end_text} (개별 판정 시각과 다를 수 있음)"))
        evidence.extend(f"관련 뉴스 제목: {n['title']} / {n['url']}" for n in issue["news"] if n["id"] in script["news_ids"])
        # 화면은 정확한 수치를, 음성은 그 수치가 뜻하는 바를 맡는다. 확인점
        # (`watch_point`)은 화면에 넣지 않고 검수 기록에만 남긴다 — 장면마다
        # 읽으면 "…확인하세요"가 네댓 번 반복되고, 말하지 않는 당부를 화면에만
        # 띄우면 보는 것과 듣는 것이 어긋난다. 고지문은 마무리에서 한 번이다.
        scenes.append(Scene(
            kind="consensus", title=script["headline"], kicker=f"{index + 1:02d} · {issue['sector_label']}",
            body="\n".join(f"{label} — 예 {percent}" for label, percent, _ in options),
            options=tuple(options),
            narration=" ".join(part for part in (
                transition(index),
                to_spoken_question(script["question"]),
                speak_markets(spoken),
                end_sentence(to_polite_text(script["context"])),
            ) if part),
            accent=("gold", "blue", "red")[index % 3],
            # 잔글씨는 화면에 그대로 뜬다. 예전 "종료 예정 2026-10-01 03:59 +0000"은
            # 시각 표기의 절반이 다음 줄로 넘어갔고, `+0000`은 읽는 사람에게 아무
            # 뜻도 되지 못했다. 정확한 시각과 시간대는 근거와 검수 기록에 남는다.
            bullets=(f"24시간 참여 규모 · {volume}",
                     f"종료 예정 · {deadline:%Y-%m-%d} 세계 표준시",
                     f"표시 선택지 · 유효 {issue['valid_market_count']}개 중 상위 {len(options)}개"),
            # 배경 생성이 그날 이슈를 그리도록 원제를 붙인다. 저장 배경 선택은 앞 낱말만 본다.
            visual_query=f"{_VISUAL_QUERIES[issue['sector']]}; topic: {issue['title']}",
            # 대표 수치는 화면이 그리는 첫 선택지와 같은 값에서 나온다 — 검수
            # 기록(`review.md`)·검수 패널·내보내기가 이 셋을 읽는다.
            metric=options[0][1], metric_label=options[0][0], probability=options[0][2],
            takeaway=script["watch_point"], source_note=f"자료 기준 {shown_stamp}",
            evidence=tuple(evidence), selection_note=issue["selection"]["reason"],
            event_id=issue["id"],
            market_ids=tuple(m["id"] for m in issue["markets"]),
        ))
    # 마무리는 짧은 고지 한 줄이다. "조건"이라는 큰 글자 카드는 자리만 차지하고
    # 아무것도 알려 주지 않았다. 화면 문구는 마무리 멘트와 같은 말을 한다.
    scenes.append(Scene(
        kind="outro", title="확률은 예측입니다", kicker="마무리",
        body="질문마다 조건이 다릅니다.\n판정 규칙은 직접 확인하세요.",
        narration=CLOSING_LINE,
        source_note=f"자료 기준 {shown_stamp}",
    ))
    return Scenario(production_date.isoformat(), snapshot.generation_id, source_stamp, tuple(scenes),
                    lead_label=issues[0]["sector_label"], lead_volume=_money(issues[0]["volume24hr"]))
