"""우리가 모은 시장 뉴스에서 선정 이슈와 맞닿고 지금 많이 다뤄지는 기사를 고른다. 모델 없이 규칙만 쓴다.

원고가 컨센서스 숫자만 읽으면 기계적으로 들린다(운영자 지적 2026-10-08). 그날 실제로 오간 보도를
한 문장 섞으면 "왜 지금 이 질문인가"가 들린다. 여기서는 후보만 좁히고, 같은 사건인지 최종 판단과
문장은 원고 모델(`highlights.write_issues`의 `news_hook`)이 한다.

- **관련도는 주체어로 건다.** 선정 모델이 이슈마다 한국 언론이 그 주체를 부르는 말(`keywords`: 유가·원유,
  연준·FOMC)을 내고, 그 말이 기사 제목이나 원문에 있어야 후보가 된다. 영문 제목과 번역 제목의 낱말
  겹침으로 고르면 "all time high"가 원유 질문에 주가 신기록 기사를 붙이고 "Gold"가 일본 매체 이름
  (THE GOLD ONLINE)을 집었다(2026-10-08 실측) — 정작 "후티 위협으로 유가 상승"은 놓쳤다.
- **주체어가 제목의 앞 마디에 있으면 그 기사의 주제다.** "S&P 500, 나스닥 신고점... 국제유가 하락" 같은 장 시황은
  유가를 곁가지로 나열할 뿐이고, 같은 사흘에 그런 시황이 여러 건이다. 앞 마디 > 제목 > 원문 순으로 세고, 같으면
  최근 기사가 먼저다.
- **화제성은 동점만 가른다.** 같은 묶음에서 드문 낱말을 둘 이상 공유하는 다른 기사 수(`coverage`)는 여러 매체가
  함께 다룬 정도이고 모델에도 넘긴다. 점수에 크게 섞으면 매일 비슷한 문장으로 반복되는 장 시황이 위로 올라왔다
  (2026-10-08 실측: 원유 질문에 "후티 위협으로 유가 상승"보다 지수 시황 네 건이 먼저였다). 주체어가 맞지 않는
  기사를 화제라고 넣지 않는다.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

MAX_PER_ISSUE = 6
# 두 기사를 "같은 흐름"으로 셀 때 공유해야 하는 드문 낱말 수.
_COVERAGE_SHARED = 2
_WHEN = {0: "오늘", 1: "어제", 2: "그제"}
_PARTICLE = re.compile(r"(으로|에서|에게|까지|부터|처럼|보다|이며|이고|와|과|은|는|이|가|을|를|의|에|도|로|만)$")


def title_words(text: str) -> set[str]:
    """제목의 낱말(한국어는 끝 조사를 뗀다). 화제성 비교와 원고 문장·기사 대조(`highlights._news_hook`)가 쓴다."""
    words = set()
    for raw in re.findall(r"[가-힣]{2,}|[A-Za-z][A-Za-z0-9&]{2,}|[一-龥ぁ-んァ-ヶ]{2,}", text):
        word = _PARTICLE.sub("", raw) if re.match(r"[가-힣]", raw) else raw.lower()
        if len(word) >= 2:
            words.add(word)
    return words


def _age(published_at: str, reference: datetime) -> int | None:
    """며칠 전 기사인가(기준 시각의 시간대로 센 날짜). 사흘 밖·미래·시각 없음은 None — 시의가 아니다."""
    try:
        stamp = datetime.fromisoformat(published_at)
        if stamp.tzinfo is None or stamp > reference:
            return None
        days = (reference.date() - stamp.astimezone(reference.tzinfo).date()).days
    except (TypeError, ValueError):
        return None
    return days if days in _WHEN else None


def mentions(keyword: str, text: str) -> bool:
    """주체어가 글에 있는가. 기사 후보 거르기와 원고 문장 검사(`highlights._news_hook`)가 같은 판정을 쓴다."""
    # 영문 주체어(FOMC·ECB·OPEC)는 낱말 경계로 본다 — "Fed"가 "Federation"에 붙지 않게.
    if keyword.isascii():
        return re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE) is not None
    return keyword.replace(" ", "") in text.replace(" ", "")


def _lead(title: str) -> str:
    """제목의 앞 마디. 쉼표·말줄임·가운뎃점·쌍점 앞까지다."""
    return re.split(r"\.\.\.|…|,|·|:|，|、", title, maxsplit=1)[0]


def related_news(issue: dict[str, Any], pool: list[dict[str, Any]], reference: datetime) -> list[dict[str, Any]]:
    """이슈 하나에 붙일 시의 기사 후보. 주체어가 맞은 기사만, 맞은 자리·최신·화제성 순으로 최대 `MAX_PER_ISSUE`건.

    돌려주는 기사 id는 `market:N`이다 — 모델에 긴 수집 id를 베끼게 하지 않는다.
    """
    keywords = [word for word in (issue.get("selection") or {}).get("keywords") or [] if isinstance(word, str)]
    if not keywords:
        return []
    rows, seen = [], set()
    for row in pool:
        title = " ".join(str(row.get("title") or "").split())
        key = re.sub(r"\W+", "", title).casefold()
        days = _age(str(row.get("published_at") or ""), reference)
        # 같은 제목이 여러 시장 칸에 겹쳐 실린다(실측 2026-10-08).
        if not title or key in seen or days is None:
            continue
        seen.add(key)
        rows.append({"row": row, "title": title, "days": days, "original": str(row.get("text") or ""),
                     "words": title_words(title)})
    frequency: dict[str, int] = {}
    for item in rows:
        for word in item["words"]:
            frequency[word] = frequency.get(word, 0) + 1
    rare_limit = max(3, len(rows) // 20)
    scored = []
    for item in rows:
        hits = sum(3 if mentions(word, _lead(item["title"])) else 2 if mentions(word, item["title"])
                   else 1 if mentions(word, item["original"]) else 0 for word in keywords)
        if not hits:
            continue
        own = {word for word in item["words"] if frequency[word] <= rare_limit}
        coverage = sum(1 for other in rows if other is not item and len(own & other["words"]) >= _COVERAGE_SHARED)
        scored.append((hits, -item["days"], coverage, item["row"].get("published_at") or "", item))
    scored.sort(key=lambda entry: entry[:4], reverse=True)
    return [{
        "id": f"market:{index}", "title": item["title"], "original": item["original"][:300],
        "when": _WHEN[item["days"]], "coverage": coverage, "market": item["row"].get("market"),
        "source": item["row"].get("source"), "url": item["row"].get("url") or "",
        "published_at": item["row"].get("published_at"),
    } for index, (_, _, coverage, _, item) in enumerate(scored[:MAX_PER_ISSUE], 1)]
