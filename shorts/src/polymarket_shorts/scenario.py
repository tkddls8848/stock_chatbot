from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import re
from typing import Any

from .client import Snapshot

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


_HANGUL_BASE = 0xAC00
_JONGSEONG = 28
_JONG_N = 4   # ㄴ
_JONG_B = 17  # ㅂ


def _with_jongseong(syllable: str, jong: int) -> str:
    index = ord(syllable) - _HANGUL_BASE
    return chr(_HANGUL_BASE + (index // _JONGSEONG) * _JONGSEONG + jong)


def _is_hangul(ch: str) -> bool:
    return _HANGUL_BASE <= ord(ch) <= 0xD7A3


def to_polite(sentence: str) -> str:
    """해라체 평서문 하나를 합쇼체로 옮긴다. 모르는 어미는 그대로 둔다.

    `있다 -> 있습니다`, `어렵다 -> 어렵습니다`, `나타났다 -> 나타났습니다`,
    `보인다 -> 보입니다`, `예상된다 -> 예상됩니다`, `-이다 -> -입니다`.
    """
    stripped = sentence.rstrip()
    trailing = sentence[len(stripped):]
    # 어미 뒤의 마침표·물음표를 떼어 내고 본다. 붙은 채로 검사하면
    # "…어렵다."가 "다"로 끝나지 않아 한 문장도 바뀌지 않는다.
    body = stripped.rstrip(".!?…")
    tail = stripped[len(body):] + trailing
    if not body.endswith("다") or len(body) < 2:
        return sentence
    if body.endswith("니다"):
        # 이미 합쇼체다. 한 번 더 돌리면 "입니다"가 "입닙니다"가 된다.
        return sentence
    stem, prev = body[:-2], body[-2]
    if not _is_hangul(prev):
        return sentence
    if prev == "이":
        return f"{stem}입니다{tail}"
    jong = (ord(prev) - _HANGUL_BASE) % _JONGSEONG
    if jong == _JONG_N:               # 된다 · 보인다 -> 됩니다 · 보입니다
        return f"{stem}{_with_jongseong(prev, _JONG_B)}니다{tail}"
    if jong:                          # 있다 · 어렵다 · 났다 -> 습니다
        return f"{stem}{prev}습니다{tail}"
    return f"{stem}{_with_jongseong(prev, _JONG_B)}니다{tail}"


def end_sentence(text: str) -> str:
    """문장을 마침표로 닫는다. 이미 구두점으로 끝났으면 그대로 둔다.

    근거와 체크포인트 사이에 마침표가 없으면 TTS가 한 문장으로 읽어
    "그쳤습니다 공급망과"처럼 들린다.
    """
    body = text.rstrip()
    return body if body.endswith((".", "!", "?", "…")) else f"{body}."


def to_polite_text(text: str) -> str:
    """문장 단위로 나눠 각각 합쇼체로 옮긴다."""
    parts = re.split(r"(?<=[.!?])(\s+)", text)
    return "".join(
        part if index % 2 else to_polite(part) for index, part in enumerate(parts)
    )


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
        kind="intro", title=scripts[0]["headline"], kicker=f"BETTING ISSUES / {production_date:%m.%d}",
        body=f"거래가 활발한 시장 이슈 {len(issues)}개",
        narration=f"{scripts[0]['headline']}. 예측시장에서 거래가 활발한 이슈 {len(issues)}개를 살펴보겠습니다.",
        metric=str(len(issues)), metric_label="선정한 개별 이슈",
        takeaway="질문과 조건을 함께 읽습니다", source_note=f"자료 기준 {shown_stamp}",
        evidence=(issues[0]["title"],),
    )]
    for index, (issue, script) in enumerate(zip(issues, scripts, strict=True)):
        if issue["id"] != script["id"]:
            raise ValueError("원고와 이벤트가 일치하지 않습니다")
        bets, evidence = [], [f"이벤트 질문: {issue['title']}", f"이벤트 설명: {issue['description']}"]
        for market, label in zip(issue["markets"], script["market_labels"], strict=True):
            if market["id"] != label["id"]:
                raise ValueError("개별 질문과 확률이 일치하지 않습니다")
            bets.append(f"{label['label']}: 예 {market['yes']}, 아니오 {market['no']}")
            evidence.append(f"시장 {market['id']}: {market['question']} / 예 {market['yes']} / 아니오 {market['no']}")
        deadline = datetime.fromisoformat(issue["end_date"].replace("Z", "+00:00"))
        end_text = deadline.strftime("%Y-%m-%d %H:%M %z")
        volume = _money(issue["volume24hr"])
        evidence.extend((f"이벤트 24시간 거래량: {issue['volume24hr']} USD",
                         f"이벤트 종료 예정: {end_text} (개별 판정 시각과 다를 수 있음)",
                         f"원문: {issue['source_url']}"))
        evidence.extend(f"관련 뉴스 제목: {n['title']} / {n['url']}" for n in issue["news"] if n["id"] in script["news_ids"])
        status = ". ".join(bets) + "."
        scenes.append(Scene(
            kind="consensus", title=script["headline"], kicker=f"{index + 1:02d} · {issue['sector_label']}",
            body="\n".join(bets),
            narration=(f"{end_sentence(script['question'])} {status} "
                       f"{end_sentence(to_polite_text(script['context']))} "
                       f"{end_sentence(to_polite_text(script['watch_point']))}"),
            accent=("gold", "blue", "red")[index % 3],
            bullets=(f"24시간 거래량 · {volume}", f"종료 예정 · {end_text}",
                     f"표시 시장 · 유효 {issue['valid_market_count']}개 중 거래량 상위 {len(bets)}개"),
            visual_query=_VISUAL_QUERIES[issue["sector"]],
            metric=issue["markets"][0]["yes"], metric_label=script["market_labels"][0]["label"],
            probability=issue["markets"][0]["yes_probability"],
            takeaway=script["watch_point"], source_note=f"자료 기준 {shown_stamp}",
            evidence=tuple(evidence), selection_note=issue["selection"]["reason"],
            source_url=issue["source_url"], event_id=issue["id"],
            market_ids=tuple(m["id"] for m in issue["markets"]),
        ))
    scenes.append(Scene(
        kind="outro", title="가격은 예측입니다", kicker="CHECK THE CONDITIONS",
        body="각 질문의 조건과 판정 규칙을 확인하세요.",
        narration="표시한 값은 예측시장 가격입니다. 질문별 조건과 판정 규칙을 확인하세요. 투자 조언은 아닙니다.",
        metric="조건", metric_label="확률과 함께 확인", takeaway="거래량은 참여자 수가 아닙니다",
        source_note=f"자료 기준 {shown_stamp}",
    ))
    return Scenario(production_date.isoformat(), snapshot.generation_id, source_stamp, tuple(scenes),
                    lead_label=issues[0]["sector_label"], lead_volume=_money(issues[0]["volume24hr"]))
