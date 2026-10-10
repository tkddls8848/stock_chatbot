from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from .client import Snapshot
from .speech import (
    CLOSING_LINE, CLOSING_SCREEN, consensus_mood, end_sentence, opening_line, speak_markets, to_polite_text,
    to_spoken_question, transition,
)

_FOLLOWING = ("이번에는", "다음으로", "다음은", "이어서", "마지막으로")


def _placed(opener: str | None, index: int, count: int) -> str | None:
    """원고를 쓴 뒤 이슈가 빠지면 여는 말의 자리가 바뀐다. 자리와 어긋난 여는 말을 고친다.

    첫 장면이 "이번에는 …로 넘어가 보겠습니다"로 열리거나(2026-10-08 시험: 둘 중 하나가 검증에서 빠졌다), 마지막이
    아닌 장면이 "마지막으로"로 열리면 틀린 말이다. 첫 장면의 이음말은 여는 말째 버리고(대체 문장이 없는 자리라 해설로
    바로 간다), 중간 장면의 "마지막으로"는 그 말만 뗀다.
    """
    if not opener:
        return opener
    first = opener.split()[0].rstrip(",")
    if index == 0 and first in _FOLLOWING:
        return None
    if first == "마지막으로" and index < count - 1:
        return opener.split(maxsplit=1)[1] if len(opener.split()) > 1 else None
    return opener


def _joined(text: str) -> str:
    """원고 한 조각을 대사에 넣는다. 쉼표로 끝난 조각("먼저 국제유가부터 보면,")은 마침표 없이 다음 조각에 잇는다."""
    if not text:
        return ""
    return text if text.rstrip().endswith(",") else end_sentence(to_polite_text(text))


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
    # 내레이션에서 확률을 말하기 시작하는 위치(0~1). 0이면 예전 고정 시점을 쓴다.
    options_at: float = 0.0

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
            "scenes": [{key: value for key, value in asdict(scene).items()
                        if key != "background" or value} for scene in self.scenes],
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
# 원고가 배경 묘사(image_scene)를 주지 못했을 때 그릴 장면. 건물 정면(현판에 가짜
# 글자가 그려진다)과 사람(실존 인물 얼굴)을 피한 사물·풍경이다(실측 2026-09-26).
_SCENE_DEFAULTS = {
    "composite": "container ships and port cranes along a harbor at dusk",
    "macro": "a brass balance scale beside stacked gold coins on dark marble, soft window light",
    "equities": "abstract glowing light trails over a night city seen from high above",
    "geopolitics": "a vintage globe on a dark desk beside a ship compass and nautical map shapes",
    "general": "a quiet financial district skyline at dusk seen from across a river",
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
        narration=opening_line(len(issues), production_date),
        bullets=(f"오늘의 질문 · {len(issues)}개",),
        source_note=f"자료 기준 {shown_stamp}",
        evidence=(issues[0]["title"],),
    )]
    moods: set[str] = set()
    opener_word = ""
    for index, (issue, script) in enumerate(zip(issues, scripts, strict=True)):
        if issue["id"] != script["id"]:
            raise ValueError("원고와 이벤트가 일치하지 않습니다")
        options, spoken = [], []
        evidence = [f"이벤트 질문: {issue['title']}", f"이벤트 설명: {issue['description']}"]
        for market, label in zip(issue["markets"], script["market_labels"], strict=True):
            if market["id"] != label["id"]:
                raise ValueError("개별 질문과 확률이 일치하지 않습니다")
            options.append((label["label"], market["yes"], market["yes_probability"]))
            spoken.append((label["label"], market["yes"], market["no"], label.get("outlook")))
            evidence.append(f"시장 {market['id']}: {market['question']} / 예 {market['yes']} / 아니오 {market['no']}")
        deadline = datetime.fromisoformat(issue["end_date"].replace("Z", "+00:00"))
        end_text = deadline.strftime("%Y-%m-%d %H:%M %z")
        volume = _money(issue["volume24hr"])
        keywords = (issue.get("selection") or {}).get("keywords") or []
        evidence.append(f"표시 선택지: 유효 {issue['valid_market_count']}개 중 상위 {len(options)}개")
        evidence.extend((f"이벤트 24시간 참여 규모: {issue['volume24hr']} USD",
                         f"이벤트 종료 예정: {end_text} (개별 판정 시각과 다를 수 있음)"))
        evidence.extend(f"관련 뉴스 제목: {n['title']} / {n['url']}" for n in issue["news"] if n["id"] in script["news_ids"])
        hooked = next((n for n in issue.get("market_news", []) if n["id"] == script.get("hook_news_id")), None)
        if hooked and script.get("news_hook"):
            evidence.append(f"시의 뉴스({hooked['when']} · 같은 흐름 {hooked['coverage']}건): {hooked['title']}"
                            f" / 원문: {hooked['original']} / {hooked['source']} {hooked['url']}".rstrip())
        # 화면은 정확한 수치를, 음성은 그 수치가 뜻하는 바를 맡는다. 확인점
        # (`watch_point`)은 화면에 넣지 않고 검수 기록에만 남긴다 — 장면마다
        # 읽으면 "…확인하세요"가 네댓 번 반복되고, 말하지 않는 당부를 화면에만
        # 띄우면 보는 것과 듣는 것이 어긋난다. 고지문은 마무리에서 한 번이다.
        # 선정 이유(왜 이 이슈인가)를 먼저 말하고, 질문을 던진 뒤 확률로 답한다
        # (운영자 결정 2026-09-27: 예전 순서는 확률 → 이유였다).
        # 장면을 여는 말은 원고가 이슈마다 다르게 쓴다(`lead_in`). 확률 뒤에는 숫자가 어떻게 갈렸는지 한 문장으로
        # 풀어 장면을 닫는다(2026-10-07). 대상을 대명사로 퉁치게 되는 경우는 풀이 없이 끝낸다(`speech.consensus_mood`).
        opener = _placed(script.get("lead_in"), index, len(issues))
        if opener:
            # 원고가 장면마다 같은 이음말로 열면("이번에는 …", "이번에는 …") 둘째부터 그 말을 뗀다.
            words = opener.split()
            if words[0] == opener_word and len(words) > 3:
                opener = " ".join(words[1:])
            opener_word = words[0]
        mood = consensus_mood(issue.get("event_type", ""), spoken)
        # 같은 풀이를 두 장면에서 되풀이하지 않는다.
        mood = "" if mood in moods else mood
        moods.add(mood)
        # 여는 말 다음에 그날 실제 보도 한 문장(`news_hook`)을 넣어, 숫자를 읽기 전에 왜 지금 이 질문인지가
        # 들리게 한다(운영자 요청 2026-10-08). 맞는 기사가 없던 이슈는 예전처럼 해설로 바로 간다.
        lead = " ".join(part for part in (
            # 여는 말이 "먼저 국제유가부터 보면,"처럼 쉼표로 끝나면 마침표를 붙이지 않고 다음 문장으로 잇는다 —
            # 장면마다 짧은 평서문이 마침표로 끊기면 기계가 읽는 것처럼 들렸다(운영자 지적 2026-10-08).
            _joined(opener) if opener else transition(index, issue["sector_label"]),
            _joined(script.get("news_hook") or ""),
            end_sentence(to_polite_text(script["context"])),
            to_spoken_question(script["question"]),
        ) if part)
        markets_line = " ".join(part for part in (
            speak_markets(issue.get("event_type", ""), script["headline"], spoken),
            mood,
        ) if part)
        narration = f"{lead} {markets_line}".strip()
        # 선택지가 화면에 뜨는 때를 확률을 말하기 시작하는 자리에 맞춘다(글자 비율).
        options_at = len(lead) / len(narration) if markets_line else 0.0
        scenes.append(Scene(
            kind="consensus", title=script["headline"], kicker=f"{index + 1:02d} · {issue['sector_label']}",
            body="\n".join(f"{label} — 예 {percent}" for label, percent, _ in options),
            options=tuple(options),
            narration=narration,
            options_at=options_at,
            accent=("gold", "blue", "red")[index % 3],
            # 잔글씨는 화면에 그대로 뜬다. 예전 "종료 예정 2026-10-01 03:59 +0000"은
            # 시각 표기의 절반이 다음 줄로 넘어갔고, `+0000`은 읽는 사람에게 아무
            # 뜻도 되지 못했다. 정확한 시각과 시간대는 근거와 검수 기록에 남는다.
            # 셋째 칸은 이 질문의 주제어다("주제어 · 유가 · 원유", 운영자 요청 2026-10-08) — 무슨 이야기인지 한눈에
            # 보인다. 선정 때 주제어가 없었던 이슈만 예전처럼 표시 선택지 수를 보인다(그 수는 검수 근거에도 있다).
            bullets=(f"24시간 참여 규모 · {volume}",
                     f"종료 예정 · {deadline:%Y-%m-%d} 세계 표준시",
                     f"주제어 · {' · '.join(keywords[:3])}" if keywords
                     else f"표시 선택지 · 유효 {issue['valid_market_count']}개 중 상위 {len(options)}개"),
            # 배경 생성이 그날 이슈를 그리도록 원제를 붙인다. 저장 배경 선택은 앞 낱말만 본다.
            visual_query=(f"{_VISUAL_QUERIES[issue['sector']]}; topic: "
                          f"{script.get('image_scene') or _SCENE_DEFAULTS[issue['sector']]}"),
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
        body=CLOSING_SCREEN,
        narration=CLOSING_LINE,
        source_note=f"자료 기준 {shown_stamp}",
    ))
    return Scenario(production_date.isoformat(), snapshot.generation_id, source_stamp, tuple(scenes),
                    lead_label=issues[0]["sector_label"], lead_volume=_money(issues[0]["volume24hr"]))
