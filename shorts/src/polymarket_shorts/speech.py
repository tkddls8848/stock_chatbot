"""화면이 아니라 귀를 위한 한국어. 모델 없이 규칙만으로 동작한다.

**화면과 음성의 역할을 나눈다.** 확률의 정확한 값은 화면에 남기고(`99.95%`),
소리로는 듣는 사람 기준으로 푼다("사실상 굳어진 분위기입니다"). 2026-09-23
산출물의 내레이션이 "9월 WTI 90달러 이하: 예 99.95%, 아니오 0.05%"를 그대로
낭독했는데, 소수점 둘째 자리와 예·아니오 쌍은 눈으로 읽는 표의 문법이지
말의 문법이 아니다. 귀로는 어느 쪽이 얼마나 유력한지만 남는다.

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

# 확률을 말로 옮기는 눈금. 각 줄은 (하한, 그 구간의 말)이고 위에서부터 찾는다.
# 구간 폭은 사람이 실제로 쓰는 분수에 맞췄다 — 0.75는 "넷 중 셋", 0.2는
# "다섯 번에 한 번"이다. 그래서 경계가 일정 간격이 아니다.
_ODDS: tuple[tuple[float, str], ...] = (
    (.965, "사실상 굳어진 분위기입니다"),
    (.895, "열에 아홉은 그렇게 봅니다"),
    (.830, "열에 여덟쯤으로 봅니다"),
    (.720, "넷 중 셋은 그렇게 봅니다"),
    (.640, "셋 중 둘꼴로 봅니다"),
    (.580, "다섯에 셋 정도로 봅니다"),
    (.520, "반반에서 조금 기운 정도입니다"),
    (.480, "거의 반반입니다"),
    (.420, "반반에 조금 못 미칩니다"),
    (.360, "다섯에 둘쯤으로 봅니다"),
    (.280, "셋에 하나꼴로 봅니다"),
    (.170, "다섯 번에 한 번꼴로 봅니다"),
    (.105, "여덟 번에 한 번꼴로 봅니다"),
    (.075, "열 번에 한 번꼴로 봅니다"),
    (.035, "스무 번에 한 번꼴로 봅니다"),
    (.000, "사실상 없다고 봅니다"),
)
# 49.5 대 50.5처럼 눈금 하나 차이는 기울었다고 말하지 않는다.
_EVEN_BAND = .01

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


def odds_phrase(probability: float) -> str:
    """확률 하나를 듣는 사람 기준의 말로 옮긴다."""
    if abs(probability - .5) <= _EVEN_BAND:
        return "정확히 반반입니다"
    for floor, phrase in _ODDS:
        if probability >= floor:
            return phrase
    return _ODDS[-1][1]


def _with(word: str) -> str:
    """받침에 맞는 접속 조사. 한글이 아니면 모음 뒤로 본다."""
    last = word.strip()[-1:]
    if not last or not _is_hangul(last):
        return "와"
    return "과" if (ord(last) - _HANGUL_BASE) % _JONGSEONG else "와"


def speak_markets(rows: Sequence[tuple[str, float]]) -> str:
    """개별 선택지의 확률을 말로 푼다. 숫자는 한 개도 발음하지 않는다.

    두 선택지의 말이 같으면 한 문장으로 합친다 — 49.5 대 49.5를 따로 읽으면
    같은 문장을 두 번 듣게 된다.
    """
    if not rows:
        return ""
    phrases = [(label, odds_phrase(probability)) for label, probability in rows]
    if len(phrases) == 2 and phrases[0][1] == phrases[1][1]:
        first, second = phrases[0][0], phrases[1][0]
        return f"{first}{_with(first)} {second}, 둘 다 {phrases[0][1]}."
    spoken = [f"{phrases[0][0]} 쪽은 {phrases[0][1]}."]
    for index in range(1, len(phrases)):
        label, phrase = phrases[index]
        link = "반면 " if abs(rows[index][1] - rows[index - 1][1]) >= .25 else "그리고 "
        spoken.append(f"{link}{label} 쪽은 {phrase}.")
    return " ".join(spoken)


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
