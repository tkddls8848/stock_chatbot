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
    metric: str = ""
    metric_label: str = ""
    takeaway: str = ""
    volume_share: float = 0.0
    source_note: str = ""


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


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|(?<=[.!?])(?=[가-힣A-Z])", text.strip()) if part.strip()]


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
    """최신 단락에서 고유명사·수치가 있는 완결된 문장을 우선한다."""
    if group.get("stale") or group.get("status") != "ok":
        return "최신 요약을 준비하지 못했습니다. 거래 규모만 확인합니다."
    sentences = _sentences(str(group.get("paragraph") or group.get("overview") or ""))
    # A whole, specific sentence beats a truncated generic overview. Preserve decimal values.
    sentences = [s for s in sentences if len(s) <= max(150, limit) and not s.endswith("…")]
    if not sentences:
        return "구체적인 요약을 준비하지 못했습니다."
    def score(text: str) -> int:
        concrete = bool(re.search(r"\d+(?:\.\d+)?%", text))
        named = sum(word in text for word in ("연준", "OPEC", "Hormuz", "호르무즈", "중국", "미국", "OpenAI", "Anthropic"))
        generic = text.startswith(("전체적으로", "이러한", "주요 이슈 중"))
        return 4 * concrete + 2 * named + 2 * bool(re.search(r"\d+월", text)) - 3 * generic
    text = max(sentences, key=score)
    text = text.replace("Strait of Hormuz", "호르무즈 해협")
    return _end_sentence(to_polite_text(text))


# ── 어조 ────────────────────────────────────────────────
# 웹 단락은 **해라체**다(`web/prompts/polymarket_brief_ko.txt`가 그렇게 지시한다).
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
    """Question → evidence → interpretation; keep every sector and its source status."""
    groups = _sector_cards(snapshot.brief, max_groups)
    if not groups:
        raise ValueError("영상에 사용할 분야 카드가 없습니다")
    total_volume = sum(float(g.get("volume24hr") or 0) for g in groups)
    lead = groups[0]
    lead_label = str(lead["label"])
    lead_volume = _money(lead.get("volume24hr"))
    share = float(lead.get("volume24hr") or 0) / total_volume if total_volume else 0
    share_text = f"{share:.0%}"
    total_events = sum(int(g.get("event_count") or 0) for g in groups)
    event_share = int(lead.get("event_count") or 0) / total_events if total_events else 0
    intro = (
        f"거래가 많으면, 전망도 확실할까요? "
        f"{lead_label}는 오늘 다루는 분야 질문의 {event_share:.0%}인데, 거래는 {share_text}입니다."
    )
    scenes = [Scene(
        kind="intro", title="돈이 몰리면\n정답일까요?",
        kicker=f"MARKET BRIEF / {production_date:%m.%d}",
        body=f"{lead_label}에 집중된 거래", narration=intro,
        bullets=(f"24시간 거래량 · {lead_volume}", f"거래 비중 · {share_text}"),
        metric=share_text, metric_label=f"선정 {len(groups)}개 분야 중 {lead_label} 거래 비중",
        takeaway=f"질문 비중 {event_share:.0%} / 거래 비중 {share_text}\n관심이 한쪽으로 쏠렸습니다" if share > event_share else "거래 규모와 예측 확률은 다릅니다",
        volume_share=share, source_note="Polymarket / 24시간 거래량",
    )]
    for index, group in enumerate(groups):
        label = str(group["label"])
        key = str(group.get("key") or "")
        count = int(group.get("event_count") or 0)
        volume = _money(group.get("volume24hr"))
        summary = _card_summary(group, max(90, target_chars // max(1, len(groups))))
        valid = group.get("status") == "ok" and not group.get("stale")
        strong = int((group.get("probability") or {}).get("strong") or 0)
        tight = int((group.get("probability") or {}).get("tight") or 0)
        if index == 0 and valid and tight:
            summary = f"하지만 {count}개 질문 중 {tight}개는 경합입니다. " + summary
        # Domain-specific questions guide attention without asserting a causal forecast.
        watch = {
            "macro": "금리의 방향보다\n결정 시점을 확인",
            "geopolitics": "외교·분쟁 전망은\n최신성부터 확인",
            "composite": "해상 운송과 원가,\n무엇이 바뀔까?",
            "equities": "시장 전체와\n개별 기업은 다릅니다",
            "general": "서로 다른 질문을\n하나로 묶지 않기",
        }.get(key, "질문별 조건을 확인하세요")
        if not valid:
            watch = "요약 갱신 대기\n방향 판단은 보류"
        scenes.append(Scene(
            kind="consensus", title=label,
            kicker=f"{index + 1:02d} / MONEY & MEANING",
            body=summary, narration=f"{label}. {summary}",
            accent=("gold", "blue", "red")[index % 3],
            bullets=(
                f"이벤트 · {count:,}건",
                f"24시간 거래량 · {volume}",
                f"합의 · 강한 {strong}건 / 경합 {tight}건",
                f"무엇에 걸고 있나 · {summary}",
            ),
            visual_query=_VISUAL_QUERIES.get(key, "business strategy meeting"),
            metric=volume.replace("달러", ""), metric_label="24시간 거래량 / USD",
            takeaway=watch, volume_share=float(group.get("volume24hr") or 0) / total_volume if total_volume else 0,
            source_note="최신 요약" if valid else "요약 갱신 대기 / 수치만 표시",
        ))
    scenes.append(Scene(
        kind="outro", title="돈의 크기보다\n질문의 조건",
        kicker="ONE THING TO REMEMBER",
        body="거래량은 관심의 크기입니다. 확률은 질문별 가격입니다.",
        narration="거래량은 관심의 크기이지 정답의 보증이 아닙니다. "
                  "확률을 볼 땐 질문과 마감일을 함께 확인하세요. 투자 조언은 아닙니다.",
        accent="blue", bullets=("질문 · 무엇이 일어나야 하나", "기한 · 언제까지인가"),
        metric="조건", metric_label="확률을 읽기 전에",
        takeaway="질문 → 마감일 → 확률", source_note="예측시장 가격 / 사실 확정 아님",
    ))
    return Scenario(
        date=production_date.isoformat(), generation_id=snapshot.generation_id,
        source_written_at=str(snapshot.brief.get("written_at") or ""),
        scenes=tuple(scenes), lead_label=lead_label, lead_volume=lead_volume,
    )
