"""화면이 아니라 귀를 위한 한국어. 모델 없이 규칙만으로 동작한다.

**확률은 화면과 같은 퍼센트로 짧게 말한다**("참여자의 64.5%는 …것으로 봅니다"). 운영자
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

# 장면을 잇는 말은 다음 이슈의 테마(분야)를 알린다(운영자 결정 2026-09-28). 예전의
# "이번엔 분위기가 좀 다릅니다" 같은 말은 무엇이 다른지 알려 주지 않았다.
# 고지문은 마무리에서 한 번만 말한다. 장면마다 "…확인하세요"를 붙이면 같은
# 당부가 네댓 번 반복돼 아무도 듣지 않는다 — 장면별 확인점은 화면에 남긴다.
# 마무리는 고정 멘트다(운영자 결정 2026-09-27). 주소는 소리로 "눈치 닷 라이브"라고
# 말한다 — "nunchi.live"를 그대로 두면 TTS가 영문 철자로 읽는다. 화면에는 주소를 적는다.
CLOSING_LINE = (
    "여기 숫자는 사람들의 전망일 뿐, 정해진 결과도 투자 조언도 아닙니다. "
    "질문마다 조건이 다르니, 자세한 내용은 눈치 닷 라이브에 방문하여 확인해 보세요."
)
CLOSING_SCREEN = "질문마다 조건이 다릅니다.\n자세한 내용은 nunchi.live에서 확인해 보세요."

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


def _with_direction(word: str) -> str:
    """방향격 조사. 받침이 없거나 ㄹ 받침이면 '로', 그 밖의 받침이면 '으로'."""
    last = word.strip()[-1:]
    if not last or not _is_hangul(last):
        return f"{word}으로"
    jong = (ord(last) - _HANGUL_BASE) % _JONGSEONG
    return f"{word}로" if jong in (0, 8) else f"{word}으로"


def speak_markets(event_type: str, topic: str, rows: Sequence[Sequence]) -> str:
    """선택지 확률을 "참여자의 N%는 ⟨전망⟩ 것으로 봅니다"로 말한다(운영자 결정 2026-10-02).

    예전 "…를 선택한 사람은 전체의 N%"는 무엇을 보는지가 아니라 무엇을 눌렀는지만
    말했다. 운영자 지시로 주제에 맞는 동사로 전망을 말한다.
    `rows`는 (선택지 이름, '예' 퍼센트, '아니오' 퍼센트[, 전망 구절])이고 숫자는 화면과 같다.
    전망 구절은 모델이 쓴 관형형("휴전이 10월 31일까지 이어질")이다. 없으면 라벨로
    "…쪽으로 봅니다"를 만든다 — 음성이 모델에 묶이지 않게.
    - 양자택일: "참여자의 X%는 ⟨전망⟩ 것으로, Y%는 그렇지 않을 것으로 봅니다."
    - 여러 선택지: "참여자의 X%는 ⟨A⟩ 것으로, Y%는 ⟨B⟩ 것으로 봅니다."
      한 가지만 고르는 질문(exclusive)은 앞에 "주제에 대해"를 붙인다.
    """
    if not rows:
        return ""

    def clause(row) -> str:
        label, yes = row[0], row[1]
        outlook = row[3] if len(row) > 3 else None
        return f"{yes}는 {outlook} 것으로" if outlook else f"{yes}는 {_with_direction(label + ' 쪽')}"

    if event_type == "binary" or len(rows) == 1 and event_type not in {"exclusive_multi", "independent_multi"}:
        row = rows[0]
        no = row[2]
        if len(row) > 3 and row[3]:
            return f"참여자의 {clause(row)}, {no}는 그렇지 않을 것으로 봅니다."
        return f"{row[0]}에 대해 참여자의 {row[1]}는 그렇다고, {no}는 그렇지 않다고 봅니다."
    spoken = "참여자의 " + ", ".join(clause(row) for row in rows) + " 봅니다."
    if event_type == "exclusive_multi" and topic:
        return f"{topic}에 대해 {spoken}"
    return spoken


def transition(index: int, theme: str = "") -> str:
    """이슈 사이를 잇는 말. 첫 이슈는 도입에 바로 이어지므로 비어 있다."""
    if index <= 0:
        return ""
    subject = f"{theme} 테마의 " if theme else ""
    return f"다음은 {subject}주요 컨센서스 현황을 살펴봅니다."


def to_spoken_question(question: str) -> str:
    """글로 쓴 의문형을 말로 묻는 형태로 바꾼다. 모르는 어미는 그대로 둔다."""
    body = question.rstrip().rstrip("?").rstrip()
    for stiff, spoken in _STIFF_QUESTIONS:
        if body.endswith(stiff):
            return f"{body[:-len(stiff)].rstrip()}{spoken}?"
    return end_sentence(question)


def opening_line(count: int, as_of=None) -> str:
    """도입. 자료 기준 날짜와 함께 오늘의 이슈를 골랐다고 말하고, 볼 분량을 알린다.

    날짜를 말해야 며칠 뒤 본 사람도 숫자가 언제 것인지 안다(운영자 결정 2026-09-27).
    문장은 운영자가 정했다(2026-09-28). 예전에는 첫 제목을 그대로 읽어 "누구인가?."처럼
    문장부호가 겹쳤다.
    """
    subject = "질문 하나를" if count == 1 else f"질문 {count}개를"
    day = f"{as_of.year}년 {as_of.month}월 {as_of.day}일" if as_of else "오늘의"
    return (f"{day} 시장 컨센서스 이슈를 선정하였습니다. "
            f"오늘은 {subject} 숫자와 함께 짚어 보겠습니다.")
