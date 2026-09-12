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


def _clip_at_clause(text: str, limit: int) -> str:
    """글자 수로 자르되 쉼표·띄어쓰기 경계까지 물러선다.

    그냥 잘라 내면 "우세한 방향이 나…"처럼 단어 한가운데가 끊긴다.
    """
    head = text[: max(1, limit - 1)].rstrip()
    for boundary in (", ", " "):
        cut = head.rfind(boundary)
        if cut >= limit // 2:
            return head[:cut].rstrip(" ,") + "…"
    return head + "…"


def clip_at_sentence(text: str, limit: int) -> str:
    chosen: list[str] = []
    for sentence in _sentences(text):
        candidate = " ".join([*chosen, sentence])
        if len(candidate) > limit:
            used = len(" ".join(chosen))
            remaining = limit - used - (1 if chosen else 0)
            if remaining >= 24:
                chosen.append(_clip_at_clause(sentence, remaining))
            break
        chosen.append(sentence)
    if chosen:
        return " ".join(chosen)
    text = text.strip()
    return text if len(text) <= limit else _clip_at_clause(text, limit)


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


def _sector_cards(brief: dict[str, Any], maximum: int) -> list[dict[str, Any]]:
    """웹의 `?sort=volume24hr` 화면과 같은 순서로 분야 카드를 만든다.

    다섯 분야를 모두 싣는다. 예전에는 `status == "ok"`인 셋만 골랐는데, 그러면
    요약 생성이 실패한 분야가 화면에서 통째로 사라져 "오늘은 네 분야뿐인가"로
    읽힌다. 건수와 거래량은 요약과 무관하게 늘 있으므로 카드는 그대로 세우고
    요약만 비운다.
    """
    cards = [
        group
        for group in brief.get("groups", [])
        if isinstance(group, dict) and str(group.get("label") or "").strip()
    ]
    cards.sort(key=lambda group: -float(group.get("volume24hr") or 0))
    return cards[:maximum]


def _card_summary(group: dict[str, Any], limit: int) -> str:
    """카드에 실을 '배팅 내용 정리' 한 토막.

    `overview`는 웹이 뽑아 둔 첫 문장(정성 요약)이다. 없으면 단락 첫 문장을
    쓰고, 그것도 없으면 요약이 준비되지 않았다고 밝힌다 — 지어내지 않는다.
    """
    text = str(group.get("overview") or "").strip()
    if not text:
        sentences = _sentences(str(group.get("paragraph") or ""))
        text = sentences[0] if sentences else ""
    if not text:
        return "요약을 준비하지 못했습니다."
    return _end_sentence(to_polite_text(clip_at_sentence(text, limit)))


# ── 어조 ────────────────────────────────────────────────
# 웹 단락은 **해라체**다(`shared/prompts/polymarket_brief_ko.txt`가 그렇게 지시한다).
# 읽는 화면에는 맞지만 영상은 말로 읽으므로 합쇼체여야 한다. 그대로 끼워 넣으면
# "…5%로 나타났다. 경영진은 확인하십시오" 처럼 한 문장 안에서 어조가 뒤섞인다.
# 매체가 다르니 웹 프롬프트를 바꾸지 않고 여기서 옮긴다.
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


def _end_sentence(text: str) -> str:
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
    max_groups: int = 5,
) -> Scenario:
    """다섯 분야를 카드 한 장씩으로 만든다.

    각 카드는 웹의 컨센서스 화면이 보여 주는 것과 같은 셋을 싣는다 —
    열린 이벤트 건수, 24시간 거래량, 그리고 그 분야의 배팅 내용 요약.
    """
    groups = _sector_cards(snapshot.brief, max_groups)
    if not groups:
        raise ValueError("영상에 사용할 분야 카드가 없습니다")

    accounting = snapshot.summary.get("accounting") or {}
    event_count = int(accounting.get("open_event_count") or 0)
    total_volume = _money(sum(float(g.get("volume24hr") or 0) for g in groups))
    intro = (
        f"오늘의 예측시장 브리핑입니다. 열린 이벤트 {event_count:,}개, "
        f"24시간 거래량 {total_volume}입니다. 분야 {len(groups)}곳을 차례로 보겠습니다."
    )
    outro = (
        "숫자가 움직이면 판단도 바뀝니다. 확률은 베팅 가격이 암시하는 값이며 "
        "사실 확정이나 투자 조언이 아닙니다."
    )
    # 카드 한 장의 고정 문구(분야명·건수·거래량·합의 분포)가 대략 60자다.
    fixed = len(intro) + len(outro) + sum(60 + len(str(g.get("label") or "")) for g in groups)
    summary_budget = max(55, (target_chars - fixed) // len(groups))

    scenes: list[Scene] = [
        Scene(
            kind="intro",
            title="오늘의 예측시장 컨센서스",
            kicker=f"TODAY'S BRIEF · {production_date:%Y.%m.%d}",
            body="분야별로\n건수와 거래량\n그리고 무엇에 걸고 있는지",
            narration=intro,
            bullets=(
                f"열린 이벤트 {event_count:,}건",
                f"24시간 거래량 {total_volume}",
                f"분야 {len(groups)}곳",
            ),
            visual_query="financial market overview skyline",
        )
    ]
    accents = ("gold", "blue", "red")
    total = len(groups)
    for index, group in enumerate(groups):
        key = str(group.get("key") or "")
        label = str(group.get("label") or "시장")
        count = int(group.get("event_count") or 0)
        volume = _money(group.get("volume24hr"))
        probability = group.get("probability") or {}
        strong = int(probability.get("strong") or 0)
        tight = int(probability.get("tight") or 0)
        summary = _card_summary(group, summary_budget)
        scenes.append(
            Scene(
                kind="consensus",
                title=label,
                kicker=f"SECTOR {index + 1} / {total} · 24H 거래량 순",
                body=(
                    f"이벤트 {count:,}건 · 24시간 {volume}\n"
                    f"강한 합의 {strong} · 경합 {tight}\n{summary}"
                ),
                narration=(
                    f"{label}. 이벤트 {count:,}건에 24시간 거래량 {volume}입니다. "
                    f"강한 합의 {strong}건, 경합 {tight}건입니다. {summary}"
                ),
                accent=accents[index % len(accents)],
                bullets=(
                    f"이벤트 · {count:,}건",
                    f"24시간 거래량 · {volume}",
                    f"합의 · 강한 {strong}건 / 경합 {tight}건",
                    f"무엇에 걸고 있나 · {summary}",
                ),
                visual_query=_VISUAL_QUERIES.get(key, "business strategy meeting"),
            )
        )
    scenes.append(
        Scene(
            kind="outro",
            title="숫자가 움직이면 판단도 바뀝니다",
            kicker="WHAT TO WATCH",
            body="확률의 방향 전환\n거래량이 쏠린 분야\n내일 같은 기준으로 재점검",
            narration=outro,
            accent="blue",
            bullets=("확률의 방향 전환", "거래량이 쏠린 분야", "내일 같은 기준으로 재점검"),
            visual_query="financial data review desk",
        )
    )
    return Scenario(
        date=production_date.isoformat(),
        generation_id=snapshot.generation_id,
        source_written_at=str(snapshot.brief.get("written_at") or ""),
        scenes=tuple(scenes),
    )
