from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import re
from typing import Any

from .client import Snapshot


_GROUP_PRIORITY = {"composite": 0, "macro": 1, "equities": 2, "geopolitics": 3, "general": 4}


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


@dataclass(frozen=True)
class Scenario:
    date: str
    generation_id: str
    source_written_at: str
    scenes: tuple[Scene, ...]

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
        }


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s*", text.strip()) if part.strip()]


def clip_at_sentence(text: str, limit: int) -> str:
    chosen: list[str] = []
    for sentence in _sentences(text):
        candidate = " ".join([*chosen, sentence])
        if len(candidate) > limit:
            used = len(" ".join(chosen))
            remaining = limit - used - (1 if chosen else 0)
            if remaining >= 24:
                chosen.append(sentence[: remaining - 1].rstrip() + "…")
            break
        chosen.append(sentence)
    if chosen:
        return " ".join(chosen)
    text = text.strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip() + "…"


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


def _eligible_groups(brief: dict[str, Any], maximum: int) -> list[dict[str, Any]]:
    groups = [
        group for group in brief.get("groups", [])
        if isinstance(group, dict)
        and group.get("status") == "ok"
        and not group.get("stale")
        and str(group.get("paragraph") or "").strip()
    ]
    groups.sort(
        key=lambda group: (
            _GROUP_PRIORITY.get(str(group.get("key")), 99),
            -float(group.get("volume24hr") or 0),
        )
    )
    if len(groups) > maximum:
        must = [group for group in groups if group.get("key") == "composite"][:1]
        remainder = sorted(
            [group for group in groups if group not in must],
            key=lambda group: float(group.get("volume24hr") or 0),
            reverse=True,
        )
        groups = [*must, *remainder][:maximum]
    return groups


_WATCH_POINTS = {
    "composite": "공급망과 원가 전망으로 번지는지 확인하십시오",
    "macro": "금리 기대가 자금조달 비용을 바꾸는지 확인하십시오",
    "equities": "거래량이 실제 위험선호 확대로 이어지는지 확인하십시오",
    "geopolitics": "정책과 분쟁 확률이 운영 리스크로 전이되는지 확인하십시오",
    "general": "시장 기대가 수요와 투자 계획을 바꾸는지 확인하십시오",
}

_VISUAL_QUERIES = {
    "composite": "global cargo shipping containers trade",
    "macro": "central bank finance building",
    "equities": "stock exchange trading floor",
    "geopolitics": "United Nations Security Council meeting",
    "general": "business financial district skyline",
}


def _state_line(group: dict[str, Any]) -> str:
    probability = group.get("probability") or {}
    strong = int(probability.get("strong") or 0)
    tight = int(probability.get("tight") or 0)
    count = max(1, int(group.get("event_count") or 0))
    if tight >= max(2, strong):
        return "판단이 갈려 방향성은 아직 열려 있습니다"
    if strong >= max(3, round(count * 0.2)):
        return "참여자 기대가 한쪽으로 뚜렷하게 모였습니다"
    return "전체 합의보다 일부 핵심 이슈에 베팅이 집중됐습니다"


def _specific_evidence(paragraph: str, limit: int) -> str:
    sentences = _sentences(paragraph)
    specific = [sentence for sentence in sentences if "%" in sentence]
    candidates = specific or sentences
    if not candidates:
        return "구체적인 우세 베팅은 추가 확인이 필요합니다"
    # 숫자가 담긴 문장을 우선하고, 두 문장이 예산 안에 들어오면 함께 쓴다.
    return clip_at_sentence(" ".join(candidates[:2]), limit).rstrip(".")


def build_scenario(
    snapshot: Snapshot,
    *,
    production_date: date,
    target_chars: int = 760,
    max_groups: int = 3,
) -> Scenario:
    groups = _eligible_groups(snapshot.brief, max_groups)
    if not groups:
        raise ValueError("영상에 사용할 최신 컨센서스 단락이 없습니다")

    accounting = snapshot.summary.get("accounting") or {}
    event_count = int(accounting.get("open_event_count") or 0)
    intro = (
        f"오늘의 2분 의사결정 브리핑입니다. 열린 이벤트 {event_count:,}개에서 "
        "경영진이 볼 신호 세 가지만 추렸습니다. 결론, 근거, 체크포인트 순서로 보겠습니다."
    )
    outro = (
        "오늘의 결론은 방향보다 변화 감시입니다. 숫자가 움직일 때 전략 가정을 다시 점검하십시오. "
        "확률은 베팅 가격이 암시하는 값이며 사실 확정이나 투자 조언이 아닙니다."
    )
    fixed = len(intro) + len(outro) + sum(85 + len(str(g.get("label") or "")) for g in groups)
    evidence_budget = max(75, (target_chars - fixed) // len(groups))

    scenes: list[Scene] = [
        Scene(
            kind="intro",
            title="오늘의 예측시장 컨센서스",
            kicker=f"EXECUTIVE BRIEF · {production_date:%Y.%m.%d}",
            body="결론부터\n숫자로 확인하고\n실행 포인트만 남깁니다",
            narration=intro,
            bullets=("2분", "핵심 신호 3개", f"표본 {event_count:,} EVENT"),
            visual_query="executive business presentation skyline",
        )
    ]
    accents = ("red", "blue", "gold")
    for index, group in enumerate(groups):
        key = str(group.get("key") or "")
        label = str(group.get("label") or "시장")
        count = int(group.get("event_count") or 0)
        volume = _money(group.get("volume24hr"))
        probability = group.get("probability") or {}
        strong = int(probability.get("strong") or 0)
        tight = int(probability.get("tight") or 0)
        state = _state_line(group)
        evidence = _specific_evidence(str(group["paragraph"]), evidence_budget)
        watch = _WATCH_POINTS.get(key, "핵심 확률의 방향 전환을 확인하십시오")
        scenes.append(
            Scene(
                kind="consensus",
                title=f"SIGNAL {index + 1} · {label}",
                kicker="EXECUTIVE TAKEAWAY",
                body=f"{state}\n{evidence}\n{watch}",
                narration=(
                    f"{index + 1}번 신호, {label}. 결론부터 말하면 {state}. "
                    f"표본 {count}개 중 강한 합의는 {strong}개, 경합은 {tight}개입니다. "
                    f"구체적인 근거는 {evidence}. 경영진은 {watch}"
                ),
                accent=accents[index % len(accents)],
                bullets=(
                    f"판단 · {state}",
                    f"근거 · {count:,} EVENT / 24H {volume}",
                    f"분포 · 강한 합의 {strong} / 경합 {tight}",
                    f"체크 · {watch}",
                ),
                visual_query=_VISUAL_QUERIES.get(key, "business strategy meeting"),
            )
        )
    scenes.append(
        Scene(
            kind="outro",
            title="결론은 변화 감시입니다",
            kicker="CEO CHECKLIST",
            body="전략 가정\n자금조달 비용\n공급망 리스크",
            narration=outro,
            accent="blue",
            bullets=("확률의 방향 전환", "사업 가정에 미치는 영향", "내일 같은 기준으로 재점검"),
            visual_query="executive boardroom city view",
        )
    )
    return Scenario(
        date=production_date.isoformat(),
        generation_id=snapshot.generation_id,
        source_written_at=str(snapshot.brief.get("written_at") or ""),
        scenes=tuple(scenes),
    )
