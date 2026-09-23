"""Polymarket raw tag를 한 개의 대표 분야와 보조 축으로 분류한다."""

from __future__ import annotations

from typing import Any

TAXONOMY_VERSION = "2026-08-30.1"
NAMED_CATEGORY_TARGET = 0.90

CATEGORY_LABELS = {
    "politics": "정치·선거",
    "geopolitics": "지정학·국제",
    "economy_finance": "경제·금융",
    "crypto": "크립토",
    "technology_ai": "기술·AI",
    "business": "기업·비즈니스",
    "sports": "스포츠",
    "culture": "문화·엔터테인먼트",
    "science_health": "과학·건강",
    "weather_climate": "날씨·기후",
    "law_regulation": "법률·규제",
    "other": "기타·미분류",
}

# G0(2026-08-30, 열린 event 22,047건) 상위 raw tag를 모두 검토해 고정했다.
# 세부 리그·코인명은 대표 분야의 명백한 자식이므로 exact allowlist로만 확장한다.
CATEGORY_TAGS: dict[str, set[str]] = {
    "politics": {
        "politics", "elections", "us-presidential-election", "midterms",
        "house-elections", "global-elections", "world-elections", "main-election",
        "international-election-props", "nov-4-elections", "trump", "midtermmov",
    },
    "geopolitics": {
        "geopolitics", "world", "middle-east", "iran", "russia", "ukraine",
        "china", "israel", "war", "foreign-policy",
    },
    "economy_finance": {
        "economy", "finance", "fed", "fed-rates", "macro-indicators", "inflation",
        "interest-rates", "equities", "stocks", "pre-market",
    },
    "crypto": {
        "crypto", "crypto-prices", "bitcoin", "ethereum", "solana", "xrp", "ripple",
        "bnb", "dogecoin", "zcash", "hype", "up-or-down", "5m", "15m", "1h",
    },
    "technology_ai": {"tech", "ai", "big-tech", "technology", "artificial-intelligence"},
    "business": {"business", "companies", "deals", "mergers", "earnings"},
    "sports": {
        "sports", "games", "soccer", "football", "nfl", "mlb", "baseball", "tennis",
        "table-tennis", "cricket", "esports", "counter-strike-2", "league-of-legends",
        "formula-1", "fa-cup", "efl-championship", "mls", "cfb", "epl", "ucl",
        "premier-league", "bundesliga", "bundesliga-2", "la-liga", "la-liga-2",
        "ligue-1", "ligue-2", "serie-b", "national-league", "international-cricket",
        "season-stats", "props", "margin-of-victory", "setka", "setkameua",
        "japan-j-league", "japan-j2-league", "k-league", "brazil-serie-a",
        "saudi-professional-league", "scottish-premiership", "primeira-liga",
    },
    "culture": {"culture", "pop-culture", "awards", "movies", "music", "entertainment"},
    "science_health": {"science", "space", "health", "medicine", "public-health"},
    "weather_climate": {
        "weather", "climate", "temperature", "daily-temperature", "highest-temperature",
        "lowest-temperature",
    },
    "law_regulation": {"legal", "law", "courts", "regulation", "supreme-court"},
}

REGION_TAGS = {
    "us", "usa", "united-states", "brazil", "japan", "korea", "china", "iran",
    "russia", "ukraine", "europe", "middle-east", "mex", "argpn", "sea", "rus",
}
SYSTEM_TAGS = {"hide-from-new", "recurring", "featured", "rewards", "closed"}


def _slug(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def extract_tags(event: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in event.get("tags") or []:
        if not isinstance(raw, dict):
            continue
        slug = _slug(raw.get("slug") or raw.get("label"))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        result.append({"slug": slug, "label": str(raw.get("label") or slug)})
    return result


def classify(tags: list[dict[str, str]]) -> dict[str, Any]:
    slugs = {tag["slug"] for tag in tags}
    candidates = [key for key, allowed in CATEGORY_TAGS.items() if slugs & allowed]
    if len(candidates) == 1:
        category = candidates[0]
        reason = "tag:" + sorted(slugs & CATEGORY_TAGS[category])[0]
    elif len(candidates) > 1:
        category = "other"
        reason = "ambiguous:" + ",".join(sorted(candidates))
    else:
        category = "other"
        reason = "unmapped"
    return {
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "category_reason": reason,
        "tags": sorted(slugs - REGION_TAGS - SYSTEM_TAGS),
        "regions": sorted(slugs & REGION_TAGS),
        "system_tags": sorted(slugs & SYSTEM_TAGS),
    }


# ── 섹터 줄글 브리프의 감시 태그 ───────────────────────────────────────────
# 이 목록은 `classify()`가 매기는 category와 **독립이다.** classify는 둘 이상
# 분야에 걸린 event를 `other`로 보내는데, 지정학과 경제가 동시에 걸린 event가
# 바로 브리프가 보려는 것이라 그 경로로는 잡히지 않는다. 그래서 브리프는
# category를 보지 않고 tags를 직접 본다 — detail seek도 필요 없고 classify를
# 건드리지 않아 대시보드의 다른 분야 숫자도 움직이지 않는다.
#
# 국가 태그(iran·russia·china·middle-east)는 넣지 않는다. 축구 리그와 선거까지
# 끌고 들어와 노이즈가 크다. 지정학을 뜻하는 태그만 본다.
BRIEF_ECON_TAGS = frozenset(
    {"economy", "finance", "fed", "fed-rates", "interest-rates", "inflation",
     "equities", "stocks", "pre-market", "macro-indicators"}
)
BRIEF_GEO_TAGS = frozenset({"geopolitics", "foreign-policy"})

# 그룹은 **순서가 곧 우선순위다.** 태그를 여럿 단 event는 첫 일치 그룹에 넣는다.
# 매번 같은 결과가 나와야 "지난번과 뭐가 달라졌나"를 비교할 수 있다.
#
# 금리·물가를 따로 두지 않는 이유: 2026-09-01 실측에서 fed·fed-rates·
# interest-rates·inflation이 전부 39건 미만이었다. 각각 그룹으로 두면 매 주기
# "표본 부족"만 뜬다. 이 태그들이 커지면 그때 쪼갠다.
BRIEF_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "key": "equities",
        "label": "주식·시장",
        "sector": "economy_finance",
        "tags": frozenset({"equities", "stocks", "pre-market"}),
    },
    {
        "key": "macro",
        "label": "거시·통화",
        "sector": "economy_finance",
        "tags": frozenset(
            {"macro-indicators", "fed", "fed-rates", "interest-rates", "inflation"}
        ),
    },
    {
        "key": "general",
        "label": "기타 경제·금융",
        "sector": "economy_finance",
        "tags": frozenset({"economy", "finance"}),
    },
)
COMPOSITE_GROUP = {
    "key": "composite",
    "label": "복합(경제·지정학)",
    "sector": "composite",
}
GEOPOLITICS_GROUP = {
    "key": "geopolitics",
    "label": "지정학",
    "sector": "geopolitics",
}


def assign_brief_group(tags: list[str]) -> dict[str, Any] | None:
    """event의 tags로 브리프 그룹을 정한다. 감시 대상이 아니면 None.

    섹터는 서로 겹치지 않는다 — event 하나는 정확히 한 그룹에만 들어간다.
    """
    slugs = set(tags)
    has_econ = bool(slugs & BRIEF_ECON_TAGS)
    has_geo = bool(slugs & BRIEF_GEO_TAGS)
    if has_econ and has_geo:
        return COMPOSITE_GROUP
    if has_geo:
        return GEOPOLITICS_GROUP
    if not has_econ:
        return None
    for group in BRIEF_GROUPS:
        if slugs & group["tags"]:
            return group
    return None


def brief_groups() -> tuple[dict[str, Any], ...]:
    """화면에 그릴 순서. 복합을 먼저 둔다 — 교차 event가 가장 읽을 값어치가 있다."""
    return (COMPOSITE_GROUP, *BRIEF_GROUPS, GEOPOLITICS_GROUP)
