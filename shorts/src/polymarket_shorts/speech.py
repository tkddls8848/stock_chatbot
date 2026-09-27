"""화면이 아니라 귀를 위한 한국어. 모델 없이 규칙만으로 동작한다.

**확률은 화면과 같은 퍼센트로 짧게 말한다**("…쪽은 64.5%입니다"). 운영자
결정(2026-09-27): "셋 중 둘꼴"·"다섯에 둘쯤" 같은 비유는 오히려 낯설고 길었다.
다만 예·아니오 쌍은 읽지 않는다 — '예' 확률 하나만 말한다(2026-09-23 산출물이
"예 99.95%, 아니오 0.05%"를 통째로 낭독해 표를 읽는 소리가 났다).

Cloudflare 무료 한도가 떨어져도 원고의 자연스러움이 같아야 하므로 이 모듈은
LLM을 부르지 않는다. 모델이 쓰는 것은 질문·해설 문장뿐이고, 확률을 말로
옮기고 어조를 맞추는 일은 전부 여기 규칙이 한다.
"""

from __future__ import annotations

import re
from typing import Sequence


_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONGSEONG = 28
_JONG_N = 4   # ㄴ
_JONG_B = 17  # ㅂ

# 장면을 잇는 말. "첫째·다음은·마지막으로"처럼 세어 나가면 목록을 읽는 소리가
# 난다. 화면에는 이미 `01 / 04` 번호가 있으니 말은 번호를 다시 세지 않는다.
_TRANSITIONS: tuple[str, ...] = (
    "",
    "이번엔 분위기가 좀 다릅니다.",
    "여기서 한 번 더 눈길이 갑니다.",
    "비슷해 보여도 결이 다릅니다.",
    "짚고 갈 게 하나 더 있습니다.",
)

# 고지문은 마무리에서 한 번만 말한다. 장면마다 "…확인하세요"를 붙이면 같은
# 당부가 네댓 번 반복돼 아무도 듣지 않는다 — 장면별 확인점은 화면에 남긴다.
CLOSING_LINE = (
    "여기 숫자는 사람들의 전망일 뿐, 정해진 결과도 투자 조언도 아닙니다. "
    "질문마다 조건이 다르니 판정 규칙은 직접 확인하세요."
)

# "얼마에 도달할 것인가?"는 글로 읽는 문장이다. 말로는 "…도달할까요?"로 묻는다.
_STIFF_QUESTIONS = (("것인가", "까요"), ("인가", "일까요"))


def _is_hangul(ch: str) -> bool:
    return _HANGUL_BASE <= ord(ch) <= _HANGUL_LAST


def _with_jongseong(syllable: str, jong: int) -> str:
    index = ord(syllable) - _HANGUL_BASE
    return chr(_HANGUL_BASE + (index // _JONGSEONG) * _JONGSEONG + jong)


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


def to_polite_text(text: str) -> str:
    """문장 단위로 나눠 각각 합쇼체로 옮긴다."""
    parts = re.split(r"(?<=[.!?])(\s+)", text)
    return "".join(
        part if index % 2 else to_polite(part) for index, part in enumerate(parts)
    )


def end_sentence(text: str) -> str:
    """문장을 마침표로 닫는다. 이미 구두점으로 끝났으면 그대로 둔다.

    문장 사이에 마침표가 없으면 TTS가 한 문장으로 읽어
    "그쳤습니다 공급망과"처럼 들린다.
    """
    body = text.rstrip()
    return body if body.endswith((".", "!", "?", "…")) else f"{body}."


def speak_markets(rows: Sequence[tuple[str, str]]) -> str:
    """개별 선택지의 '예' 확률을 화면과 같은 퍼센트로 말한다.

    `rows`는 (선택지 이름, 화면에 뜨는 퍼센트 문자열)이다. 화면과 음성이 같은 숫자를
    말해야 듣는 사람이 화면에서 바로 찾는다.
    """
    if not rows:
        return ""
    parts = [f"{label} 쪽은 {percent}" for label, percent in rows]
    return ", ".join(parts) + "입니다."


def transition(index: int) -> str:
    """이슈 사이를 잇는 말. 첫 이슈는 도입에 바로 이어지므로 비어 있다."""
    return _TRANSITIONS[index] if 0 <= index < len(_TRANSITIONS) else ""


def to_spoken_question(question: str) -> str:
    """글로 쓴 의문형을 말로 묻는 형태로 바꾼다. 모르는 어미는 그대로 둔다."""
    body = question.rstrip().rstrip("?").rstrip()
    for stiff, spoken in _STIFF_QUESTIONS:
        if body.endswith(stiff):
            return f"{body[:-len(stiff)].rstrip()}{spoken}?"
    return end_sentence(question)


def opening_line(headline: str, count: int) -> str:
    """도입. 제목을 한 번 부르고 오늘 볼 분량을 말로 알린다."""
    subject = "질문 하나를" if count == 1 else f"이런 질문 {count}개를"
    return (f"{' '.join(headline.split())}. 참여가 몰린 질문 가운데 하나입니다. "
            f"오늘은 {subject} 숫자와 함께 짚어 보겠습니다.")
